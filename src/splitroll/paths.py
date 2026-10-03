"""Per-user locations for the library, cache and log (never the install dir)."""
import os
import sys
from pathlib import Path

from splitroll import APP_NAME


def _home_override():
    p = os.environ.get("SPLITROLL_HOME")
    return Path(p) if p else None


def data_dir() -> Path:
    o = _home_override()
    if o:
        d = o / "data"
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    o = _home_override()
    if o:
        d = o / "cache"
    elif sys.platform == "win32":
        d = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME / "cache"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Caches" / APP_NAME
    else:
        d = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    return cache_dir().parent / f"{APP_NAME}.log" if sys.platform == "win32" else data_dir() / f"{APP_NAME}.log"


def library_path() -> Path:
    return data_dir() / "library.json"
