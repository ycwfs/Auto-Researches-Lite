#!/usr/bin/env bash
# One-time: download + unzip the pre-built ICLR2026 ChromaDB bundle (~1-2GB) into
# the mounted /data volume. Idempotent — skips if the DB is already present.
#
#   docker compose run --rm paperfinder bash download_data.sh
set -euo pipefail

DEST="${PAPERFINDER_DB_PATH:-/data/ICLR2026}"
PARENT="$(dirname "$DEST")"
DRIVE_ID="${PAPERFINDER_DRIVE_ID:-1RTKWZ4qY4X2mW5BorZOrWTOb2fCipIhr}"

mkdir -p "$PARENT"

if [ -e "$DEST/chroma.sqlite3" ]; then
  echo "ChromaDB already present at $DEST — skipping download."
  exit 0
fi

echo "Downloading ICLR2026 ChromaDB bundle..."
cd "$PARENT"
gdown "https://drive.google.com/uc?id=${DRIVE_ID}" -O ICLR2026.zip
unzip -o ICLR2026.zip -d "$PARENT"
rm -f ICLR2026.zip

# Normalize: ensure $DEST holds the chroma DB regardless of the zip's top-level layout.
if [ ! -e "$DEST/chroma.sqlite3" ]; then
  found_db="$(find "$PARENT" -name chroma.sqlite3 -print -quit || true)"
  if [ -n "$found_db" ]; then
    src_dir="$(dirname "$found_db")"
    if [ "$src_dir" != "$DEST" ]; then
      rm -rf "$DEST"
      mv "$src_dir" "$DEST"
    fi
  fi
fi

echo "Done. ChromaDB at: $DEST"
ls -la "$DEST" || true
