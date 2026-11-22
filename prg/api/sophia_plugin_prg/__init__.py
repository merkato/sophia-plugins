"""Wtyczka PRG: granice z Państwowego Rejestru Granic (GUGiK) w schemacie `prg` — obręby ewidencyjne
i nadleśnictwa — z wersjonowaniem i porównaniem z granicami z innego źródła (endpointy /api/plugins/prg/…).

Import: POST /importy (rodzaj, źródło wfs|plik, obszar, zakres) → [plik: PUT /importy/{id}/plik?offset=… →
POST /importy/{id}/wczytaj] → w tle: pobranie, walidacja, podgląd → POST /importy/{id}/zatwierdz albo /odrzuc.
Porównanie: POST /porownania (rodzaj, obszar) — tabela porównywana z ustawień wtyczki → GET /porownania/{id}.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import portal_conn
from app.plugins import data_dir, get_settings_for, require_plugin
from app.security import CurrentUser

from . import importer, porownanie, zrodla

router = APIRouter()
VIEW = require_plugin("prg", "prg.podglad")
IMPORT = require_plugin("prg", "prg.import")
MAX_CHUNK = 16 * 1024 * 1024
MAX_FILE = 3 * 1024 * 1024 * 1024
RODZAJ_RE = "^(obreb|nadlesnictwo)$"
USTAWIENIA = {"obreb": "obreby", "nadlesnictwo": "nadlesnictwa"}       # prefiks ustawień porównania


async def startup() -> None:
    """Zadania przerwane restartem API: wczytywanie od nowa, scalanie wycofała baza."""
    async with portal_conn() as conn:
        await conn.execute("""UPDATE prg.import SET status = 'blad', postep = 'Przerwane restartem serwera — uruchom import ponownie'
                               WHERE status = 'wczytywanie'""")
        await conn.execute("""UPDATE prg.import SET status = 'roboczy',
                                     postep = 'Scalanie przerwane restartem serwera (rejestr bez zmian) — zatwierdź ponownie'
                               WHERE status = 'scalanie'""")
        await conn.execute("""UPDATE prg.porownanie SET status = 'blad', postep = 'Przerwane restartem serwera', zakonczono = now()
                               WHERE status = 'w_toku'""")
        await conn.execute("""UPDATE prg.slownik_stan SET status = CASE WHEN pobrano IS NULL THEN 'blad' ELSE 'gotowy' END,
                                     postep = CASE WHEN pobrano IS NULL THEN 'Przerwane restartem serwera' END
                               WHERE status = 'pobieranie'""")


def _conninfo() -> str:
    return f"service={get_settings().portal_db_service}"


def _file(imp_id: int) -> Path:
    return data_dir("prg") / f"import_{imp_id}.dane"


def _workdir(imp_id: int) -> Path:
    return data_dir("prg") / f"import_{imp_id}"


async def _get(imp_id: int) -> dict:
    async with portal_conn() as conn:
        row = await (await conn.execute("SELECT * FROM prg.import WHERE id = %s", (imp_id,))).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brak importu")
    return row


def _expect(imp: dict, *statuses: str) -> None:
    if imp["status"] not in statuses:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Import w stanie „{imp['status']}” — tej operacji nie można wykonać")


def _start_load(imp_id: int, path: Path | None) -> None:
    threading.Thread(target=importer.load, args=(_conninfo(), imp_id, path, _workdir(imp_id)), daemon=True,
                     name=f"prg-load-{imp_id}").start()


# ---------------------------------------------------------------------------- stan rejestru


@router.get("/rodzaje")
async def kinds(_: CurrentUser = Depends(VIEW)):
    """Rodzaje obiektów: liczba w rejestrze, ostatni zatwierdzony import, czy jest ustawione porównanie."""
    ust = await get_settings_for("prg")
    out = []
    async with portal_conn() as conn:
        for r in zrodla.RODZAJE.values():
            n = await (await conn.execute(f"SELECT count(*) AS n FROM prg.{r.id}")).fetchone()
            last = await (await conn.execute(
                """SELECT id, zatwierdzono, obszar, zakres_typ, zrodlo FROM prg.import
                    WHERE rodzaj = %s AND status = 'zatwierdzony' ORDER BY zatwierdzono DESC LIMIT 1""", (r.id,))).fetchone()
            out.append({"id": r.id, "nazwa": r.nazwa, "obszar_opis": r.obszar_opis, "w_rejestrze": n["n"],
                        "ostatni_import": last, "porownanie": (ust.get(f"{USTAWIENIA[r.id]}_tabela") or "").strip() or None})
    return {"rodzaje": out, "projekt": ust.get("projekt")}


# ---------------------------------------------------------------------------- importy


class ImportIn(BaseModel):
    rodzaj: str = Field(pattern=RODZAJ_RE)
    zrodlo: str = Field(pattern="^(wfs|plik)$")
    obszar: list[str] = Field(default=[], max_length=500)
    zakres_typ: str = Field(pattern="^(pelny|uzupelnienie)$")
    plik: str | None = Field(default=None, max_length=255)
    rozmiar: int | None = Field(default=None, gt=0, le=MAX_FILE)


@router.get("/importy")
async def imports(_: CurrentUser = Depends(IMPORT)):
    async with portal_conn() as conn:
        return await (await conn.execute(
            """SELECT id, rodzaj, zrodlo, plik, rozmiar, obszar, zakres_typ, status, postep, statystyka,
                      utworzyl_nazwa, utworzono, zatwierdzono
                 FROM prg.import ORDER BY id DESC LIMIT 100""")).fetchall()


@router.post("/importy", status_code=201)
async def create_import(body: ImportIn, user: CurrentUser = Depends(IMPORT)):
    try:
        obszar = zrodla.sprawdz_obszar(zrodla.RODZAJE[body.rodzaj], body.obszar)
    except zrodla.ZrodloError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if body.zrodlo == "plik" and not (body.plik and body.rozmiar):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Import z pliku: podaj nazwę i rozmiar pliku")
    async with portal_conn() as conn:
        busy = await (await conn.execute(
            "SELECT id FROM prg.import WHERE rodzaj = %s AND status IN ('wczytywanie', 'scalanie') LIMIT 1", (body.rodzaj,))).fetchone()
        if busy:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Trwa już import tego rodzaju (#{busy['id']})")
        row = await (await conn.execute(
            """INSERT INTO prg.import (rodzaj, zrodlo, plik, rozmiar, obszar, zakres_typ, status, postep, utworzyl, utworzyl_nazwa)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (body.rodzaj, body.zrodlo, body.plik if body.zrodlo == "plik" else None, body.rozmiar if body.zrodlo == "plik" else None,
             obszar, body.zakres_typ, "wysylanie" if body.zrodlo == "plik" else "wczytywanie",
             None if body.zrodlo == "plik" else "W kolejce", user.id, user.full_name or user.login))).fetchone()
    imp_id = row["id"]
    _file(imp_id).unlink(missing_ok=True)
    if body.zrodlo == "wfs":
        _start_load(imp_id, None)
    return {"id": imp_id, "chunk": MAX_CHUNK}


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
            if written > MAX_CHUNK or size + written > (imp["rozmiar"] or 0):
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Za duża część albo plik większy niż zgłoszony")
            out.write(chunk)
    return {"rozmiar": size + written}


