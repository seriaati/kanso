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
cp icons/icon.svg public/favicon.svg
magick "$TMP/icon.png" -resize 192x192 public/icon-192.png
cp "$TMP/icon.png" public/icon-512.png
cp "$TMP/maskable.png" public/icon-maskable-512.png
# iOS rounds the corners itself and shows transparency as black: full bleed, like the maskable one.
magick "$TMP/maskable.png" -resize 180x180 public/apple-touch-icon.png
rm -r "$TMP"
