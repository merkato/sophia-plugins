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


class ConflictError(Exception):
    """Działka nachodzi na inną aktywną działkę, a ULDK nie potwierdza, że to ona jest aktualna w tym miejscu."""


SHARE_DUPLICATE = 0.5      # część wspólna ≥ 50% mniejszej działki = to samo miejsce (jedna z nich nieaktualna)


def parcel_at(point: tuple[float, float], client: httpx.Client | None = None) -> str | None:
    """Identyfikator działki, którą ULDK pokazuje w punkcie (EPSG:2180); None przy braku / awarii."""
    params = {"request": "GetParcelByXY", "xy": f"{point[0]:.2f},{point[1]:.2f},2180", "result": "id"}
    try:
        if client is None:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as c:
                r = c.get(ULDK_URL, params=params)
        else:
            r = client.get(ULDK_URL, params=params)
    except httpx.HTTPError:
        return None
    lines = [ln.strip() for ln in r.text.strip().splitlines() if ln.strip()]
    return lines[1].split("|")[0] if r.status_code == 200 and lines and lines[0].startswith("0") and len(lines) > 1 else None


def conflicts(conn, ident: str, geom_sql: str, params: tuple) -> list[dict]:
    """Aktywne działki (GML / ULDK, inny identyfikator) nachodzące na geometrię (> 2 cm)."""
    cur = conn.execute(
        f"""WITH n AS (SELECT {geom_sql} AS g)
            SELECT v.id_dzialki, v.zrodlo, (SELECT pochodzenie FROM egib.dzialka_uldk u WHERE u.id_dzialki = v.id_dzialki) AS pochodzenie,
                   ST_Area(ST_Intersection(v.geom, n.g)) AS wspolne, ST_Area(v.geom) AS pow_inna, ST_Area(n.g) AS pow_nowa,
                   ST_X(ST_PointOnSurface(ST_Intersection(v.geom, n.g))) AS x, ST_Y(ST_PointOnSurface(ST_Intersection(v.geom, n.g))) AS y
              FROM egib.v_dzialka v, n
             WHERE v.id_dzialki <> %s AND egib.nakladka(v.geom, n.g)""", (*params, ident))
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def retire(conn, ident: str, by: str, reason: str) -> None:
    conn.execute("""UPDATE egib.dzialka_uldk SET wycofana = now(), wycofanie_powod = %s, zastapiona_przez = %s
                     WHERE id_dzialki = %s AND wycofana IS NULL""", (reason, by, ident))


def resolve(conn, ident: str, geom_sql: str, params: tuple, pochodzenie: str = "ULDK",
            client: httpx.Client | None = None) -> tuple[str, tuple, list[dict]]:
    """Przed zapisem działki `ident`: rozstrzyga nakładki z aktywnymi działkami. Zwraca (geometria SQL, parametry,
    lista działań). Zasady: działka z GML ma pierwszeństwo; to samo miejsce (≥ 50%) — decyduje ULDK w punkcie
    wspólnej części, nieaktualna jest wycofywana; drobna zakładka na granicy (obie aktualne) — docięcie działki
    o mniejszym znaczeniu (ze starej bazy, a między działkami z ULDK — większej, której ubywa mniej)."""
    actions: list[dict] = []
    for c in conflicts(conn, ident, geom_sql, params):
        if c["zrodlo"] == "EGiB":
            raise ConflictError(f"W tym miejscu jest działka {c['id_dzialki']} z importu GML")
        share = c["wspolne"] / max(min(c["pow_inna"], c["pow_nowa"]), 1e-9)
        if share >= SHARE_DUPLICATE:
            at = parcel_at((c["x"], c["y"]), client)
            if at == ident:
                retire(conn, c["id_dzialki"], ident, f"w ULDK w tym miejscu jest działka {ident}")
                actions.append({"dzialanie": "wycofana", "id": c["id_dzialki"], "zastapiona_przez": ident})
            elif at == c["id_dzialki"]:
                raise ConflictError(f"W ULDK w tym miejscu jest działka {c['id_dzialki']} — {ident} jest nieaktualna")
            else:
                raise ConflictError(f"Nakładka z działką {c['id_dzialki']}: ULDK nie wskazuje w tym miejscu żadnej z nich ({at or 'brak odpowiedzi'})")
        else:
            clip_new = pochodzenie == "migracja" or (c["pochodzenie"] != "migracja" and c["pow_nowa"] >= c["pow_inna"])
            if clip_new:
                geom_sql = f"ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Difference({geom_sql}, (SELECT geom FROM egib.v_dzialka WHERE id_dzialki = %s))), 3))"
                params = (*params, c["id_dzialki"])
                actions.append({"dzialanie": "docieta", "id": ident, "do": c["id_dzialki"], "m2": round(c["wspolne"], 1)})
            else:
                conn.execute(
                    f"""UPDATE egib.dzialka_uldk SET uwagi = concat_ws('; ', uwagi, %s::text),
                               geom = ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Difference(geom, (SELECT g FROM (SELECT {geom_sql} AS g) q))), 3))
                         WHERE id_dzialki = %s""",
                    (f"docięta do {ident} ({round(c['wspolne'], 1)} m², rozbieżność w danych źródłowych)", *params, c["id_dzialki"]))
                actions.append({"dzialanie": "docieta", "id": c["id_dzialki"], "do": ident, "m2": round(c["wspolne"], 1)})
    return geom_sql, params, actions


