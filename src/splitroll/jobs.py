"""Background work: importing scans, exporting halves, building contact sheets."""
import logging
import math
import os
import re
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from splitroll import imaging, APP_NAME
from splitroll.library import new_frame, effective_state

log = logging.getLogger(__name__)

_jobs = {}
_jobs_lock = threading.Lock()
_preview_locks = {}
_preview_locks_lock = threading.Lock()


# ── job registry ─────────────────────────────────────────────────────────────
def new_job(kind, roll_id, total=0, msg=""):
    jid = uuid.uuid4().hex[:8]
    job = {"id": jid, "kind": kind, "rollId": roll_id, "total": total, "done": 0, "failed": 0,
           "running": True, "msg": msg, "errors": [], "result": None, "started": time.time()}
    with _jobs_lock:
        _jobs[jid] = job
    return job


def get_job(jid):
    with _jobs_lock:
        j = _jobs.get(jid)
        return dict(j) if j else None


def active_jobs():
    with _jobs_lock:
        cutoff = time.time() - 30
        return [dict(j) for j in _jobs.values() if j["running"] or j.get("ended", 0) > cutoff]


def _finish(job, msg, result=None):
    job["running"] = False
    job["msg"] = msg
    job["result"] = result
    job["ended"] = time.time()


def run_async(fn, *a):
    t = threading.Thread(target=fn, args=a, daemon=True)
    t.start()
    return t


# ── previews / cache ─────────────────────────────────────────────────────────
class Cache:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def disp(self, fid):
        return self.root / f"d_{fid}.jpg"

    def thumb(self, fid):
        return self.root / f"t_{fid}.jpg"

    def drop(self, fid):
        for p in (self.disp(fid), self.thumb(fid)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass


def _plock(fid):
    with _preview_locks_lock:
        return _preview_locks.setdefault(fid, threading.Lock())


def ensure_previews(cache, frame):
    """Make sure display + thumbnail JPEGs exist; returns True if they do."""
    d, t = cache.disp(frame["id"]), cache.thumb(frame["id"])
    if d.exists() and t.exists():
        return True
    with _plock(frame["id"]):
        if d.exists() and t.exists():
            return True
        if not os.path.exists(frame["path"]):
            return False
        pic = imaging.load(frame["path"])
        imaging.make_previews(pic, d, t)
        w, h = pic.size
        frame["w"], frame["h"], frame["bits"] = w, h, pic.bits
        return True


# ── import ───────────────────────────────────────────────────────────────────
def _natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def list_images(folder):
    folder = Path(folder)

    def scan(d):
        try:
            return [p for p in d.iterdir()
                    if p.is_file() and p.suffix.lower() in imaging.IMAGE_EXTS and not p.name.startswith(".")]
        except OSError:
            return []

    files = scan(folder)
    if not files:                       # e.g. lab delivery with a single raw/ or jpg/ subfolder
        for sub in sorted((d for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".")),
                          key=_natural_key):
            files = scan(sub)
            if files:
                break
    return sorted(files, key=_natural_key)


def _process_one(cache, path):
    f = new_frame(path, path.name, 0, 0, 8)
    pic = imaging.load(str(path))
    w, h = pic.size
    f["w"], f["h"], f["bits"] = w, h, pic.bits
    disp = imaging.make_previews(pic, cache.disp(f["id"]), cache.thumb(f["id"]))
    det = imaging.detect_cut(disp, w)
    if det:
        f["cut"], f["gap"], f["auto"] = det[0], det[1], True
    return f


def import_folder(lib, cache, roll_id, folder):
    files = list_images(folder)
    job = new_job("import", roll_id, len(files), f"Found {len(files)} scans")
    run_async(_import_worker, lib, cache, job, roll_id, files)
    return job


