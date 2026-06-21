#!/bin/bash
# Overlay the vendored WebUI fork tree onto the base image's /apptoo.
#
# The base image ships /apptoo as a clean `COPY . /apptoo` of upstream
# hermes-webui at the pinned tag, plus exactly one generated file
# (api/_version.py). So the fork tree IS /apptoo's source, and we rsync the
# whole fork over /apptoo: unchanged files are no-ops, HUMR-changed files win.
# Against a clean checkout this reproduces /apptoo byte for byte except the
# HUMR-changed files.
#
#   --delete            reconcile upstream deletions/renames (stale source can't
#                       survive). Excluded paths are protected from deletion.
#   --exclude-from .dockerignore   the fork carries upstream's own .dockerignore
#                       (tests/, .env*, __pycache__, *.pyc); mirroring it keeps
#                       /apptoo identical to what an upstream image build emits.
#   --exclude api/_version.py      build-stamped, not in git -- must survive.
#   --exclude .git/.humr-upstream-version   not WebUI source.
set -euo pipefail

SRC=${1:?usage: overlay-webui.sh <webui-src-dir> <dest-apptoo-dir>}
DEST=${2:?missing dest dir}

excludes=(--exclude='.git' --exclude='.humr-upstream-version' --exclude='api/_version.py')
if [ -f "$SRC/.dockerignore" ]; then
    excludes+=(--exclude-from="$SRC/.dockerignore")
fi

rsync -a --delete "${excludes[@]}" "$SRC/" "$DEST/"

# Recompile so .pyc stamps match the overlaid source's (mtime, size); stale
# bytecode must not shadow the overlaid sources at runtime.
python3 -m compileall -q "$DEST" || true

echo "[overlay-webui] overlaid $SRC -> $DEST"
