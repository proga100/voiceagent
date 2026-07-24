#!/usr/bin/env bash
# Rebuild assets/avatar/avatar-bundle.js from the avatar sources.
# Single classic-script bundle (three.js + GLTFLoader + scene + GLB base64) —
# Android WebView blocks ES-module fetches from file://, so everything must
# ship as one non-module script with zero runtime fetches.
set -euo pipefail
cd "$(dirname "$0")"
npx --yes esbuild entry.js \
  --bundle \
  --format=iife \
  --minify \
  --alias:three=./vendor/three/three.module.js \
  --loader:.glb=base64 \
  --outfile=../assets/avatar/avatar-bundle.js
ls -lh ../assets/avatar/avatar-bundle.js
