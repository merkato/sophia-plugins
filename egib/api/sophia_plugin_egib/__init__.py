"""Wtyczka EGiB: import GML z wersjonowaniem i rejestr (endpointy pod /api/plugins/egib/…).

Przebieg importu: POST /importy (nazwa, rozmiar, zakres, warstwy) → PUT /importy/{id}/plik?offset=…
(plik w częściach — proxy Sophii ogranicza pojedyncze żądanie) → [POST /importy/{id}/klauzula] →
POST /importy/{id}/wczytaj (w tle: walidacja, podgląd) → [PUT /importy/{id}/zakres] →
POST /importy/{id}/zatwierdz (w tle: scalanie w jednej transakcji) albo POST /importy/{id}/odrzuc.

Działki z ULDK (uprawnienie egib.uldk): GET /uldk/{id} (podgląd), POST /uldk (zapis), POST /uldk/zadania
(pobieranie hurtowe w tle) i GET /uldk/zadania/{id}. POST /stan — źródło geometrii działek (dla innych wtyczek).
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import portal_conn
from app.fetchers import FetchError, NotFound, limiter
from app.plugins import data_dir, require_plugin
from app.security import CurrentUser

from . import importer, parser, uldk

router = APIRouter()
VIEW = require_plugin("egib", "egib.podglad")
IMPORT = require_plugin("egib", "egib.import")
ULDK = require_plugin("egib", "egib.uldk")
MAX_CHUNK = 16 * 1024 * 1024
MAX_FILE = 4 * 1024 * 1024 * 1024
WARSTWY = ("jednostka", "obreb", "dzialka", "uzytek", "punkt")


async def startup() -> None:
    """Wywoływane przez Sophię przy starcie API (wtyczka włączona): zadania przerwane restartem.
    Wczytywanie trzeba powtórzyć od wysłania pliku; scalanie wycofała baza — import wraca do zatwierdzenia."""
    async with portal_conn() as conn:
        await conn.execute("""UPDATE egib.import SET status = 'blad',
                                   postep = 'Przerwane restartem serwera — wyślij plik ponownie'
                             WHERE status = 'wczytywanie'""")
        await conn.execute("""UPDATE egib.import SET status = 'roboczy',
                                   postep = 'Scalanie przerwane restartem serwera (rejestr bez zmian) — zatwierdź ponownie'
                             WHERE status = 'scalanie'""")
        await conn.execute("UPDATE egib.uldk_zadanie SET status = 'przerwane', zakonczono = now() WHERE status = 'w_toku'")


def _conninfo() -> str:
    return f"service={get_settings().portal_db_service}"


def _file(imp_id: int) -> Path:
    return data_dir("egib") / f"import_{imp_id}.gml"


async def _get(imp_id: int) -> dict:
    async with portal_conn() as conn:
        row = await (await conn.execute("SELECT * FROM egib.import WHERE id = %s", (imp_id,))).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brak importu")
    return row


def _expect(imp: dict, *statuses: str) -> None:
    if imp["status"] not in statuses:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Import w stanie „{imp['status']}” — tej operacji nie można wykonać")


# ---------------------------------------------------------------------------- importy


class ImportIn(BaseModel):
    plik: str = Field(min_length=1, max_length=255)
    rozmiar: int = Field(gt=0, le=MAX_FILE)
    zakres_typ: str = Field(pattern="^(obreb|lista_dzialek)$")
    warstwy: list[str] = ["jednostka", "obreb", "dzialka", "uzytek"]


@router.get("/importy")
async def imports(_: CurrentUser = Depends(IMPORT)):
    async with portal_conn() as conn:
        return await (await conn.execute(
            """SELECT id, plik, rozmiar, status, postep, zakres_typ, system_zrodlowy, klauzula->>'organProwadzacyZasob' AS organ,
                      statystyka, utworzyl_nazwa, utworzono, zatwierdzono
                 FROM egib.import ORDER BY id DESC LIMIT 100""")).fetchall()


@router.post("/importy", status_code=201)
async def create_import(body: ImportIn, user: CurrentUser = Depends(IMPORT)):
    bad = set(body.warstwy) - set(WARSTWY)
    if bad or "dzialka" not in body.warstwy:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Warstwy: jednostka, obreb, dzialka (wymagana), uzytek, punkt")
    async with portal_conn() as conn:
        row = await (await conn.execute(
            """INSERT INTO egib.import (plik, rozmiar, zakres_typ, warstwy, utworzyl, utworzyl_nazwa)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (body.plik, body.rozmiar, body.zakres_typ, body.warstwy, user.id, user.full_name or user.login))).fetchone()
    _file(row["id"]).unlink(missing_ok=True)
    return {"id": row["id"], "chunk": MAX_CHUNK}


