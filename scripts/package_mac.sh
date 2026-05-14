#!/usr/bin/env bash
# Build a macOS .dmg installer for Ride Sim.
#
# Prerequisites:
#   pip install pyinstaller
#   brew install create-dmg
#
# Usage:
#   scripts/package_mac.sh
#
# Output:
#   dist/Ride Sim-<version>-mac.dmg
#
# Note: this build is NOT code-signed or notarized. Beta testers will see
# a Gatekeeper warning ("unidentified developer") and must Ctrl-click ->
# Open the first time. Signing requires an Apple Developer ID ($99/yr).

set -euo pipefail

cd "$(dirname "$0")/.."

command -v pyinstaller >/dev/null 2>&1 || {
  echo "ERROR: pyinstaller not found. Run: pip install pyinstaller" >&2
  exit 1
}
command -v create-dmg >/dev/null 2>&1 || {
  echo "ERROR: create-dmg not found. Run: brew install create-dmg" >&2
  exit 1
}

VERSION=$(grep -E '^APP_VERSION' ride_sim.py | head -1 | sed -E 's/.*= *"([^"]+)".*/\1/')
APP_NAME="Ride Sim"
DMG_NAME="${APP_NAME}-${VERSION}-mac.dmg"

echo "==> Building ${APP_NAME} ${VERSION} for macOS"

rm -rf build dist
pyinstaller ride_sim.spec --clean --noconfirm

# Clean up any leftover temp DMG from an interrupted create-dmg run,
# then restart Finder so its automation backlog doesn't time out the
# AppleScript that positions icons in the DMG window (a known issue on
# recent macOS that fails with "AppleEvent timed out. (-1712)").
rm -f "dist/${DMG_NAME}" "dist/rw."*"${APP_NAME}"*.dmg
killall Finder 2>/dev/null || true

create-dmg \
  --volname "${APP_NAME}" \
  --window-size 540 360 \
  --icon-size 96 \
  --icon "${APP_NAME}.app" 140 180 \
  --app-drop-link 400 180 \
  --no-internet-enable \
  "dist/${DMG_NAME}" \
  "dist/${APP_NAME}.app"

echo
echo "==> Done: dist/${DMG_NAME}"
