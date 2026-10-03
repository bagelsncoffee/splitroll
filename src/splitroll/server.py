"""Local HTTP API + UI for the app window (bound to 127.0.0.1 and token-protected)."""
import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, Response
from PIL import Image

from splitroll import APP_NAME, VERSION, imaging, jobs
from splitroll.library import load_any

log = logging.getLogger(__name__)
UI_DIR = Path(__file__).resolve().parent / "ui"


def reveal(path):
    p = str(path)
    try:
        if sys.platform == "win32":
            if os.path.isfile(p):
                subprocess.Popen(["explorer", "/select,", p])
            else:
                os.startfile(p)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", p] if os.path.isfile(p) else ["open", p])
        else:
            subprocess.Popen(["xdg-open", p if os.path.isdir(p) else os.path.dirname(p)])
    except Exception as e:
        log.warning("reveal failed: %s", e)


def create_app(lib, cache, dialogs):
    app = Flask(__name__, static_folder=None)
    app.config["TOKEN"] = token = secrets.token_urlsafe(16)

    @app.before_request
    def _auth():
        if request.path.startswith("/ui/") or request.path == "/favicon.ico":
            return
        t = request.headers.get("X-Token") or request.args.get("t")
        if t != token:
            abort(403)

    # ── UI ──────────────────────────────────────────────────────────────────
    @app.route("/")
    def index():
        html = (UI_DIR / "index.html").read_text("utf-8")
        boot = f'<script>window.SR={{token:"{token}",version:"{VERSION}",app:"{APP_NAME}",platform:"{sys.platform}"}};</script>'
        return Response(html.replace("<!--BOOT-->", boot), mimetype="text/html")

    @app.route("/ui/<path:name>")
    def ui(name):
        p = (UI_DIR / name).resolve()
        if UI_DIR not in p.parents or not p.is_file():
            abort(404)
        resp = send_file(str(p))
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # ── helpers ─────────────────────────────────────────────────────────────
    def roll_out(r):
        return {**r, "frames": [frame_out(f) for f in r["frames"]]}

    def frame_out(f):
        return {**f, "missing": not os.path.exists(f["path"])}

    def body():
        return request.get_json(silent=True) or {}

    def need_roll(rid):
        r = lib.roll(rid)
        if not r:
            abort(404)
        return r

    # ── state ───────────────────────────────────────────────────────────────
    @app.route("/api/state")
    def state():
        with lib.lock:
            return jsonify({"version": VERSION, "settings": lib.settings,
                            "rolls": [roll_out(r) for r in lib.rolls]})

    @app.route("/api/settings", methods=["PATCH"])
    def settings():
        with lib.lock:
            for k, v in body().items():
                if k in lib.settings:
                    lib.settings[k] = v
        lib.touch()
        return jsonify(lib.settings)

    # ── rolls ───────────────────────────────────────────────────────────────
    @app.route("/api/rolls", methods=["POST"])
    def create_roll():
        d = body()
        r = lib.add_roll(d.get("name") or f"Roll {len(lib.rolls) + 1:03d}", d.get("film", ""))
        return jsonify(roll_out(r))

    @app.route("/api/rolls/<rid>")
    def get_roll(rid):
        with lib.lock:
            return jsonify(roll_out(need_roll(rid)))

    @app.route("/api/rolls/<rid>", methods=["PATCH"])
    def patch_roll(rid):
        r = lib.update_roll(rid, body())
        if not r:
            abort(404)
        return jsonify({k: v for k, v in r.items() if k != "frames"})

    @app.route("/api/rolls/<rid>", methods=["DELETE"])
    def delete_roll(rid):
        r = lib.delete_roll(rid)
        if r:
            for f in r["frames"]:
                cache.drop(f["id"])
        return jsonify({"ok": True})

    # ── frames ──────────────────────────────────────────────────────────────
    @app.route("/api/rolls/<rid>/frames", methods=["PATCH"])
    def patch_frames(rid):
        out = lib.update_frames(rid, body().get("frames", []))
        if out is None:
            abort(404)
        return jsonify([frame_out(f) for f in out])

    @app.route("/api/rolls/<rid>/frames/delete", methods=["POST"])
    def delete_frames(rid):
        gone = lib.delete_frames(rid, body().get("ids", []))
        for f in gone:
            cache.drop(f["id"])
        return jsonify({"deleted": len(gone)})

    @app.route("/api/rolls/<rid>/detect", methods=["POST"])
    def detect(rid):
        r = need_roll(rid)
        ids = set(body().get("ids", []))
        updates, found = [], 0
        for f in list(r["frames"]):
            if f["id"] not in ids:
                continue
            try:
                det = jobs.redetect(lib, cache, r, f)
            except Exception as e:
                log.warning("detect failed: %s", e)
                det = None
            if det:
                found += 1
                updates.append({"id": f["id"], "cut": det[0], "gap": det[1], "auto": True})
            else:
                updates.append({"id": f["id"], "auto": False})
        out = lib.update_frames(rid, updates)
        return jsonify({"found": found, "frames": [frame_out(f) for f in out]})

    def _preview(fid, kind):
        r, _, f = lib.find_frame(fid)
        if not f:
            abort(404)
        try:
            had_bits = f.get("bits")
            if not jobs.ensure_previews(cache, f):
                abort(404)
            if not had_bits:
                lib.touch()
        except Exception as e:
            log.warning("preview failed for %s: %s", f["path"], e)
            abort(500)
        p = cache.disp(fid) if kind == "d" else cache.thumb(fid)
        rot = request.args.get("r", 0, type=int) % 360
        if not rot:
            resp = send_file(str(p), mimetype="image/jpeg")
        else:
            with Image.open(p) as im:
                im = imaging.pil_rotate(im, rot)
                resp = send_file(imaging.jpeg_bytes(im, 90 if kind == "d" else 85), mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "private, max-age=86400"
        return resp

    @app.route("/api/thumb/<fid>")
    def thumb(fid):
        return _preview(fid, "t")

    @app.route("/api/display/<fid>")
    def display(fid):
        return _preview(fid, "d")

    # ── import / relink ─────────────────────────────────────────────────────
    @app.route("/api/pick", methods=["POST"])
    def pick():
        d = body()
        start = d.get("dir") or ""
        if d.get("kind") == "file":
            res = dialogs.pick_file(d.get("title", "Choose file"), start, d.get("types") or [])
        else:
            res = dialogs.pick_folder(d.get("title", "Choose folder"), start)
        return jsonify({"path": res})

    def _valid_dir(p):
        return bool(p) and os.path.isdir(p)

    @app.route("/api/import", methods=["POST"])
    def import_():
        d = body()
        folder = d.get("path", "")
        if not _valid_dir(folder):
            return jsonify({"error": "That folder doesn't exist"}), 400
        rid = d.get("rollId")
        if rid:
            need_roll(rid)
        else:
            name = Path(folder).name
            if name.lower() in ("raw", "jpg", "jpeg", "tif", "tiff", "scans") and Path(folder).parent.name:
                name = Path(folder).parent.name
            rid = lib.add_roll(name)["id"]
        with lib.lock:
            lib.settings["lastImportDir"] = str(Path(folder).parent)
        job = jobs.import_folder(lib, cache, rid, folder)
        return jsonify({"rollId": rid, "job": job})

    @app.route("/api/rolls/<rid>/relink", methods=["POST"])
    def relink(rid):
        need_roll(rid)
        folder = body().get("path", "")
        if not _valid_dir(folder):
            return jsonify({"error": "That folder doesn't exist"}), 400
        n, miss = jobs.relink(lib, rid, folder)
        return jsonify({"relinked": n, "missing": miss})

    @app.route("/api/library/import", methods=["POST"])
    def import_library():
        p = body().get("path", "")
        if not p or not os.path.isfile(p):
            return jsonify({"error": "File not found"}), 400
        try:
            rolls = load_any(p)
        except Exception as e:
            return jsonify({"error": f"Couldn't read that library: {e}"}), 400
        lib.merge_rolls(rolls)
        return jsonify({"rolls": len(rolls), "frames": sum(len(r["frames"]) for r in rolls)})

    # ── export ──────────────────────────────────────────────────────────────
    def _export_args():
        d = body()
        fmt = d.get("format", "tiff")
        naming = d.get("naming", "roll")
        if fmt not in jobs.EXT or naming not in ("roll", "source"):
            abort(400)
        return d, fmt, naming

    @app.route("/api/rolls/<rid>/export/check", methods=["POST"])
    def export_check(rid):
        r = need_roll(rid)
        d, fmt, naming = _export_args()
        with lib.lock:
            plan = jobs.plan_export(r, d.get("ids"), fmt, naming)
        out_dir = d.get("dir", "")
        conflicts = jobs.existing_conflicts(plan, out_dir) if _valid_dir(out_dir) else []
        return jsonify({"files": len(plan), "conflicts": conflicts[:50], "conflictCount": len(conflicts)})

    @app.route("/api/rolls/<rid>/export", methods=["POST"])
    def export(rid):
        need_roll(rid)
        d, fmt, naming = _export_args()
        out_dir = d.get("dir", "")
        if not out_dir:
            return jsonify({"error": "Choose an export folder"}), 400
        try:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return jsonify({"error": f"Can't write there: {e}"}), 400
        with lib.lock:
            lib.settings.update({"exportDir": out_dir, "exportFormat": fmt, "naming": naming})
        lib.touch()
        job = jobs.export(lib, rid, d.get("ids"), out_dir, fmt, naming, d.get("conflict", "overwrite"))
        return jsonify({"job": job})

    @app.route("/api/rolls/<rid>/contact-sheet", methods=["POST"])
    def sheet(rid):
        need_roll(rid)
        out_dir = body().get("dir", "")
        if not _valid_dir(out_dir):
            return jsonify({"error": "Choose a folder"}), 400
        return jsonify({"job": jobs.contact_sheet(lib, cache, rid, out_dir)})

    # ── jobs / misc ─────────────────────────────────────────────────────────
    @app.route("/api/jobs")
    def list_jobs():
        return jsonify(jobs.active_jobs())

    @app.route("/api/jobs/<jid>")
    def job(jid):
        j = jobs.get_job(jid)
        if not j:
            abort(404)
        return jsonify(j)

    @app.route("/api/reveal", methods=["POST"])
    def reveal_():
        p = body().get("path", "")
        if p == "@data":
            p = str(lib.path.parent)
        if p and os.path.exists(p):
            reveal(p)
        return jsonify({"ok": True})

    return app
