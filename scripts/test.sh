#!/bin/sh
# Testy i lint wszystkich wtyczek w obrazie API Sophii (te same biblioteki co na serwerze).
#   SOPHIA=/ścieżka/do/sophia ./scripts/test.sh          (domyślnie ../sophia)
# Kod rdzenia (pakiet `app`) bierze z $SOPHIA/api, więc testuje wtyczki z bieżącą wersją Sophii.
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
sophia=${SOPHIA:-$here/../sophia}
paths=$(for d in "$here"/*/api; do printf ':/p/%s' "$(basename "$(dirname "$d")")/api"; done)
docker run --rm -v "$here":/p -v "$sophia/api":/srv/sophia:ro -w /p \
  -e PYTHONPATH="/srv/sophia${paths}" sophia-api sh -c '
    pip install -q pytest ruff >/dev/null 2>&1
    python -m pytest */tests
    ruff check .'
