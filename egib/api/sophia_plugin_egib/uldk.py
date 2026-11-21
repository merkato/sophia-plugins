"""Działki z ULDK (GUGiK) po identyfikatorze: pobranie, zapis do `egib.dzialka_uldk`, zadania hurtowe.

Parsowanie odpowiedzi i nazwy pól — wspólne z narzędziem pobierania rdzenia (`app.fetchers`).
"""
from __future__ import annotations

import re
import time

import httpx
import psycopg
from psycopg.types.json import Jsonb

from app.fetchers import TIMEOUT, ULDK_URL, FetchError, NotFound, normalize_uldk, parse_uldk

RESULT = ("id", "voivodeship", "county", "commune", "region", "parcel")
ID_RE = re.compile(r"^\d{6}_\d\.\d{4}\.\S+$")
DELAY = 1.0                         # s między zapytaniami w zadaniu hurtowym (ochrona usługi GUGiK)
RETRY_AFTER = 60.0                  # s przed jednym ponowieniem działek, przy których zawiodła usługa powiatu
MAX_BATCH = 5000


class CountyError(FetchError):
    """ULDK odpowiedziało, ale usługa powiatu, o którą pyta, zwróciła błąd (zwykle chwilowy)."""


def valid_id(ident: str) -> bool:
    return bool(ID_RE.match(ident))


def fetch_by_id(ident: str, client: httpx.Client | None = None) -> tuple[dict, str]:
    """→ (pola znormalizowane jak w narzędziu pobierania, EWKT w 2180). NotFound / FetchError."""
    params = {"request": "GetParcelById", "id": ident, "result": ",".join((*RESULT, "geom_wkt")), "srid": "2180"}
    try:
        if client is None:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as c:
                r = c.get(ULDK_URL, params=params)
        else:
            r = client.get(ULDK_URL, params=params)
    except httpx.HTTPError as e:
        raise FetchError("Usługa ULDK nie odpowiada — spróbuj za chwilę") from e
    if r.status_code != 200:
        raise FetchError(f"ULDK: HTTP {r.status_code}")
    try:
        raw = parse_uldk(r.text, RESULT)
    except NotFound as e:
        # ULDK pyta usługę powiatu; jej awaria też daje „brak wyników”, ale z dopiskiem o błędnej odpowiedzi
        if "błędny format" in r.text.lower() or "nie odpowiada" in r.text.lower():
            raise CountyError(f"Usługa powiatu (przez ULDK) zwróciła błąd dla {ident} — spróbuj później") from e
        raise NotFound(f"ULDK nie zna działki {ident}") from e
    ewkt = raw["geom_wkt"]
    if not ewkt.upper().startswith("SRID="):
        ewkt = "SRID=2180;" + ewkt
    props = normalize_uldk(raw)
    if props.get("teryt") != ident:
        raise FetchError(f"ULDK zwróciło inną działkę ({props.get('teryt')}) niż {ident}")
    return props, ewkt


def save(conn, props: dict, ewkt: str, login: str | None) -> None:
    conn.execute(
        """INSERT INTO egib.dzialka_uldk (id_dzialki, numer, obreb, gmina, powiat, wojewodztwo, geom, pobral_login)
           VALUES (%(teryt)s, %(numer)s, %(obreb)s, %(gmina)s, %(powiat)s, %(wojewodztwo)s,
                   ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromEWKT(%(ewkt)s)), 3)), %(login)s)
           ON CONFLICT (id_dzialki) DO UPDATE SET numer = EXCLUDED.numer, obreb = EXCLUDED.obreb, gmina = EXCLUDED.gmina,
                 powiat = EXCLUDED.powiat, wojewodztwo = EXCLUDED.wojewodztwo, geom = EXCLUDED.geom,
                 pobrano = now(), pobral_login = EXCLUDED.pobral_login""",
        {**props, "ewkt": ewkt, "login": login})


def state(conn, ids: list[str]) -> dict[str, dict]:
    """Stan działek: źródło geometrii (EGiB / ULDK) i klucz w widoku `egib.v_dzialka` — dla innych wtyczek."""
    rows = conn.execute("SELECT id_dzialki, klucz, zrodlo FROM egib.v_dzialka WHERE id_dzialki = ANY(%s)", (ids,)).fetchall()
    return {r[0]: {"klucz": r[1], "zrodlo": r[2]} for r in rows}


def _error(ident: str, exc: Exception) -> dict:
    """Błąd jednej działki w zadaniu: typ (powiat / usluga / inny), powiat (4 cyfry TERYT), komunikat."""
    typ = "powiat" if isinstance(exc, CountyError) else "usluga" if isinstance(exc, FetchError) else "inny"
    return {"id": ident, "typ": typ, "powiat": ident[:4], "komunikat": str(exc)}


def run_batch(conninfo: str, job_id: int, login: str | None) -> None:
    """Zadanie hurtowe: kolejne identyfikatory bez geometrii (pomija te, które już są w EGiB albo ULDK).
    Działki, przy których zawiodła usługa powiatu albo ULDK, są raz ponawiane po RETRY_AFTER sekund."""
    with psycopg.connect(conninfo, autocommit=True) as conn, httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        ids = conn.execute("SELECT ids FROM egib.uldk_zadanie WHERE id = %s", (job_id,)).fetchone()[0]
        res = {"pobrane": 0, "bylo": 0, "nie_znaleziono": [], "bledy": [], "ponowienie": False}

        def progress(done: int) -> bool:
            row = conn.execute("UPDATE egib.uldk_zadanie SET zrobione = %s, wynik = %s WHERE id = %s RETURNING status",
                               (done, Jsonb(res), job_id)).fetchone()
            return bool(row and row[0] == "przerwane")

        def one(ident: str) -> None:
            try:
                if state(conn, [ident]):
                    res["bylo"] += 1
                    return
                props, ewkt = fetch_by_id(ident, client)
                save(conn, props, ewkt, login)
                res["pobrane"] += 1
            except NotFound:
                res["nie_znaleziono"].append(ident)
            except Exception as exc:   # błąd jednej działki nie przerywa zadania
                res["bledy"].append(_error(ident, exc))
            time.sleep(DELAY)

        for i, ident in enumerate(ids, start=1):
            one(ident)
            if (i % 5 == 0 or i == len(ids)) and progress(i):
                return
        retry = [e["id"] for e in res["bledy"] if e["typ"] in ("powiat", "usluga")]
        if retry:
            res["ponowienie"] = True
            res["bledy"] = [e for e in res["bledy"] if e["typ"] not in ("powiat", "usluga")]
            if progress(len(ids)):
                return
            time.sleep(RETRY_AFTER)
            for n, ident in enumerate(retry, start=1):
                one(ident)
                if n % 5 == 0 and progress(len(ids)):
                    return
        conn.execute("UPDATE egib.uldk_zadanie SET status = 'zakonczone', zakonczono = now(), zrobione = %s, wynik = %s WHERE id = %s",
                     (len(ids), Jsonb(res), job_id))