@router.post("/importy/{imp_id}/wczytaj")
async def start_load(imp_id: int, _: CurrentUser = Depends(IMPORT)):
    imp = await _get(imp_id)
    _expect(imp, "wysylanie")
    f = _file(imp_id)
    if imp["zrodlo"] != "plik" or not f.exists() or f.stat().st_size != imp["rozmiar"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Plik nie został przesłany w całości")
    # rozszerzenie z nazwy pliku — odczyt (ZIP z SHP / GML) zależy od niego
    suffix = Path(imp["plik"] or "").suffix.lower()
    target = f.with_suffix(suffix if suffix in (".zip", ".gml", ".xml") else ".zip")
    f.rename(target)
    async with portal_conn() as conn:
        await conn.execute("UPDATE prg.import SET status = 'wczytywanie', postep = 'W kolejce' WHERE id = %s", (imp_id,))
    threading.Thread(target=_load_file, args=(imp_id, target), daemon=True, name=f"prg-load-{imp_id}").start()
    return {"ok": True}


def _load_file(imp_id: int, path: Path) -> None:
    try:
        importer.load(_conninfo(), imp_id, path, _workdir(imp_id))
    finally:
        path.unlink(missing_ok=True)


@router.get("/importy/{imp_id}")
async def get_import(imp_id: int, _: CurrentUser = Depends(IMPORT)):
    return await _get(imp_id)


@router.post("/importy/{imp_id}/zatwierdz")
async def approve(imp_id: int, user: CurrentUser = Depends(IMPORT)):
    imp = await _get(imp_id)
    _expect(imp, "roboczy")
    async with portal_conn() as conn:
        await conn.execute("UPDATE prg.import SET status = 'scalanie', postep = 'Scalanie z rejestrem…' WHERE id = %s", (imp_id,))
    threading.Thread(target=importer.merge, args=(_conninfo(), imp_id, user.id), daemon=True, name=f"prg-merge-{imp_id}").start()
    return {"ok": True}


@router.post("/importy/{imp_id}/odrzuc")
async def reject(imp_id: int, _: CurrentUser = Depends(IMPORT)):
    imp = await _get(imp_id)
    _expect(imp, "wysylanie", "roboczy", "blad")
    async with portal_conn() as conn:
        await conn.execute("DELETE FROM prg_staging.obiekt WHERE import_id = %s", (imp_id,))
        await conn.execute("UPDATE prg.import SET status = 'odrzucony', postep = 'Odrzucony' WHERE id = %s", (imp_id,))
    _file(imp_id).unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------------------- rejestr


@router.get("/obiekty/{rodzaj}")
async def search(rodzaj: str = PathParam(pattern=RODZAJ_RE), q: str = Query(min_length=2, max_length=60),
                 _: CurrentUser = Depends(VIEW)):
    """Obiekty bieżące po początku kodu albo fragmencie nazwy."""
    like = q.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    async with portal_conn() as conn:
        return await (await conn.execute(
            f"""SELECT klucz, kod, nazwa, wazny_od, wazna_od, round((ST_Area(geom) / 10000)::numeric, 2) AS pow_ha
                  FROM prg.{rodzaj} WHERE kod LIKE %s OR nazwa ILIKE %s ORDER BY kod LIMIT 100""",
            (like + "%", "%" + like + "%"))).fetchall()


@router.get("/obiekty/{rodzaj}/{klucz:path}")
async def detail(klucz: str, rodzaj: str = PathParam(pattern=RODZAJ_RE), crs: int = 2180, _: CurrentUser = Depends(VIEW)):
    """Obiekt: stan bieżący z geometrią i historia wersji (po identyfikatorze IIP i po kodzie)."""
    async with portal_conn() as conn:
        cur = await (await conn.execute(
            f"""SELECT klucz, wersja_id, wazna_od, import_id, kod, nazwa, wazny_od, atrybuty,
                       round((ST_Area(geom) / 10000)::numeric, 4) AS pow_ha,
                       ST_AsGeoJSON(ST_Transform(geom, %s), 2)::json AS geometry
                  FROM prg.{rodzaj} WHERE klucz = %s""", (crs, klucz))).fetchone()
        kod = cur["kod"] if cur else None
        hist = await (await conn.execute(
            f"""SELECT klucz, wersja_id, kod, nazwa, wazna_od, wazna_do, zamkniecie, import_id, import_zamykajacy,
                       round((ST_Area(geom) / 10000)::numeric, 4) AS pow_ha
                  FROM prg.{rodzaj}_historia WHERE klucz = %s OR kod = %s ORDER BY wazna_od DESC LIMIT 100""",
            (klucz, kod))).fetchall()
    if cur is None and not hist:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brak obiektu w rejestrze")
    return {"biezacy": cur, "historia": hist}


# ---------------------------------------------------------------------------- porównania


class PorownanieIn(BaseModel):
    rodzaj: str = Field(pattern=RODZAJ_RE)
    obszar: list[str] = Field(default=[], max_length=500)


def _tolerancja(v) -> float:
    try:
        t = float(str(v or "1").replace(",", "."))
    except ValueError:
        t = 1.0
    return min(max(t, 0.05), 1000.0)


@router.get("/porownania")
async def comparisons(_: CurrentUser = Depends(VIEW)):
    async with portal_conn() as conn:
        return await (await conn.execute(
            """SELECT id, rodzaj, tabela, pole_kodu, obszar, tolerancja_m, status, postep, statystyka,
                      utworzyl_nazwa, utworzono, zakonczono
                 FROM prg.porownanie ORDER BY id DESC LIMIT 50""")).fetchall()


@router.post("/porownania", status_code=201)
async def compare(body: PorownanieIn, user: CurrentUser = Depends(IMPORT)):
    ust = await get_settings_for("prg")
    pref = USTAWIENIA[body.rodzaj]
    tabela = (ust.get(f"{pref}_tabela") or "").strip()
    pole_kodu = (ust.get(f"{pref}_pole_kodu") or "").strip() or None
    pole_nazwy = (ust.get(f"{pref}_pole_nazwy") or "").strip() or None
    if not tabela:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Administrator nie wskazał tabeli do porównania (Wtyczki → PRG → Ustawienia)")
    try:
        obszar = zrodla.sprawdz_obszar(zrodla.RODZAJE[body.rodzaj], body.obszar)
    except zrodla.ZrodloError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    def check():
        with psycopg.connect(_conninfo()) as conn:
            porownanie.tabela_zrodla(conn, tabela, pole_kodu, pole_nazwy)
    try:
        await asyncio.to_thread(check)
    except porownanie.PorownanieError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    async with portal_conn() as conn:
        busy = await (await conn.execute("SELECT id FROM prg.porownanie WHERE status = 'w_toku' LIMIT 1")).fetchone()
        if busy:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Trwa już porównanie #{busy['id']}")
        row = await (await conn.execute(
            """INSERT INTO prg.porownanie (rodzaj, tabela, pole_kodu, pole_nazwy, obszar, tolerancja_m, postep, utworzyl_nazwa)
               VALUES (%s, %s, %s, %s, %s, %s, 'W kolejce', %s) RETURNING id""",
            (body.rodzaj, tabela, pole_kodu, pole_nazwy, obszar, _tolerancja(ust.get("tolerancja_m")),
             user.full_name or user.login))).fetchone()
    threading.Thread(target=porownanie.run, args=(_conninfo(), row["id"]), daemon=True, name=f"prg-por-{row['id']}").start()
    return {"id": row["id"]}


@router.get("/porownania/{por_id}")
async def comparison(por_id: int, stan: str | None = Query(default=None, pattern="^(zgodne|roznica|tylko_prg|tylko_porownywane)$"),
                     _: CurrentUser = Depends(VIEW)):
    async with portal_conn() as conn:
        p = await (await conn.execute("SELECT * FROM prg.porownanie WHERE id = %s", (por_id,))).fetchone()
        if p is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Brak porównania")
        wyniki = await (await conn.execute(
            """SELECT nr, stan, prg_kod, prg_nazwa, por_id, por_kod, por_nazwa, pow_prg_ha, pow_por_ha,
                      tylko_prg_ha, tylko_por_ha, max_szer_m,
                      (SELECT count(*) FROM prg.porownanie_roznica r WHERE r.porownanie_id = w.porownanie_id AND r.nr = w.nr) AS roznice
                 FROM prg.porownanie_wynik w
                WHERE porownanie_id = %s AND (%s::text IS NULL OR stan = %s)
                ORDER BY CASE stan WHEN 'roznica' THEN 0 WHEN 'tylko_prg' THEN 1 WHEN 'tylko_porownywane' THEN 2 ELSE 3 END,
                         max_szer_m DESC NULLS LAST, nr
                LIMIT 5000""", (por_id, stan, stan))).fetchall()
    return {"porownanie": p, "wyniki": wyniki}


@router.get("/porownania/{por_id}/mapa/{nr}")
async def comparison_map(por_id: int, nr: int, crs: int = 4326, _: CurrentUser = Depends(VIEW)):
    """Geometria do podglądu na mapie: rozbieżności pary, a gdy ich nie ma — obiekt PRG albo porównywany."""
    async with portal_conn() as conn:
        w = await (await conn.execute("""SELECT w.*, p.rodzaj FROM prg.porownanie_wynik w JOIN prg.porownanie p ON p.id = w.porownanie_id
                                          WHERE w.porownanie_id = %s AND w.nr = %s""", (por_id, nr))).fetchone()
        if w is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Brak wyniku")
        g = await (await conn.execute(
            """SELECT ST_AsGeoJSON(ST_Transform(ST_Collect(geom), %s), 7)::json AS g
                 FROM prg.porownanie_roznica WHERE porownanie_id = %s AND nr = %s""", (crs, por_id, nr))).fetchone()
        if g["g"] is None and w["prg_klucz"]:
            g = await (await conn.execute(
                f"SELECT ST_AsGeoJSON(ST_Transform(geom, %s), 7)::json AS g FROM prg.{w['rodzaj']} WHERE klucz = %s",
                (crs, w["prg_klucz"]))).fetchone()
    if not g or g["g"] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brak geometrii do pokazania")
    return {"geometry": g["g"], "label": f"{w['prg_kod'] or w['por_kod']} {w['prg_nazwa'] or w['por_nazwa'] or ''}".strip()}


@router.delete("/porownania/{por_id}")
async def comparison_delete(por_id: int, _: CurrentUser = Depends(IMPORT)):
    async with portal_conn() as conn:
        await conn.execute("DELETE FROM prg.porownanie WHERE id = %s AND status <> 'w_toku'", (por_id,))
    return {"ok": True}


# ---------------------------------------------------------------------------- słownik jednostek (wybór obszaru po nazwie)


def _load_dictionary() -> None:
    """Wątek: listy jednostek z usługi WFS PRG (bez geometrii, kilka minut) → prg.jednostka."""
    with psycopg.connect(_conninfo()) as conn:
        try:
            for i, poziom in enumerate(zrodla.SLOWNIK_WARSTWY, 1):
                conn.execute("UPDATE prg.slownik_stan SET postep = %s",
                             (f"Pobieranie listy {i} z {len(zrodla.SLOWNIK_WARSTWY)}…",))
                conn.commit()
                rows = zrodla.pobierz_slownik(poziom)
                conn.execute("DELETE FROM prg.jednostka WHERE poziom = %s", (poziom,))
                with conn.cursor() as cur:
                    cur.executemany("INSERT INTO prg.jednostka (poziom, kod, nazwa) VALUES (%s, %s, %s)",
                                    [(poziom, k, n) for k, n in rows])
                conn.commit()
            conn.execute("UPDATE prg.slownik_stan SET status = 'gotowy', postep = NULL, pobrano = now()")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            msg = str(exc) if isinstance(exc, zrodla.ZrodloError) else f"Błąd: {exc}"
            conn.execute("UPDATE prg.slownik_stan SET status = 'blad', postep = %s", (msg,))
            conn.commit()


async def _start_dictionary() -> bool:
    """Uruchamia pobieranie słownika, jeśli nie trwa (wiersz stanu jako blokada)."""
    async with portal_conn() as conn:
        row = await (await conn.execute(
            """INSERT INTO prg.slownik_stan (status, postep) VALUES ('pobieranie', 'W kolejce')
               ON CONFLICT (id) DO UPDATE SET status = 'pobieranie', postep = 'W kolejce'
                WHERE prg.slownik_stan.status <> 'pobieranie'
               RETURNING id""")).fetchone()
    if row:
        threading.Thread(target=_load_dictionary, daemon=True, name="prg-slownik").start()
    return bool(row)


@router.get("/obszary")
async def areas(_: CurrentUser = Depends(VIEW)):
    """Jednostki do wyboru obszaru: obręby — województwo → powiat → jednostka ewidencyjna; nadleśnictwa —
    RDLP → nadleśnictwo. Przy pierwszym użyciu lista jest pobierana z usługi PRG w tle."""
    async with portal_conn() as conn:
        st = await (await conn.execute("SELECT status, postep, pobrano FROM prg.slownik_stan")).fetchone()
        if st is None:
            await _start_dictionary()
            st = {"status": "pobieranie", "postep": "W kolejce", "pobrano": None}
        rows = await (await conn.execute("SELECT poziom, kod, nazwa FROM prg.jednostka ORDER BY poziom, kod")).fetchall()
    out: dict[str, list] = {p: [] for p in zrodla.SLOWNIK_WARSTWY}
    for r in rows:
        out[r["poziom"]].append([r["kod"], r["nazwa"]])
    return {"stan": st, "poziomy": zrodla.POZIOMY, "jednostki": out}


@router.post("/obszary/odswiez")
async def areas_refresh(_: CurrentUser = Depends(IMPORT)):
    if not await _start_dictionary():
        raise HTTPException(status.HTTP_409_CONFLICT, "Lista jednostek jest właśnie pobierana")
    return {"ok": True}