def _import_worker(lib, cache, job, roll_id, files):
    try:
        r = lib.roll(roll_id)
        if not r:
            return _finish(job, "Roll no longer exists")
        first_import = not r["frames"]
        known = {os.path.normcase(f["path"]) for f in r["frames"]}
        todo = [p for p in files if os.path.normcase(str(p)) not in known]
        job["done"] = len(files) - len(todo)
        added, pending = [], []
        with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 2)) as ex:
            futures = [(p, ex.submit(_process_one, cache, p)) for p in todo]
            for p, fut in futures:
                try:
                    f = fut.result()
                    pending.append(f)
                    added.append(f)
                    job["msg"] = f"{job['done'] + 1}/{job['total']} · {p.name}"
                except Exception as e:
                    job["failed"] += 1
                    job["errors"].append(f"{p.name}: {e}")
                    log.warning("import failed for %s: %s", p, e)
                job["done"] += 1
                if len(pending) >= 4:
                    lib.append_frames(roll_id, pending)
                    pending = []
        if pending:
            lib.append_frames(roll_id, pending)
        extra = ""
        r = lib.roll(roll_id)
        gaps = [f["gap"] for f in added if f.get("gap")]
        if r and first_import and not r.get("border") and len(gaps) >= max(2, len(added) // 3):
            border = int(statistics.median(gaps)) + 4
            border += border % 2
            lib.update_roll(roll_id, {"border": border})
            extra = f" · border set to {border}px"
        skipped = len(files) - len(todo)
        msg = f"Imported {len(added)}" + (f", {job['failed']} failed" if job["failed"] else "") \
              + (f", {skipped} already in roll" if skipped else "") + extra
        _finish(job, msg, {"added": len(added), "ids": [f["id"] for f in added], "border": extra != "",
                           "firstImport": first_import})
    except Exception as e:
        log.exception("import crashed")
        _finish(job, f"Import failed: {e}")


def redetect(lib, cache, roll, frame):
    if not ensure_previews(cache, frame):
        return None
    with Image.open(cache.disp(frame["id"])) as im:
        im = imaging.pil_rotate(im.convert("RGB"), frame["rot"])
        w = frame["h"] if frame["rot"] in (90, 270) else frame["w"]
        return imaging.detect_cut(im, w)


# ── export ───────────────────────────────────────────────────────────────────
EXT = {"tiff": "tif", "png": "png", "jpeg": "jpg"}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(s):
    s = _BAD.sub("_", str(s)).strip().rstrip(". ")
    return s or "roll"


def plan_export(roll, ids, fmt, naming):
    """List of (frame, part, filename) where part is 'L', 'R' or 'F'."""
    ext = EXT[fmt]
    want = set(ids) if ids is not None else None
    width = max(2, len(str(len(roll["frames"]))))
    base = safe_name(roll["name"])
    out = []
    for i, f in enumerate(roll["frames"]):
        if want is not None and f["id"] not in want:
            continue
        st = effective_state(roll, f)
        if st == "pending":
            continue
        num = str(i + 1).zfill(width)
        stem = safe_name(Path(f["name"]).stem)
        if st == "split":
            if naming == "source":
                out += [(f, "L", f"{stem}_L.{ext}"), (f, "R", f"{stem}_R.{ext}")]
            else:
                out += [(f, "L", f"{base}_{num}L.{ext}"), (f, "R", f"{base}_{num}R.{ext}")]
        else:
            out.append((f, "F", f"{stem}.{ext}" if naming == "source" else f"{base}_{num}_full.{ext}"))
    return out


def existing_conflicts(plan, out_dir):
    d = Path(out_dir)
    return [name for _, _, name in plan if (d / name).exists()]


def export(lib, roll_id, ids, out_dir, fmt, naming, conflict):
    r = lib.roll(roll_id)
    with lib.lock:
        roll = {**r, "frames": [dict(f) for f in r["frames"]]}
    plan = plan_export(roll, ids, fmt, naming)
    job = new_job("export", roll_id, len(plan), "Exporting…")
    run_async(_export_worker, job, roll, plan, out_dir, fmt, conflict)
    return job


def _export_worker(job, roll, plan, out_dir, fmt, conflict):
    out = Path(out_dir)
    written, skipped = 0, 0
    try:
        out.mkdir(parents=True, exist_ok=True)
        by_frame = {}
        for f, part, name in plan:
            by_frame.setdefault(f["id"], (f, []))[1].append((part, name))
        for f, parts in by_frame.values():
            try:
                todo = [(p, n) for p, n in parts if not (conflict == "skip" and (out / n).exists())]
                skipped += len(parts) - len(todo)
                job["done"] += len(parts) - len(todo)
                if not todo:
                    continue
                pic = imaging.load(f["path"])
                arr = imaging.rotate(pic.arr, f["rot"])
                width = arr.shape[1]
                le, rs = imaging.split_bounds(width, f["cut"], roll.get("border", 0))
                for part, name in todo:
                    job["msg"] = name
                    if part == "L":
                        a = imaging.rotate(arr[:, :le], f["lRot"])
                    elif part == "R":
                        a = imaging.rotate(arr[:, rs:], f["rRot"])
                    else:
                        a = arr
                    imaging.save(a, out / name, fmt, icc=pic.icc, dpi=pic.dpi)
                    written += 1
                    job["done"] += 1
            except Exception as e:
                job["failed"] += 1
                job["done"] += 1
                job["errors"].append(f"{f['name']}: {e}")
                log.exception("export failed for %s", f["path"])
        msg = f"Exported {written} file{'s' if written != 1 else ''}"
        if skipped:
            msg += f", {skipped} skipped"
        if job["failed"]:
            msg += f", {job['failed']} failed"
        _finish(job, msg, {"dir": str(out), "written": written})
    except Exception as e:
        log.exception("export crashed")
        _finish(job, f"Export failed: {e}", {"dir": str(out), "written": written})


# ── contact sheet ────────────────────────────────────────────────────────────
def _font(size, bold=False):
    cands = []
    if sys.platform == "win32":
        fd = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        cands += [fd / ("consolab.ttf" if bold else "consola.ttf"), fd / "cour.ttf"]
    elif sys.platform == "darwin":
        cands += [Path("/System/Library/Fonts/Menlo.ttc"), Path("/System/Library/Fonts/Monaco.ttf")]
    cands += [Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else
                   "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")]
    for p in cands:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def contact_sheet(lib, cache, roll_id, out_dir):
    r = lib.roll(roll_id)
    with lib.lock:
        roll = {**r, "frames": [dict(f) for f in r["frames"]]}
    job = new_job("sheet", roll_id, len(roll["frames"]), "Building contact sheet…")
    run_async(_sheet_worker, cache, job, roll, out_dir)
    return job


