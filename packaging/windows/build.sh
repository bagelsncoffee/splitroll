#!/usr/bin/env bash
# Build the Windows installer from Linux (no Windows machine needed).
#
# Needs: makensis, x86_64-w64-mingw32-gcc/windres, python3.12 (for .pyc),
#        python3 with pip, Pillow.
# Inputs (cached in $TOOLCHAIN, default ./_toolchain):
#   cpython-3.12-win64.tar.gz   python-build-standalone "install_only_stripped" build
# Output: dist/splitroll-<version>-setup.exe
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TOOLCHAIN="${TOOLCHAIN:-$ROOT/_toolchain}"
WORK="${WORK:-$ROOT/build}"
VERSION=$(python3 -c "import sys; sys.path.insert(0,'$ROOT/src'); import splitroll; print(splitroll.VERSION)")
PUBLISHER="splitroll"
STAGE="$WORK/stage"
echo "== splitroll $VERSION =="

rm -rf "$STAGE" && mkdir -p "$STAGE/app" "$WORK"

# 1. Python runtime ----------------------------------------------------------
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261003/cpython-3.12.15%2B20261003-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
if [ ! -f "$TOOLCHAIN/cpython-3.12-win64.tar.gz" ]; then
  mkdir -p "$TOOLCHAIN"
  curl -fsSL -o "$TOOLCHAIN/cpython-3.12-win64.tar.gz" "$PBS_URL"
fi
if [ ! -d "$WORK/python" ]; then
  tar xzf "$TOOLCHAIN/cpython-3.12-win64.tar.gz" -C "$WORK"
fi
cp -r "$WORK/python" "$STAGE/runtime"
R="$STAGE/runtime"
rm -rf "$R/Scripts" "$R/include" "$R/libs" "$R/tcl" "$R/python.exe" "$R/pythonw.exe" "$R/Lib/site-packages"/*
rm -rf "$R/Lib/"{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3,pydoc_data,venv,__phello__,turtle.py}
rm -f "$R/DLLs/"{_tkinter.pyd,tcl86t.dll,tk86t.dll,_test*.pyd,_ctypes_test.pyd,_msi.pyd,xxlimited*.pyd}
find "$R" -name "__pycache__" -type d -prune -exec rm -rf {} +
printf 'Lib\r\nDLLs\r\nLib\\site-packages\r\n..\\app\r\n' > "$R/python312._pth"

# 2. Dependencies (Windows wheels) -------------------------------------------
WHEELS="$WORK/wheels"
if [ ! -f "$WHEELS/.done" ]; then
  mkdir -p "$WHEELS"
  pip download -q --dest "$WHEELS" --platform win_amd64 --python-version 3.12 --implementation cp \
      --only-binary=:all: -r "$ROOT/requirements-win.txt" --no-deps
  pip download -q --dest "$WORK/sdist" --no-deps --no-binary=:all: proxy_tools==0.1.0
  touch "$WHEELS/.done"
fi
SITE="$R/Lib/site-packages"
pip install -q --no-index --target "$SITE" --platform win_amd64 --python-version 3.12 --implementation cp \
    --only-binary=:all: --no-deps "$WHEELS"/*.whl
tar xzf "$WORK"/sdist/proxy_tools-0.1.0.tar.gz -C "$WORK/sdist"
cp -r "$WORK/sdist/proxy_tools-0.1.0/proxy_tools" "$SITE/"
# trim what a desktop app never uses
rm -rf "$SITE"/bin "$SITE"/numpy/*/tests "$SITE"/numpy/tests "$SITE"/numpy/_pyinstaller "$SITE"/numpy/f2py \
       "$SITE"/webview/lib/pywebview-android.jar "$SITE"/webview/lib/WebBrowserInterop.x86.dll \
       "$SITE"/webview/lib/runtimes/win-x86 "$SITE"/webview/lib/runtimes/win-arm64 \
       "$SITE"/clr_loader/ffi/dlls/x86 "$SITE"/PIL/*.pyi
find "$SITE" -name "*.pdb" -delete
# imagecodecs: keep only the codecs TIFF/PNG decoding needs
KEEP='_imcd|_shared|_shared_cython|_png|_spng|_lzw|_zlib|_zlibng|_deflate|_jpeg8|_ljpeg|_jpegsof3|_lzma|_zstd|_webp|_tiff'
for f in "$SITE"/imagecodecs/_*.pyd; do
  b=$(basename "$f" .pyd); [[ "$b" =~ ^($KEEP)$ ]] || rm -f "$f"
done
find "$SITE" -name "__pycache__" -type d -prune -exec rm -rf {} +

# 3. App ----------------------------------------------------------------------
cp -r "$ROOT/src/splitroll" "$STAGE/app/"
find "$STAGE/app" -name "__pycache__" -type d -prune -exec rm -rf {} +

# 4. Precompile bytecode (same 3.12 magic as the runtime) --------------------
python3.12 -m compileall -q -j 0 "$R/Lib" "$STAGE/app" >/dev/null 2>&1 || true

# 5. Launcher exe ---------------------------------------------------------------
L="$WORK/launcher" && rm -rf "$L" && mkdir -p "$L"
cp "$ROOT/packaging/windows/launcher.c" "$ROOT/packaging/windows/launcher.manifest" "$ROOT/assets/icon.ico" "$L/"
IFS=. read -r V1 V2 V3 <<< "$VERSION"
sed -e "s/@V1@/$V1/g; s/@V2@/$V2/g; s/@V3@/$V3/g; s/@VERSION@/$VERSION/g; s/@PUBLISHER@/$PUBLISHER/g" \
    "$ROOT/packaging/windows/launcher.rc.in" > "$L/launcher.rc"
(cd "$L" && x86_64-w64-mingw32-windres launcher.rc -O coff -o launcher.res \
  && x86_64-w64-mingw32-gcc -O2 -Wall -Werror=implicit-function-declaration -Werror=int-conversion -municode -mwindows -s -o splitroll.exe launcher.c launcher.res -lshell32)
cp "$L/splitroll.exe" "$STAGE/"

# 6. Installer ----------------------------------------------------------------
N="$WORK/nsis" && rm -rf "$N" && mkdir -p "$N"
cp "$ROOT/packaging/windows/installer.nsi" "$ROOT/assets/icon.ico" "$N/"
python3 "$ROOT/packaging/windows/wizard_bitmap.py" "$ROOT/assets/icon.png" "$N/wizard.bmp"
SIZE_KB=$(du -sk "$STAGE" | cut -f1)
mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/splitroll-$VERSION-setup.exe"
(cd "$N" && makensis -V2 -DVERSION="$VERSION" -DSTAGE="$STAGE" -DOUTFILE="$OUT" -DSIZE_KB="$SIZE_KB" installer.nsi)
ls -la "$OUT"
sha256sum "$OUT" | tee "$OUT.sha256"
