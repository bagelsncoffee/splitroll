"""The roll library: data model, persistence and migration from v1.1.

Schema (version 2)
  settings: {exportDir, exportFormat, naming, lastImportDir}
  rolls[]:  {id, name, film, date, fullFrame, border, frames[]}
  frames[]: {id, path, name, w, h, bits, rot, cut, auto, gap, state, lRot, rRot, export}
    state: 'pending' | 'split' | 'full'
    rot:   clockwise rotation of the whole scan (0/90/180/270)
    cut:   split position as % of the (rotated) width
    export: included in export when state != pending
All edits are stored in the library; source scans are never modified.
"""
import json
import os
import shutil
import threading
import time
import uuid
from datetime import date
from pathlib import Path

SCHEMA = 2
DEFAULT_SETTINGS = {"exportDir": "", "exportFormat": "tiff", "naming": "roll", "lastImportDir": "", "uiZoom": 1.0}
FRAME_FIELDS = {"rot", "cut", "auto", "gap", "state", "lRot", "rRot", "export"}
ROLL_FIELDS = {"name", "film", "date", "fullFrame", "border"}


def new_id():
    return uuid.uuid4().hex[:10]


def new_frame(path, name, w, h, bits):
    return {"id": new_id(), "path": str(path), "name": name, "w": int(w), "h": int(h),
            "bits": int(bits), "rot": 0, "cut": 50.0, "auto": False, "gap": None,
            "state": "pending", "lRot": 0, "rRot": 0, "export": True}


def new_roll(name, film="", d=None):
    return {"id": new_id(), "name": name or "New roll", "film": film or "",
            "date": d or date.today().isoformat(), "fullFrame": False, "border": 0, "frames": []}


def effective_state(roll, f):
    if f["state"] == "pending" and roll.get("fullFrame"):
        return "full"
    return f["state"]


def _clean_frame(f):
    f["rot"] = int(f.get("rot", 0)) % 360
    f["lRot"] = int(f.get("lRot", 0)) % 360
    f["rRot"] = int(f.get("rRot", 0)) % 360
    try:
        f["cut"] = min(95.0, max(5.0, float(f.get("cut", 50))))
    except (TypeError, ValueError):
        f["cut"] = 50.0
    if f.get("state") not in ("pending", "split", "full"):
        f["state"] = "pending"
    f["export"] = bool(f.get("export", True))
    f["auto"] = bool(f.get("auto", False))
    return f


# ── v1.1 → v2 migration ─────────────────────────────────────────────────────
def migrate_v1(old):
    """Convert a v1.1 halfframe_data.json dict into v2 rolls."""
    rolls = []
    for r in old.get("rolls", []):
        crops = {int(k): v for k, v in (r.get("crops") or {}).items() if v}
        pos = {int(k): v for k, v in (r.get("pos") or {}).items()}
        rots = {int(k): v for k, v in (r.get("rots") or {}).items() if v}
        irots = {int(k): v for k, v in (r.get("inputRots") or {}).items()}
        exp = set(int(i) for i in (r.get("expSel") or []))
        nr = new_roll(r.get("name") or "Roll", r.get("film", ""), r.get("date") or None)
        nr["fullFrame"] = bool(r.get("fullFrame"))
        nr["border"] = int(r.get("deadZone") or 0)
        for i, fr in enumerate(r.get("frames", [])):
            f = new_frame(fr.get("path", ""), fr.get("name") or Path(fr.get("path", "")).name,
                          fr.get("w", 0), fr.get("h", 0), 8)
            f["bits"] = 0                                    # unknown → probed lazily
            cr = crops.get(i)
            rr = rots.get(i) or {}
            f["rot"] = int(irots.get(i, 0) or 0)
            if cr:
                f["state"] = "full" if cr.get("skip") else "split"
                f["cut"] = cr.get("pct", pos.get(i, 50)) if not cr.get("skip") else pos.get(i, 50)
                f["lRot"] = int(cr.get("lRot", rr.get("l", 0)) or 0)
                f["rRot"] = int(cr.get("rRot", rr.get("r", 0)) or 0)
            else:
                f["cut"] = pos.get(i, 50)
                f["lRot"] = int(rr.get("l", 0) or 0)
                f["rRot"] = int(rr.get("r", 0) or 0)
            f["export"] = (i in exp) if exp else True
            nr["frames"].append(_clean_frame(f))
        rolls.append(nr)
    return rolls