def _sheet_worker(cache, job, roll, out_dir):
    try:
        frames = roll["frames"]
        n = len(frames)
        cols = min(6, max(1, n))
        rows = math.ceil(n / cols)
        TW, TH, PAD, HEAD, STRIP = 600, 400, 24, 96, 40
        W = cols * TW + (cols + 1) * PAD
        H = HEAD + rows * (TH + STRIP + PAD) + PAD + 30
        sheet = Image.new("RGB", (W, H), (22, 22, 20))
        d = ImageDraw.Draw(sheet)
        ft, fs, fn, fb = _font(32, True), _font(20), _font(18), _font(14, True)
        d.text((PAD, 18), roll["name"], font=ft, fill=(232, 160, 48))
        sub = " · ".join(x for x in (roll.get("film", ""), roll.get("date", ""), f"{n} frames") if x)
        d.text((PAD, 60), sub, font=fs, fill=(150, 150, 140))
        border = roll.get("border", 0)
        for i, f in enumerate(frames):
            col, row = i % cols, i // cols
            x = PAD + col * (TW + PAD)
            y = HEAD + row * (TH + STRIP + PAD)
            d.rectangle([x, y, x + TW - 1, y + TH - 1], fill=(12, 12, 10))
            st = effective_state(roll, f)
            try:
                if ensure_previews(cache, f):
                    with Image.open(cache.disp(f["id"])) as im:
                        im = imaging.pil_rotate(im.convert("RGB"), f["rot"])
                        sc = min(TW / im.width, TH / im.height)
                        dw, dh = max(1, int(im.width * sc)), max(1, int(im.height * sc))
                        im = im.resize((dw, dh), Image.Resampling.LANCZOS)
                        ox, oy = x + (TW - dw) // 2, y + (TH - dh) // 2
                        sheet.paste(im, (ox, oy))
                        if st == "split":
                            srcw = f["h"] if f["rot"] in (90, 270) else f["w"]
                            le, rs = imaging.split_bounds(srcw, f["cut"], border)
                            lx, rx = ox + le * dw / srcw, ox + rs * dw / srcw
                            if rx - lx >= 2:
                                ov = Image.new("RGBA", (max(1, int(rx - lx)), dh), (0, 0, 0, 110))
                                sheet.paste(ov, (int(lx), oy), ov)
                            cx = int(round((lx + rx) / 2))
                            d.line([cx, oy, cx, oy + dh - 1], fill=(232, 160, 48), width=2)
                else:
                    d.text((x + TW // 2, y + TH // 2), "missing", font=fn, fill=(192, 112, 64), anchor="mm")
            except Exception as e:
                log.warning("sheet cell %s failed: %s", f["name"], e)
            d.rectangle([x, y + TH, x + TW - 1, y + TH + STRIP - 1], fill=(36, 36, 34))
            d.text((x + 10, y + TH + STRIP // 2), str(i + 1).zfill(2), font=fn, fill=(150, 150, 140), anchor="lm")
            label = {"split": "SPLIT", "full": "FULL", "pending": ""}[st]
            if label:
                col_ = (58, 106, 74) if st == "split" else (64, 80, 112)
                d.rectangle([x + TW - 84, y + TH + 8, x + TW - 10, y + TH + STRIP - 8], fill=col_)
                d.text((x + TW - 47, y + TH + STRIP // 2), label, font=fb, fill=(255, 255, 255), anchor="mm")
            job["done"] = i + 1
        d.text((W - PAD, H - 18), APP_NAME, font=fn, fill=(80, 80, 76), anchor="rm")
        out = Path(out_dir)
        base = safe_name(roll["name"]) + "_contact"
        path = out / f"{base}.png"
        k = 2
        while path.exists():
            path = out / f"{base}-{k}.png"
            k += 1
        sheet.save(path, "PNG", optimize=False, compress_level=6)
        _finish(job, f"Saved {path.name}", {"dir": str(out), "path": str(path)})
    except Exception as e:
        log.exception("contact sheet failed")
        _finish(job, f"Contact sheet failed: {e}")


# ── relink ───────────────────────────────────────────────────────────────────
def relink(lib, roll_id, folder):
    index = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for fn in files:
            if not fn.startswith("."):
                index.setdefault(fn.lower(), os.path.join(root, fn))
    relinked = missing = 0
    with lib.lock:
        r = lib.roll(roll_id)
        if not r:
            return 0, 0
        for f in r["frames"]:
            if os.path.exists(f["path"]):
                continue
            cand = index.get(f["name"].lower())
            if cand:
                f["path"] = cand
                relinked += 1
            else:
                missing += 1
    if relinked:
        lib.touch()
    return relinked, missing
