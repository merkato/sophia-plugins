"""Naprawa nakładek aktywnych działek z ULDK (jednorazowo albo okresowo).

    PYTHONPATH=/srv:/plugins/egib/api python -m sophia_plugin_egib.nakladki [--proba]
"""
from __future__ import annotations

import argparse
import json

import httpx
import psycopg

from app.config import get_settings
from app.fetchers import TIMEOUT

from .uldk import repair_overlaps


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--proba", action="store_true", help="wycofaj zmiany (tylko raport)")
    a = ap.parse_args()
    with psycopg.connect(f"service={get_settings().portal_db_service}") as conn, \
            httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        out = repair_overlaps(conn, client)
        if a.proba:
            conn.rollback()
        else:
            conn.commit()
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