def save(conn, props: dict, ewkt: str, login: str | None, pochodzenie: str = "ULDK",
         client: httpx.Client | None = None) -> list[dict]:
    """Zapis działki z ULDK po rozstrzygnięciu nakładek (ConflictError, gdy nie da się ustalić aktualnej)."""
    base = "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromEWKT(%s)), 3))"
    geom_sql, params, actions = resolve(conn, props["teryt"], base, (ewkt,), pochodzenie, client)
    note = "; ".join(f"docięta do {a['do']} ({a['m2']} m², rozbieżność w danych źródłowych)"
                     for a in actions if a["dzialanie"] == "docieta" and a["id"] == props["teryt"]) or None
    conn.execute(
        f"""INSERT INTO egib.dzialka_uldk (id_dzialki, numer, obreb, gmina, powiat, wojewodztwo, geom, pobral_login, pochodzenie, uwagi)
            VALUES (%s, %s, %s, %s, %s, %s, {geom_sql}, %s, %s, %s)
            ON CONFLICT (id_dzialki) DO UPDATE SET numer = EXCLUDED.numer, obreb = EXCLUDED.obreb, gmina = EXCLUDED.gmina,
                  powiat = EXCLUDED.powiat, wojewodztwo = EXCLUDED.wojewodztwo, geom = EXCLUDED.geom, pochodzenie = EXCLUDED.pochodzenie,
                  pobrano = now(), pobral_login = EXCLUDED.pobral_login, uwagi = EXCLUDED.uwagi,
                  wycofana = NULL, wycofanie_powod = NULL, zastapiona_przez = NULL""",
        (props["teryt"], props.get("numer"), props.get("obreb"), props.get("gmina"), props.get("powiat"),
         props.get("wojewodztwo"), *params, login, pochodzenie, note))
    return actions


def repair_overlaps(conn, client: httpx.Client | None = None) -> list[dict]:
    """Istniejące nakładki aktywnych działek z ULDK: rozstrzygnięcie parami (jak przy zapisie)."""
    pairs = conn.execute(
        """SELECT a.id_dzialki, b.id_dzialki FROM egib.v_dzialka a JOIN egib.v_dzialka b
             ON a.id_dzialki < b.id_dzialki AND (a.zrodlo = 'ULDK' OR b.zrodlo = 'ULDK') AND egib.nakladka(a.geom, b.geom)
            ORDER BY 1, 2""").fetchall()
    out = []
    for a, b in pairs:
        # „nowa” = działka z ULDK (przy parze GML–ULDK) albo pierwsza z pary; obie muszą być nadal aktywne
        act = dict(conn.execute("SELECT id_dzialki, zrodlo FROM egib.v_dzialka WHERE id_dzialki IN (%s, %s)", (a, b)).fetchall())
        if len(act) < 2:
            continue
        new = b if act[a] == "EGiB" else a
        other = a if new == b else b
        poch = conn.execute("SELECT pochodzenie FROM egib.dzialka_uldk WHERE id_dzialki = %s", (new,)).fetchone()[0]
        conn.execute("SAVEPOINT p")
        try:
            # tymczasowo wycofana, żeby kontrola przy ponownym zapisie geometrii nie widziała jej samej
            geom_sql, params, actions = resolve(conn, new, "(SELECT geom FROM egib.dzialka_uldk WHERE id_dzialki = %s)",
                                                (new,), poch, client)
            if any(x["dzialanie"] == "docieta" and x["id"] == new for x in actions):
                conn.execute(f"UPDATE egib.dzialka_uldk SET geom = {geom_sql}, uwagi = concat_ws('; ', uwagi, %s::text) WHERE id_dzialki = %s",
                             (*params, f"docięta do {other} (rozbieżność w danych źródłowych)", new))
            conn.execute("RELEASE SAVEPOINT p")
            out += [{"para": f"{a} × {b}", **x} for x in actions]
        except (ConflictError, psycopg.Error) as exc:
            conn.execute("ROLLBACK TO SAVEPOINT p")
            msg = str(exc).splitlines()[0]
            if "jest nieaktualna" in msg:      # aktualna jest ta druga — wycofujemy „nową”
                retire(conn, new, other, f"w ULDK w tym miejscu jest działka {other}")
                out.append({"para": f"{a} × {b}", "dzialanie": "wycofana", "id": new, "zastapiona_przez": other})
            else:
                out.append({"para": f"{a} × {b}", "dzialanie": "nierozstrzygnieta", "komunikat": msg})
    return out


def state(conn, ids: list[str]) -> dict[str, dict]:
    """Stan działek: źródło geometrii (EGiB / ULDK) i klucz w widoku `egib.v_dzialka` — dla innych wtyczek."""
    rows = conn.execute("SELECT id_dzialki, klucz, zrodlo FROM egib.v_dzialka WHERE id_dzialki = ANY(%s)", (ids,)).fetchall()
    return {r[0]: {"klucz": r[1], "zrodlo": r[2]} for r in rows}


def _error(ident: str, exc: Exception) -> dict:
    """Błąd jednej działki w zadaniu: typ (powiat / usluga / inny), powiat (4 cyfry TERYT), komunikat."""
    typ = ("powiat" if isinstance(exc, CountyError) else "usluga" if isinstance(exc, FetchError)
           else "konflikt" if isinstance(exc, ConflictError) else "inny")
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
                conn.execute("BEGIN")
                try:
                    save(conn, props, ewkt, login, client=client)
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
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
