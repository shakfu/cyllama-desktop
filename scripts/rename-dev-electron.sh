#!/usr/bin/env bash
# Name the dev Electron.app after the product, so the macOS menu bar and Dock
# say "Cyllama Desktop". macOS takes the app menu title from the bundle's
# Info.plist; app.setName() and menu labels cannot change it. Packaged builds
# get the name from electron-builder. Run by postinstall.
#
# The npm Electron binary is linker-signed with no Info.plist slot, so the edit
# leaves its signature valid.
set -euo pipefail
[[ "$(uname -s)" == Darwin ]] || exit 0
APP="node_modules/electron/dist/Electron.app"
[[ -f "$APP/Contents/Info.plist" ]] || exit 0
NAME="$(node -p 'require("./package.json").productName')"
plutil -replace CFBundleName -string "$NAME" "$APP/Contents/Info.plist"
plutil -replace CFBundleDisplayName -string "$NAME" "$APP/Contents/Info.plist"
# LaunchServices caches bundle names by modification time.
touch "$APP"
