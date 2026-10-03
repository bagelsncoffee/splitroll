import sys

try:
    from splitroll.main import run
except Exception:                       # broken install: report instead of vanishing silently
    import os
    import traceback
    msg = traceback.format_exc()
    try:
        d = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "splitroll")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "crash.log"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, "splitroll failed to start:\n\n" + msg[-1500:], "splitroll", 0x10)
    else:
        print(msg, file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    run()
