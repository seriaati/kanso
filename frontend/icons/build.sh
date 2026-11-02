#!/bin/sh
# Rasterize the app icons into public/. Run from frontend/: sh icons/build.sh
# CHROME is any headless Chromium (e.g. Playwright's chrome-headless-shell); ImageMagick 7 scales.
set -e
CHROME=${CHROME:-chrome-headless-shell}
TMP=$(mktemp -d)
shot() { # svg, out.png: render at 512x512 with a transparent background
  "$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars --default-background-color=00000000 \
    --window-size=512,512 --screenshot="$2" "file://$PWD/$1" >/dev/null 2>&1
}
shot icons/icon.svg "$TMP/icon.png"
shot icons/maskable.svg "$TMP/maskable.png"
shot icons/badge.svg "$TMP/badge.png"
cp icons/icon.svg public/favicon.svg
magick "$TMP/icon.png" -resize 192x192 public/icon-192.png
cp "$TMP/icon.png" public/icon-512.png
cp "$TMP/maskable.png" public/icon-maskable-512.png
# The small icon of a notification on Android (push-sw.js).
magick "$TMP/badge.png" -resize 96x96 public/badge-96.png
# iOS rounds the corners itself and shows transparency as black: full bleed, like the maskable one.
magick "$TMP/maskable.png" -resize 180x180 public/apple-touch-icon.png
# The link preview (docs/, not public/: crawlers fetch it from GitHub, so no client downloads it).
# Its fonts come from node_modules, so it needs file access.
"$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars --allow-file-access-from-files \
  --virtual-time-budget=3000 --window-size=1200,630 --screenshot=../docs/og.png "file://$PWD/icons/og.html" >/dev/null 2>&1
rm -r "$TMP"