@router.put("/importy/{imp_id}/plik")
async def upload_chunk(imp_id: int, request: Request, offset: int = Query(ge=0), _: CurrentUser = Depends(IMPORT)):
    """Kolejna część pliku (surowe bajty w treści żądania), dopisywana od `offset`."""
    imp = await _get(imp_id)
    _expect(imp, "wysylanie")
    f = _file(imp_id)
    size = f.stat().st_size if f.exists() else 0
    if offset != size:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Oczekiwano części od bajtu {size}")
    written = 0
    with open(f, "ab") as out:
        async for chunk in request.stream():
            written += len(chunk)
            if written > MAX_CHUNK or size + written > imp["rozmiar"]:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Za duża część albo plik większy niż zgłoszony")
            out.write(chunk)
    return {"rozmiar": size + written}


@router.post("/importy/{imp_id}/klauzula")
async def upload_klauzula(imp_id: int, plik: UploadFile = File(...), _: CurrentUser = Depends(IMPORT)):
    imp = await _get(imp_id)
    _expect(imp, "wysylanie")
    data = await plik.read(1024 * 1024)
    try:
        kl = parser.read_klauzula(data)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Nie da się odczytać klauzuli: {exc}") from exc
    import json
    async with portal_conn() as conn:
        await conn.execute("UPDATE egib.import SET klauzula = %s::jsonb WHERE id = %s", (json.dumps(kl), imp_id))
    return kl