def load_any(path):
    """Read a library file of either schema and return a list of v2 rolls."""
    d = json.loads(Path(path).read_text("utf-8"))
    if d.get("version") == SCHEMA:
        return [dict(r, frames=[_clean_frame(f) for f in r.get("frames", [])]) for r in d.get("rolls", [])]
    return migrate_v1(d)


# ── the library object ──────────────────────────────────────────────────────
class Library:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.rolls = []
        self.settings = dict(DEFAULT_SETTINGS)
        self._dirty = False
        self._timer = None
        self._load()

    # persistence
    def _load(self):
        p = self.path
        if not p.exists():
            return
        try:
            d = json.loads(p.read_text("utf-8"))
        except Exception:
            bak = p.with_suffix(".json.bak")
            if bak.exists():
                d = json.loads(bak.read_text("utf-8"))
            else:
                broken = p.with_name(f"library.broken-{int(time.time())}.json")
                shutil.copy2(p, broken)
                return
        if d.get("version") == SCHEMA:
            self.rolls = [dict(r, frames=[_clean_frame(f) for f in r.get("frames", [])])
                          for r in d.get("rolls", [])]
        else:
            self.rolls = migrate_v1(d)
        self.settings.update(d.get("settings") or {})

    def save_now(self):
        with self.lock:
            data = json.dumps({"version": SCHEMA, "settings": self.settings, "rolls": self.rolls},
                              indent=1, ensure_ascii=False)
            self._dirty = False
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(data, "utf-8")
        if self.path.exists():
            try:
                shutil.copy2(self.path, self.path.with_suffix(".json.bak"))
            except Exception:
                pass
        os.replace(tmp, self.path)

    def touch(self):
        """Mark dirty and save shortly (coalesces bursts of edits)."""
        with self.lock:
            self._dirty = True
            if self._timer is None:
                self._timer = threading.Timer(0.4, self._flush)
                self._timer.daemon = True
                self._timer.start()

    def _flush(self):
        with self.lock:
            self._timer = None
        try:
            self.save_now()
        except Exception:
            import logging
            logging.exception("library save failed")

    def flush(self):
        with self.lock:
            t, self._timer = self._timer, None
        if t:
            t.cancel()
        if self._dirty:
            self.save_now()

    # lookups
    def roll(self, rid):
        return next((r for r in self.rolls if r["id"] == rid), None)

    def find_frame(self, fid):
        for r in self.rolls:
            for i, f in enumerate(r["frames"]):
                if f["id"] == fid:
                    return r, i, f
        return None, -1, None

    # mutations (callers hold no lock; each method is atomic)
    def add_roll(self, name, film=""):
        with self.lock:
            r = new_roll(name, film)
            self.rolls.append(r)
        self.touch()
        return r

    def update_roll(self, rid, changes):
        with self.lock:
            r = self.roll(rid)
            if not r:
                return None
            for k, v in changes.items():
                if k in ROLL_FIELDS:
                    r[k] = v
            r["border"] = max(0, int(r.get("border") or 0))
            r["fullFrame"] = bool(r.get("fullFrame"))
        self.touch()
        return r

    def delete_roll(self, rid):
        with self.lock:
            r = self.roll(rid)
            self.rolls = [x for x in self.rolls if x["id"] != rid]
        self.touch()
        return r

    def update_frames(self, rid, updates):
        """updates: list of {id, ...fields}. Returns the updated frames."""
        out = []
        with self.lock:
            r = self.roll(rid)
            if not r:
                return None
            by_id = {f["id"]: f for f in r["frames"]}
            for u in updates:
                f = by_id.get(u.get("id"))
                if not f:
                    continue
                for k, v in u.items():
                    if k in FRAME_FIELDS:
                        f[k] = v
                out.append(_clean_frame(f))
        self.touch()
        return out

    def delete_frames(self, rid, ids):
        ids = set(ids)
        with self.lock:
            r = self.roll(rid)
            if not r:
                return []
            gone = [f for f in r["frames"] if f["id"] in ids]
            r["frames"] = [f for f in r["frames"] if f["id"] not in ids]
        self.touch()
        return gone

    def append_frames(self, rid, frames):
        with self.lock:
            r = self.roll(rid)
            if not r:
                return False
            r["frames"].extend(frames)
        self.touch()
        return True

    def merge_rolls(self, rolls):
        with self.lock:
            existing = {r["id"] for r in self.rolls}
            for r in rolls:
                if r["id"] in existing:
                    r["id"] = new_id()
                self.rolls.append(r)
        self.touch()
        return rolls
