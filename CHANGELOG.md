# Changelog

## 1.2.1 — 2026-10-03 · first public release

Fixes from testing the 1.2.0 build on Windows 11:
- The app couldn't open its window ("Cannot find win-arm64"): the installer left out WebView2 files that
  pywebview needs on every PC.
- Keyboard shortcuts stopped working after a folder dialog closed, until you clicked in the window.
- Messages popped up over the Split / Export buttons — they now appear centred above the picture.
- The crop toolbar could hide the Split button in a narrow window; it now wraps instead.

New:
- Interface size: `Ctrl +` / `Ctrl −` / `Ctrl 0` (or ⋯ → Interface size…), remembered between sessions —
  handy on large or ultrawide monitors.

## 1.2.0 — 2026-10-03 · Windows rewrite, renamed from halfframe (test build, not published)

**New**
- Windows installer (per-user, no admin), Start-menu shortcut, uninstaller, app icon.
- Auto-detects the gap between half-frames on import; sets the roll border from the measured gap. `A` re-detects.
- Contact sheet multi-select actions: split, full, reset, auto-detect, rotate, remove (Shift-click ranges).
- Cut lines and border shown on contact-sheet thumbnails; portrait/rotated frames are no longer cropped.
- Undo / redo (`Ctrl+Z`, `Ctrl+Shift+Z`) for every edit.
- Fine nudge of the cut with `,` / `.` (Shift for bigger steps); double-click to place the line.
- Export: progress bar, choose ROLL_01L or ORIGINAL_L file names, overwrite / skip-existing prompt,
  remembers the folder, *Open folder* when done.
- Drag a folder onto the window to import it. Lab folders with a single `raw/` or `jpg/` subfolder are found.
- Editable roll date and free-text film stock (with suggestions).
- Import a v1.1 library (`halfframe_data.json`) and relink it to scans on this machine.
- In-app dialogs, keyboard-shortcut sheet (`?`), higher-contrast text, dark title bar on Windows.

**Fixed**
- 16-bit TIFF/PNG scans were exported as 8-bit — bit depth is now kept end to end.
- Colour profiles and DPI were dropped on export.
- JPEG export used quality 95 with chroma subsampling — now quality 100, 4:4:4.
- The cut could be dragged to 0% / 100%, producing empty halves and failed exports; a stray click moved the line.
- *Batch apply* only moved the line without confirming frames — replaced by *Split all…* which does both.
- Unticking every frame in Export re-ticked them all on the next visit.
- Exports silently overwrote files; roll names with `: / \ ? *` broke file names on Windows.
- A second import stopped progress updates for the first.
- Fixed port 5555: a busy port could connect the window to another program. Now a random local port with a token.
- Library was stored next to the app (not writable once installed); now in the user's app-data folder.
- Contact-sheet image was built from small thumbnails and came out soft.
- Selecting frames redrew the whole grid (lost scroll position, broke double-click).

## 1.1.0 — 2026-09 · halfframe (macOS)
- Python/Flask app in a native window; roll library saved to `halfframe_data.json`.
- Contact sheet, crop with draggable cut and border, per-half rotation, preview, export to JPEG/PNG/TIFF,
  contact-sheet image, frame deletion, relink of moved folders.
