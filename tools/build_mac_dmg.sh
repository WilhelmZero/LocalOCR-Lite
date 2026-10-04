#!/bin/bash
# Run on an Apple Silicon Mac with native Python 3.12 and Xcode command line tools.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This build must run on an Apple Silicon Mac, without Rosetta." >&2
  exit 1
fi
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
"$PYTHON_BIN" -c 'import platform,sys; assert sys.version_info[:2] == (3,12) and platform.machine() == "arm64", "Native arm64 Python 3.12 required"'

BUILD="$ROOT/build/mac"
VENV="$BUILD/venv"
mkdir -p "$BUILD" "$ROOT/release"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r requirements-ui.lock.txt \
  'paddleocr[doc-parser]==3.7.0' 'paddlex==3.7.2' 'paddlepaddle==3.3.1' \
  'pyinstaller>=6.0,<7'
"$VENV/bin/python" -m pip check

"$VENV/bin/python" -m PyInstaller --noconfirm --clean --windowed --onedir \
  --target-arch arm64 --name LocalOCR \
  --osx-bundle-identifier com.localocr.review \
  --paths "$ROOT" \
  --collect-all paddle --collect-all paddleocr --collect-all paddlex \
  --collect-all cv2 --collect-all PySide6 \
  --recursive-copy-metadata paddlepaddle \
  --recursive-copy-metadata paddleocr \
  --recursive-copy-metadata paddlex \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" \
  --specpath "$BUILD" tools/mac_entry.py

APP="$BUILD/dist/LocalOCR.app"
"$APP/Contents/MacOS/LocalOCR" --self-check
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

STAGE="$(mktemp -d "$BUILD/dmg-stage.XXXXXX")"
cp -R "$APP" "$STAGE/LocalOCR.app"
ln -s /Applications "$STAGE/Applications"
DMG="$ROOT/release/LocalOCR-1.3.0-Apple-Silicon.dmg"
hdiutil create -volname 'LocalOCR' -srcfolder "$STAGE" -ov -format UDZO "$DMG"
hdiutil verify "$DMG"
echo "$DMG"
