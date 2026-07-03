#!/usr/bin/env bash
# Build dist/<name>.zip bundles for upload to Claude web/desktop (Settings -> Capabilities/
# Skills; org admins can deploy org-wide). The upload accepts ZIP files containing the skill
# folder. Each bundle includes the repo LICENSE (the vendored fsj.py carries ported MIT code).
# Uses `python -m zipfile` (stdlib) so no external zip binary is needed.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-$(command -v python3 || command -v python || echo python)}"
mkdir -p dist
for dir in skills/*/; do
  name=$(basename "$dir")
  stage="dist/build/${name}"
  rm -rf "$stage" "dist/${name}.zip"
  mkdir -p "dist/build"
  cp -r "$dir" "$stage"
  cp LICENSE "$stage/LICENSE"
  find "$stage" -type d -name "__pycache__" -exec rm -rf {} +
  "$PYTHON" -c "import shutil; shutil.make_archive('dist/${name}', 'zip', 'dist/build', '${name}')"
  echo "built dist/${name}.zip"
done
rm -rf dist/build