@router.post("/importy/{imp_id}/wczytaj")
async def start_load(imp_id: int, _: CurrentUser = Depends(IMPORT)):
    imp = await _get(imp_id)
    _expect(imp, "wysylanie")
    f = _file(imp_id)
    if not f.exists() or f.stat().st_size != imp["rozmiar"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Plik nie został przesłany w całości")
    async with portal_conn() as conn:
        await conn.execute("UPDATE egib.import SET status = 'wczytywanie', postep = 'W kolejce' WHERE id = %s", (imp_id,))
    threading.Thread(target=importer.load, args=(_conninfo(), imp_id, f), daemon=True, name=f"egib-load-{imp_id}").start()
    return {"ok": True}


@router.get("/importy/{imp_id}")
async def get_import(imp_id: int, _: CurrentUser = Depends(IMPORT)):
    return await _get(imp_id)


class ZakresIn(BaseModel):
    zakres_typ: str = Field(pattern="^(obreb|lista_dzialek)$")
    obreby: list[str] = []
    dzialki: list[str] = []


@router.put("/importy/{imp_id}/zakres")
async def set_scope(imp_id: int, body: ZakresIn, _: CurrentUser = Depends(IMPORT)):
    """Zakres importu przed zatwierdzeniem: pełne obręby (zamykanie znikniętych) albo lista działek."""
    imp = await _get(imp_id)
    _expect(imp, "roboczy")
    obreby = sorted({o.strip() for o in body.obreby if o.strip()})
    dzialki = sorted({d.strip() for d in body.dzialki if d.strip()})
    import psycopg  # podgląd liczy kod synchroniczny (ten sam co scalanie)

    def run():
        with psycopg.connect(_conninfo()) as conn:
            conn.execute("UPDATE egib.import SET zakres_typ = %s, zakres_obreby = %s, zakres_dzialki = %s WHERE id = %s",
                         (body.zakres_typ, obreby, dzialki, imp_id))
            stats = importer.preview(conn, imp_id)
            from psycopg.types.json import Jsonb
            conn.execute("UPDATE egib.import SET statystyka = %s WHERE id = %s", (Jsonb(stats), imp_id))
            conn.commit()
            return stats
    import asyncio
    return await asyncio.to_thread(run)


@router.post("/importy/{imp_id}/zatwierdz")
async def approve(imp_id: int, user: CurrentUser = Depends(IMPORT)):
    imp = await _get(imp_id)
    _expect(imp, "roboczy")
    async with portal_conn() as conn:
        await conn.execute("UPDATE egib.import SET status = 'scalanie', postep = 'Scalanie z rejestrem…' WHERE id = %s", (imp_id,))
    threading.Thread(target=_approve, args=(imp_id, user.id), daemon=True, name=f"egib-merge-{imp_id}").start()
    return {"ok": True}


def _approve(imp_id: int, user_id: int) -> None:
    importer.merge(_conninfo(), imp_id, user_id)
    _file(imp_id).unlink(missing_ok=True)


@router.post("/importy/{imp_id}/odrzuc")
async def reject(imp_id: int, _: CurrentUser = Depends(IMPORT)):
    imp = await _get(imp_id)
    _expect(imp, "wysylanie", "roboczy", "blad")
    async with portal_conn() as conn:
        await conn.execute("DELETE FROM egib_staging.obiekt WHERE import_id = %s", (imp_id,))
        await conn.execute("UPDATE egib.import SET status = 'odrzucony', postep = 'Odrzucony' WHERE id = %s", (imp_id,))
    _file(imp_id).unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------------------- rejestr


@router.get("/dzialki")
async def search(q: str = Query(min_length=3, max_length=60), _: CurrentUser = Depends(VIEW)):
    """Działki bieżące po identyfikatorze (początek, np. „143801_1.0008.” albo cały numer)."""
    async with portal_conn() as conn:
        return await (await conn.execute(
            """SELECT klucz, id_dzialki, pole_ewid_ha, wazna_od, ST_Area(geom) AS pow_geom_m2
                 FROM egib.dzialka WHERE id_dzialki LIKE %s ORDER BY id_dzialki LIMIT 50""",
            (q.replace("%", "").replace("_", r"\_") + "%",))).fetchall()


@router.get("/dzialki/{klucz:path}")
async def parcel(klucz: str, crs: int = 2180, _: CurrentUser = Depends(VIEW)):
    """Działka: stan bieżący z użytkami, punkty graniczne, historia wersji i zmiany EGiB."""
    async with portal_conn() as conn:
        cur = await (await conn.execute(
            """SELECT d.klucz, d.id_dzialki, d.id_obrebu, o.nazwa AS obreb, d.numer, d.pole_ewid_ha, d.dokladnosc_pola,
                      d.dodatkowe_info, d.wersja_id, d.obiekt_od, d.wersja_od, d.wazna_od, d.import_id,
                      round(ST_Area(d.geom)::numeric / 10000, 4) AS pow_geom_ha,
                      ST_AsGeoJSON(ST_Transform(d.geom, %s), 2)::json AS geometry,
                      z.nr_zmiany, z.opis AS zmiana_opis, v.uzytki, v.udzial_ls,
                      (SELECT count(*) FROM egib.dzialka_punkt p WHERE p.dzialka_klucz = d.klucz) AS punkty
                 FROM egib.dzialka d LEFT JOIN egib.obreb o ON o.id_obrebu = d.id_obrebu
                 LEFT JOIN egib.zmiana z ON z.klucz = d.zmiana_klucz
                 LEFT JOIN egib.v_dzialka_uzytki v ON v.klucz = d.klucz
                WHERE d.klucz = %s""", (crs, klucz))).fetchone()
        hist = await (await conn.execute(
            """SELECT h.wersja_id, h.wazna_od, h.wazna_do, h.zamkniecie, h.pole_ewid_ha, h.uzytki,
                      round(ST_Area(h.geom)::numeric / 10000, 4) AS pow_geom_ha, z.nr_zmiany, z.opis AS zmiana_opis
                 FROM egib.dzialka_historia h LEFT JOIN egib.zmiana z ON z.klucz = h.zmiana_klucz
                WHERE h.klucz = %s ORDER BY h.wazna_od DESC""", (klucz,))).fetchall()
    if cur is None and not hist:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brak działki w rejestrze")
    return {"biezaca": cur, "historia": hist}


class ZmianyIn(BaseModel):
    id_dzialki: list[str] = Field(default=[], max_length=2000)
    klucze: list[str] = Field(default=[], max_length=2000)
    od: datetime


@router.post("/zmiany")
async def changes(body: ZmianyIn, _: CurrentUser = Depends(VIEW)):
    """Raport zmian dla listy działek (np. działek spraw innej wtyczki) od podanej chwili:
    stan bieżący, wersje zamknięte od tej chwili i oznaczenia użytków wtedy i dziś."""
    async with portal_conn() as conn:
        rows = await (await conn.execute(
            """WITH k AS (
                 SELECT klucz FROM egib.dzialka WHERE klucz = ANY(%(k)s) OR id_dzialki = ANY(%(i)s)
                 UNION SELECT klucz FROM egib.dzialka_historia WHERE klucz = ANY(%(k)s) OR id_dzialki = ANY(%(i)s))
               SELECT k.klucz,
                      d.id_dzialki AS id_dzialki_dzis, d.wazna_od AS biezaca_od,
                      (SELECT array_agg(DISTINCT du.ofu) FROM egib.dzialka_uzytek du WHERE du.dzialka_klucz = k.klucz) AS ofu_dzis,
                      (SELECT jsonb_agg(jsonb_build_object('wazna_od', h.wazna_od, 'wazna_do', h.wazna_do,
                              'zamkniecie', h.zamkniecie, 'id_dzialki', h.id_dzialki, 'pole_ewid_ha', h.pole_ewid_ha,
                              'ofu', (SELECT jsonb_agg(DISTINCT e->>'ofu') FROM jsonb_array_elements(coalesce(h.uzytki, '[]')) e))
                              ORDER BY h.wazna_od)
                         FROM egib.dzialka_historia h WHERE h.klucz = k.klucz AND h.wazna_do >= %(od)s) AS wersje
                 FROM k LEFT JOIN egib.dzialka d ON d.klucz = k.klucz""",
            {"k": body.klucze, "i": body.id_dzialki, "od": body.od})).fetchall()
    return [{**r, "zamknieta": r["id_dzialki_dzis"] is None, "zmieniona": bool(r["wersje"])} for r in rows]


# ---------------------------------------------------------------------------- ULDK


class StanIn(BaseModel):
    ids: list[str] = Field(max_length=10000)


@router.post("/stan")
async def parcels_state(body: StanIn, _: CurrentUser = Depends(require_plugin("egib"))):
    """Dla listy identyfikatorów: czy działka ma geometrię i skąd (EGiB / ULDK), klucz w widoku egib.v_dzialka."""
    import psycopg

    def run():
        with psycopg.connect(_conninfo()) as conn:
            return uldk.state(conn, body.ids)
    import asyncio
    return await asyncio.to_thread(run)


def _check_id(ident: str) -> str:
    ident = ident.strip()
    if not uldk.valid_id(ident):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Niepoprawny identyfikator działki: {ident}")
    return ident


async def _fetch(ident: str, user: CurrentUser):
    import asyncio
    if not limiter.check(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Za dużo zapytań do ULDK — odczekaj chwilę")
    try:
        return await asyncio.to_thread(uldk.fetch_by_id, ident)
    except NotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except FetchError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


class UldkIn(BaseModel):
    id_dzialki: str = Field(min_length=10, max_length=80)


@router.post("/uldk", status_code=201)
async def uldk_add(body: UldkIn, user: CurrentUser = Depends(ULDK)):
    """Pobranie działki z ULDK i zapis w egib.dzialka_uldk (ponowne pobranie odświeża geometrię)."""
    ident = _check_id(body.id_dzialki)
    async with portal_conn() as conn:
        egib_row = await (await conn.execute("SELECT 1 FROM egib.dzialka WHERE id_dzialki = %s", (ident,))).fetchone()
    if egib_row:
        raise HTTPException(status.HTTP_409_CONFLICT, "Działka jest już w rejestrze z importu EGiB")
    props, ewkt = await _fetch(ident, user)
    import psycopg

    def run():
        with psycopg.connect(_conninfo()) as conn:
            uldk.save(conn, props, ewkt, user.login)
            conn.commit()
    import asyncio
    await asyncio.to_thread(run)
    return {"id_dzialki": ident, "klucz": f"ULDK:{ident}", "wlasciwosci": props}


class ZadanieIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=uldk.MAX_BATCH)


@router.post("/uldk/zadania", status_code=201)
async def uldk_batch(body: ZadanieIn, user: CurrentUser = Depends(ULDK)):
    """Pobieranie hurtowe w tle (około 1 działki na sekundę); pomija działki, które już mają geometrię."""
    ids = list(dict.fromkeys(i.strip() for i in body.ids if i.strip()))
    bad = [i for i in ids if not uldk.valid_id(i)]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Niepoprawne identyfikatory: {', '.join(bad[:10])}")
    async with portal_conn() as conn:
        busy = await (await conn.execute("SELECT id FROM egib.uldk_zadanie WHERE status = 'w_toku' LIMIT 1")).fetchone()
        if busy:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Trwa już pobieranie (zadanie #{busy['id']})")
        job = await (await conn.execute(
            "INSERT INTO egib.uldk_zadanie (ids, utworzyl_login) VALUES (%s, %s) RETURNING id", (ids, user.login))).fetchone()
    threading.Thread(target=uldk.run_batch, args=(_conninfo(), job["id"], user.login), daemon=True,
                     name=f"egib-uldk-{job['id']}").start()
    return {"id": job["id"], "liczba": len(ids)}


@router.get("/uldk/zadania/{job_id}")
async def uldk_job(job_id: int, _: CurrentUser = Depends(ULDK)):
    async with portal_conn() as conn:
        row = await (await conn.execute(
            """SELECT id, status, cardinality(ids) AS liczba, zrobione, wynik, utworzyl_login, utworzono, zakonczono
                 FROM egib.uldk_zadanie WHERE id = %s""", (job_id,))).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brak zadania")
    return row


@router.post("/uldk/zadania/{job_id}/przerwij")
async def uldk_job_stop(job_id: int, _: CurrentUser = Depends(ULDK)):
    async with portal_conn() as conn:
        await conn.execute("UPDATE egib.uldk_zadanie SET status = 'przerwane', zakonczono = now() WHERE id = %s AND status = 'w_toku'",
                           (job_id,))
    return {"ok": True}


# po /uldk/zadania/… — ścieżka {ident:path} łapałaby też je
@router.get("/uldk/{ident:path}")
async def uldk_preview(ident: str, crs: int = 4326, user: CurrentUser = Depends(ULDK)):
    """Podgląd działki z ULDK (bez zapisu) i informacja, czy już jest w rejestrze."""
    ident = _check_id(ident)
    props, ewkt = await _fetch(ident, user)
    async with portal_conn() as conn:
        row = await (await conn.execute(
            """SELECT ST_AsGeoJSON(ST_Transform(ST_GeomFromEWKT(%s), %s), 7)::json AS geometry,
                      round(ST_Area(ST_GeomFromEWKT(%s))::numeric / 10000, 4) AS pow_geom_ha,
                      (SELECT zrodlo FROM egib.v_dzialka WHERE id_dzialki = %s LIMIT 1) AS w_rejestrze""",
            (ewkt, crs, ewkt, ident))).fetchone()
    return {"wlasciwosci": props, **row}
