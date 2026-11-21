"""Nazwy obrębów i gmin dla działek uzupełnień bez nazw (np. przeniesionych ze starej bazy) — z ULDK
(GetRegionById / GetCommuneById, dane PRG — niezależne od usług powiatów).

    PYTHONPATH=/srv:/plugins/egib/api python -m sophia_plugin_egib.nazwy
"""
from __future__ import annotations

import json
import time

import httpx
import psycopg

from app.config import get_settings
from app.fetchers import TIMEOUT, ULDK_URL

from .uldk import DELAY


def _name(client: httpx.Client, request: str, ident: str, field: str) -> str | None:
    r = client.get(ULDK_URL, params={"request": request, "id": ident, "result": field})
    lines = [ln.strip() for ln in r.text.strip().splitlines() if ln.strip()]
    return lines[1] if r.status_code == 200 and lines and lines[0].startswith("0") and len(lines) > 1 else None


def fill_names(conn) -> dict:
    out = {"obreby": 0, "gminy": 0, "nieudane": []}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        for (ob,) in conn.execute("SELECT DISTINCT id_obrebu FROM egib.dzialka_uldk WHERE obreb IS NULL AND id_obrebu IS NOT NULL").fetchall():
            name = _name(client, "GetRegionById", ob, "region")
            if name:
                out["obreby"] += conn.execute("UPDATE egib.dzialka_uldk SET obreb = %s WHERE id_obrebu = %s AND obreb IS NULL",
                                              (name, ob)).rowcount
            else:
                out["nieudane"].append(ob)
            time.sleep(DELAY / 2)
        for (gm,) in conn.execute("SELECT DISTINCT split_part(id_dzialki, '.', 1) FROM egib.dzialka_uldk WHERE gmina IS NULL").fetchall():
            name = _name(client, "GetCommuneById", gm, "commune")
            if name:
                out["gminy"] += conn.execute("UPDATE egib.dzialka_uldk SET gmina = %s WHERE split_part(id_dzialki, '.', 1) = %s AND gmina IS NULL",
                                             (name, gm)).rowcount
            else:
                out["nieudane"].append(gm)
            time.sleep(DELAY / 2)
    return out


if __name__ == "__main__":
    with psycopg.connect(f"service={get_settings().portal_db_service}") as c:
        res = fill_names(c)
        c.commit()
    print(json.dumps(res, ensure_ascii=False))
