# splitroll

Split half-frame 35mm scans into pairs, review them on a contact sheet, and export lossless files.

- **Import** a folder of scans → it becomes a roll. Drag a folder onto the window or use *Import folder*.
- **Auto-detect** finds the gap between the two half-frames and sets the roll's border.
- **Contact sheet** — select like File Explorer (marquee drag, Ctrl/Shift/Alt-click, `Ctrl+A`/`Ctrl+D`) and split, mark full, rotate or remove frames in one go.
- **Crop** — drag the cut line and the border tabs, nudge with `,` `.`, rotate the scan or each half, zoom and pan. `Enter` splits, `→` moves on.
- **Export** TIFF / PNG (lossless, 16-bit and colour profile preserved) or JPEG (quality 100, 4:4:4).
- Everything is undoable with `Ctrl+Z` — including an accidental import. Your scans are never modified.

Press `?` in the app for all keyboard shortcuts.

## Install (Windows 10/11)

Download `splitroll-<version>-setup.exe` and run it. It installs for the current user only (no admin
prompt) into `%LOCALAPPDATA%\Programs\splitroll` and adds a Start-menu shortcut.

Because the installer isn't code-signed yet, Windows SmartScreen may say *"Windows protected your PC"* —
click **More info → Run anyway**.

Your library lives in `%APPDATA%\splitroll\library.json`; preview cache and log in `%LOCALAPPDATA%\splitroll`.

### Moving from halfframe 1.1 (Mac)

Menu `⋯` → *Import v1.1 library…* and pick your `halfframe_data.json`. Rolls, cuts, rotations and
export choices come across; then use *Locate folder…* on each roll to point it at the scans on this computer.

## Run from source (any OS)

```
pip install flask pillow numpy tifffile imagecodecs pywebview
cd src && python -m splitroll            # native window
cd src && python -m splitroll --browser  # in your browser (dev)
```

## Build the Windows installer

Normally done by GitHub Actions on every version tag. Locally, from Linux or WSL with `nsis`, `mingw-w64`
and `python3.12` installed (the Python runtime for Windows is downloaded automatically):

```
bash packaging/windows/build.sh   # → dist/splitroll-<version>-setup.exe
```

## Versions & releases

Versions follow `MAJOR.MINOR.PATCH` — patch for fixes, minor for features. The version lives in one
place, `src/splitroll/__init__.py`, and every release has a matching `CHANGELOG.md` section and git tag.

To release:

1. Bump `VERSION` in `src/splitroll/__init__.py` and add a `## x.y.z — date` section to `CHANGELOG.md`.
2. Commit, then tag and push: `git tag vx.y.z && git push origin main vx.y.z`
3. GitHub Actions (`.github/workflows/release.yml`) runs the tests, builds the installer and publishes
   the release with the installer and its SHA-256 attached.

## Tests

```
python -m pytest tests            # imaging, migration, import/export end-to-end
python tests/ui_smoke.py          # headless UI run (needs a dev server + playwright)
```
