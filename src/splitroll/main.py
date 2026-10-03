"""Entry point: start the local server and open the native app window."""
import argparse
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from splitroll import APP_NAME, APP_TITLE, VERSION, paths

log = logging.getLogger(APP_NAME)


# ── logging (GUI builds have no console) ─────────────────────────────────────
def setup_logging():
    lp = paths.log_path()
    try:
        if lp.exists() and lp.stat().st_size > 2_000_000:
            lp.replace(lp.with_suffix(".old.log"))
    except OSError:
        pass
    handler = logging.FileHandler(lp, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    if sys.stdout is None or sys.stderr is None:            # pythonw / GUI launcher
        f = open(lp, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    log.info("%s %s starting (python %s, %s)", APP_NAME, VERSION, sys.version.split()[0], sys.platform)


def fatal(msg):
    log.error(msg)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, APP_TITLE, 0x10)
    else:
        print(msg, file=sys.stderr)


# ── single instance ──────────────────────────────────────────────────────────
_instance_handle = None


def acquire_single_instance():
    global _instance_handle
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        _instance_handle = k32.CreateMutexW(None, False, f"Local\\{APP_NAME}-single-instance")
        if k32.GetLastError() == 183:                         # ERROR_ALREADY_EXISTS
            u32 = ctypes.windll.user32
            hwnd = u32.FindWindowW(None, APP_TITLE)
            if hwnd:
                u32.ShowWindow(hwnd, 9)                       # SW_RESTORE
                u32.SetForegroundWindow(hwnd)
            return False
        return True
    import fcntl
    lock = open(paths.data_dir() / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    _instance_handle = lock
    return True


# ── native dialogs ───────────────────────────────────────────────────────────
class Dialogs:
    def __init__(self):
        self.window = None

    def _wv(self, kind, title, start, types=()):
        import webview
        ft = webview.FileDialog.FOLDER if kind == "folder" else webview.FileDialog.OPEN
        res = self.window.create_file_dialog(ft, directory=start or "", file_types=tuple(types))
        if not res:
            return None
        return res[0] if isinstance(res, (list, tuple)) else res

    def _tk(self, kind, title, start):
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception:
            return None
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            if kind == "folder":
                p = filedialog.askdirectory(title=title, initialdir=start or None)
            else:
                p = filedialog.askopenfilename(title=title, initialdir=start or None)
        finally:
            root.destroy()
        return p or None

    def pick_folder(self, title, start=""):
        return self._wv("folder", title, start) if self.window else self._tk("folder", title, start)

    def pick_file(self, title, start="", types=()):
        return self._wv("file", title, start, types) if self.window else self._tk("file", title, start)


# ── window helpers (Windows) ─────────────────────────────────────────────────
def dark_title_bar(title):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = 0
        for _ in range(40):
            hwnd = ctypes.windll.user32.FindWindowW(None, title)
            if hwnd:
                break
            time.sleep(0.05)
        if not hwnd:
            return
        val = ctypes.c_int(1)
        for attr in (20, 19):                                 # DWMWA_USE_IMMERSIVE_DARK_MODE
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4) == 0:
                break
        cap = ctypes.c_int(0x0014_1616)                       # caption colour (BGR) ≈ #161614
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap), 4)
    except Exception as e:
        log.info("dark title bar unavailable: %s", e)


def register_drop(window):
    """Folders dropped on the window are imported (full paths come from pywebview)."""
    try:
        from webview.dom import DOMEventHandler
    except Exception:
        return

    def on_drop(e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        found = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if found:
            import json
            window.evaluate_js(f"window.SRApp && SRApp.onNativeDrop({json.dumps(found)})")

    window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
    window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)


# ── server ───────────────────────────────────────────────────────────────────
def start_server(app):
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                break
        except OSError:
            time.sleep(0.05)
    return srv, port


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP_NAME)
    ap.add_argument("--browser", action="store_true", help="open in the default browser instead of a window")
    ap.add_argument("--port-file", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    setup_logging()
    if not acquire_single_instance():
        log.info("already running")
        return 0

    if sys.platform == "win32" and getattr(sys, "frozen", False) is False:
        # pythonnet needs to know which python DLL to bind to when embedded
        dll = Path(sys.base_prefix) / f"python{sys.version_info[0]}{sys.version_info[1]}.dll"
        if dll.exists():
            os.environ.setdefault("PYTHONNET_PYDLL", str(dll))
            try:
                os.add_dll_directory(str(dll.parent))
            except OSError:
                pass

    from splitroll.library import Library
    from splitroll.jobs import Cache
    from splitroll.server import create_app

    lib = Library(paths.library_path())
    cache = Cache(paths.cache_dir())
    dialogs = Dialogs()
    app = create_app(lib, cache, dialogs)
    srv, port = start_server(app)
    url = f"http://127.0.0.1:{port}/?t={app.config['TOKEN']}"
    log.info("serving on port %s", port)
    if args.port_file:
        Path(args.port_file).write_text(url)

    try:
        if args.browser:
            raise RuntimeError("browser mode requested")
        import webview
        window = webview.create_window(APP_TITLE, url, width=1440, height=920, min_size=(1180, 700),
                                       background_color="#161614", text_select=False)
        dialogs.window = window

        def on_start():
            dark_title_bar(APP_TITLE)
            register_drop(window)

        window.events.closing += lambda: lib.flush()
        kw = {"gui": "edgechromium"} if sys.platform == "win32" else {}
        webview.start(on_start, private_mode=False,
                      storage_path=str(paths.cache_dir().parent / "webview"), **kw)
    except Exception as e:
        if not args.browser:
            log.warning("native window unavailable (%s) — using browser", e)
            if sys.platform == "win32" and not args.browser:
                fatal(f"{APP_TITLE} couldn't open its window.\n\n{e}\n\n"
                      "Make sure the Microsoft Edge WebView2 Runtime is installed.")
                return 1
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        lib.flush()
        srv.shutdown()
        log.info("exit")
    return 0


def run():
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # last-resort crash report
        logging.exception("fatal")
        fatal(f"{APP_TITLE} hit an unexpected error and has to close.\n\n{e}\n\nDetails: {paths.log_path()}")
        sys.exit(1)
