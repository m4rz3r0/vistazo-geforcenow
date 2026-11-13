#!/bin/sh
# Builds out/VistazoGeForceNOW.zip with the layout Decky's installer expects (one top-level folder).
set -e
cd "$(dirname "$0")"
if command -v pnpm >/dev/null; then pnpm run build >/dev/null; else npx -y pnpm@9.15.9 run build >/dev/null; fi
rm -rf out && mkdir -p out/VistazoGeForceNOW/dist
cp plugin.json package.json main.py README.md LICENSE out/VistazoGeForceNOW/
cp dist/index.js out/VistazoGeForceNOW/dist/
mkdir out/VistazoGeForceNOW/bin
cp bin/gfn-launch out/VistazoGeForceNOW/bin/
chmod 755 out/VistazoGeForceNOW/bin/gfn-launch
(cd out && zip -qr VistazoGeForceNOW.zip VistazoGeForceNOW)
sha256sum out/VistazoGeForceNOW.zip
