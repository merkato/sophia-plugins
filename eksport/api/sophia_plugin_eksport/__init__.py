"""Wtyczka „Eksport danych”: pobieranie warstw projektu i zestawień innych wtyczek do XLSX, CSV, GeoPackage i SHP (mLas).

* Warstwy projektu — administrator wskazuje warstwy (i pola) dostępne do pobrania w projekcie. Zakres: cały
  albo bieżący widok mapy. Co trafia do pliku, rozstrzyga rdzeń Sophii tym samym warunkiem co dla mapy: prawo
  podglądu warstwy, filtr warstwy z projektu QGIS, zakres danych użytkownika (`app.scope.row_filter`).
* Zestawienia — inne wtyczki udostępniają je funkcjami w pakiecie API (umowa poniżej) i same stosują swój zakres:
      async def eksport_zestawienia(user) -> list[dict]
          [{"id", "tytul", "przestrzenne": bool, "parametry": [{"id", "etykieta", "opcje": [{"wartosc", "etykieta"}]}]}]
      async def eksport_dane(id, user, parametry: dict, fmt: str) -> list[dict]
          [{"nazwa", "kolumny": [str], "wiersze": [tuple], "geometria": [WKB] | None, "typy_geom": [str], "srid": int}]
* Paczka mLas — tylko warstwy przestrzenne, SHP w EPSG:2180, kodowanie z ustawień wtyczki, w archiwum ZIP.
Każde pobranie trafia do dziennika (kto, co, kiedy, ile wierszy).

Endpointy pod /api/plugins/eksport/…:
  GET  /zrodla?projekt=              — warstwy projektu do pobrania, zestawienia wtyczek, formaty
  POST /warstwy                      — plik z warstw projektu {projekt, warstwy, format, zakres, bbox?, crs?}
  POST /zestawienia/{wtyczka.id}     — plik z zestawienia {format, parametry}
  GET  /konfiguracja/{projekt}; PUT  — warstwy i pola (eksport.konfiguracja)
  GET  /dziennik                     — ostatnie pobrania (eksport.konfiguracja)
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from psycopg import sql
from pydantic import BaseModel, Field

from app.columns import column_info
from app.db import layer_conn, portal_conn
from app.permissions import load_project_context
from app.plugins import REGISTRY, data_dir, enabled_ids, get_settings_for, require_plugin
from app.qgsproject import FieldDef, LayerDef
from app.scope import T, col, row_filter, table
from app.security import CurrentUser

from .zapis import FORMATY, Tabela, ascii_nazwa, zapisz

log = logging.getLogger("sophia.eksport")
router = APIRouter()
POBIERZ = require_plugin("eksport", "eksport.pobieranie")
KONFIG = require_plugin("eksport", "eksport.konfiguracja")
MAX_WIERSZY = 200_000
FORMAT_RE = "^(xlsx|csv|gpkg|mlas)$"


# ---------------------------------------------------------------------------- konfiguracja i źródła

async def _konfiguracja(projekt: str) -> list[dict]:
    async with portal_conn() as conn:
        return await (await conn.execute(
            """SELECT layer_id, pola, kolejnosc, zmieniono, zmienil FROM eksport.warstwa
                WHERE projekt = %s ORDER BY kolejnosc, layer_id""", (projekt,))).fetchall()


async def _dostawcy() -> list[tuple[str, object]]:
    """Włączone wtyczki z funkcjami zestawień (umowa w opisie modułu)."""
    aktywne = await enabled_ids()
    return [(pid, p.module) for pid, p in REGISTRY.items()
            if pid in aktywne and not p.error and p.module is not None
            and hasattr(p.module, "eksport_zestawienia") and hasattr(p.module, "eksport_dane")]


async def _zestawienia(user: CurrentUser) -> list[dict]:
    out = []
    for pid, mod in await _dostawcy():
        try:
            for z in await mod.eksport_zestawienia(user):
                out.append({**z, "id": f"{pid}.{z['id']}", "wtyczka": pid})
        except HTTPException:
            continue        # brak uprawnień w tej wtyczce
        except Exception:
            log.exception("Zestawienia wtyczki %s", pid)
    return out


@router.get("/zrodla")
async def sources(projekt: str | None = None, user: CurrentUser = Depends(POBIERZ)):
    """Co użytkownik może pobrać: warstwy skonfigurowane w projekcie (z prawem podglądu) i zestawienia wtyczek."""
    warstwy = []
    if projekt:
        ctx = await load_project_context(projekt, user)
        for c in await _konfiguracja(projekt):
            lyr = ctx.project.layers.get(c["layer_id"])
            if lyr and lyr.pg and ctx.perms(lyr.id).view:
                warstwy.append({"layer_id": lyr.id, "nazwa": lyr.name, "przestrzenna": bool(lyr.pg.geometry_column)})
    return {"projekt": projekt, "warstwy": warstwy, "zestawienia": await _zestawienia(user),
            "formaty": [{"id": k, **v} for k, v in FORMATY.items()]}


# ---------------------------------------------------------------------------- dane warstw projektu

def _etykieta_wartosci(f: FieldDef, v):
    """Wartość czytelna dla człowieka (XLSX, CSV): etykieta z listy wartości, „tak/nie”."""
    if v is None:
        return None
    cfg = f.widget_config or {}
    if f.widget == "ValueMap":
        raw = cfg.get("map")
        pary = [kv for o in raw for kv in o.items()] if isinstance(raw, list) else list((raw or {}).items())
        for etyk, wart in pary:
            if str(wart) == str(v):
                return etyk
    if f.widget == "CheckBox":
        return "tak" if v in (True, "true", "t", 1, "1", cfg.get("CheckedState")) else "nie"
    return v


def _srid(crs: str | None) -> int | None:
    if crs and crs.upper().startswith("EPSG:"):
        try:
            return int(crs.split(":")[1])
        except ValueError:
            return None
    return None


async def _tabela_warstwy(ctx, lyr: LayerDef, pola_cfg: list[str], fmt: str, bbox: list[float] | None,
                          bbox_srid: int | None) -> Tabela:
    istnieja = set((await column_info([lyr])).get((lyr.pg.conninfo, lyr.pg.schema, lyr.pg.table), {}))
    pola = [f for f in lyr.fields if f.widget != "Hidden" and f.name in istnieja and f.name != lyr.pg.geometry_column]
    if pola_cfg:
        pola = [f for f in pola if f.name in pola_cfg]
    przestrzenny = FORMATY[fmt]["przestrzenny"] and bool(lyr.pg.geometry_column)
    srid_out = 2180 if fmt == "mlas" else (lyr.pg.srid or 2180)
    sel = [col(T, f.name) for f in pola]
    if przestrzenny:
        g = col(T, lyr.pg.geometry_column)
        sel += [sql.SQL("ST_AsBinary(ST_Force2D(ST_Transform({}, {}))) AS __geom").format(g, sql.Literal(srid_out)),
                sql.SQL("GeometryType({}) AS __typ").format(g)]
    warunki = [row_filter(ctx, lyr)]
    if bbox and lyr.pg.geometry_column:
        g = col(T, lyr.pg.geometry_column)
        warunki.append(sql.SQL("ST_Intersects({g}, ST_Transform(ST_MakeEnvelope({a}, {b}, {c}, {d}, {s}), ST_SRID({g})))").format(
            g=g, a=sql.Literal(bbox[0]), b=sql.Literal(bbox[1]), c=sql.Literal(bbox[2]), d=sql.Literal(bbox[3]),
            s=sql.Literal(bbox_srid or 2180)))
    zapytanie = sql.SQL("SELECT {sel} FROM {t} AS {a} WHERE {w} LIMIT {lim}").format(
        sel=sql.SQL(", ").join(sel) if sel else sql.SQL("1 AS __brak"), t=table(lyr), a=sql.Identifier(*T),
        w=sql.SQL(" AND ").join(warunki), lim=sql.Literal(MAX_WIERSZY + 1))
    async with layer_conn(lyr.pg.conninfo) as conn, conn.transaction():
        await conn.execute("SET LOCAL statement_timeout = '120s'")
        rows = await (await conn.execute(zapytanie)).fetchall()
    if len(rows) > MAX_WIERSZY:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"Warstwa „{lyr.name}”: ponad {MAX_WIERSZY} obiektów — zawęź zakres (np. bieżący widok mapy)")
    ludzki = fmt in ("xlsx", "csv")
    kolumny = [(f.alias or f.name) if ludzki else f.name for f in pola]
    wiersze = [tuple(_etykieta_wartosci(f, r[f.name]) if ludzki else r[f.name] for f in pola) for r in rows]
    return Tabela(nazwa=lyr.name, kolumny=kolumny, wiersze=wiersze,
                  geometria=[r["__geom"] for r in rows] if przestrzenny else None,
                  typy_geom={r["__typ"] for r in rows if r.get("__typ")} if przestrzenny else set(), srid=srid_out)


# ---------------------------------------------------------------------------- plik i dziennik

async def _plik(fmt: str, tabele: list[Tabela], nazwa: str, user: CurrentUser, projekt: str | None, zrodlo: str,
                zakres: str) -> Response:
    if fmt == "mlas":
        tabele = [t for t in tabele if t.geometria is not None]
        if not tabele:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Paczka mLas obejmuje tylko warstwy przestrzenne")
    kod = ((await get_settings_for("eksport")).get("kodowanie_shp") or "UTF-8").strip()
    katalog = Path(tempfile.mkdtemp(dir=data_dir("eksport")))
    try:
        path, mime = await asyncio.to_thread(zapisz, fmt, tabele, katalog, nazwa, kod)
        dane = path.read_bytes()
    except (ValueError, OSError, RuntimeError) as e:
        log.exception("Eksport %s", zrodlo)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Nie udało się zapisać pliku: {e}") from e
    finally:
        shutil.rmtree(katalog, ignore_errors=True)
    async with portal_conn() as conn:
        await conn.execute(
            """INSERT INTO eksport.dziennik (user_id, login, projekt, zrodlo, elementy, format, zakres, wiersze, rozmiar)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user.id, user.login, projekt, zrodlo, [t.nazwa for t in tabele], fmt, zakres,
             sum(len(t.wiersze) for t in tabele), len(dane)))
    return Response(dane, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{path.name}"'})


class WarstwyIn(BaseModel):
    projekt: str = Field(min_length=1, max_length=100)
    warstwy: list[str] = Field(min_length=1, max_length=30)
    format: str = Field(pattern=FORMAT_RE)
    zakres: str = Field(default="caly", pattern="^(caly|widok)$")
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    crs: str | None = Field(default=None, max_length=40)


@router.post("/warstwy")
async def export_layers(body: WarstwyIn, user: CurrentUser = Depends(POBIERZ)):
    """Plik z wybranych warstw projektu (tylko skonfigurowanych do pobrania, z prawem podglądu, w zakresie danych)."""
    ctx = await load_project_context(body.projekt, user)
    konf = {c["layer_id"]: c for c in await _konfiguracja(body.projekt)}
    if body.zakres == "widok" and not body.bbox:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Brak zasięgu widoku mapy")
    tabele = []
    for lid in dict.fromkeys(body.warstwy):
        lyr = ctx.project.layers.get(lid)
        if lid not in konf or not lyr or not lyr.pg or not ctx.perms(lid).view:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Warstwa {lid} nie jest dostępna do pobrania")
        if body.format == "mlas" and not lyr.pg.geometry_column:
            continue
        try:
            tabele.append(await _tabela_warstwy(ctx, lyr, konf[lid]["pola"], body.format,
                                                body.bbox if body.zakres == "widok" else None, _srid(body.crs)))
        except psycopg.Error as e:
            log.warning("Eksport warstwy %s/%s: %s", body.projekt, lid, e)
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Odczyt warstwy „{lyr.name}” nie powiódł się") from e
    teraz = datetime.now().strftime("%Y%m%d_%H%M")
    return await _plik(body.format, tabele, f"{body.projekt}_{teraz}", user, body.projekt, "warstwy",
                       "bieżący widok" if body.zakres == "widok" else "cały zakres")


class ZestawienieIn(BaseModel):
    format: str = Field(pattern=FORMAT_RE)
    parametry: dict[str, str | None] = {}


@router.post("/zestawienia/{pelne_id}")
async def export_dataset(pelne_id: str, body: ZestawienieIn, user: CurrentUser = Depends(POBIERZ)):
    """Plik z zestawienia wtyczki (np. „wylaczenia.do_lustracji”) — wtyczka sama stosuje zakres danych użytkownika."""
    pid, _, zid = pelne_id.partition(".")
    mod = dict(await _dostawcy()).get(pid)
    if mod is None or not zid:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Brak zestawienia {pelne_id}")
    dostepne = {z["id"]: z for z in await _zestawienia(user)}
    if pelne_id not in dostepne:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Zestawienie niedostępne")
    if FORMATY[body.format]["przestrzenny"] and not dostepne[pelne_id].get("przestrzenne"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "To zestawienie nie ma geometrii — wybierz XLSX albo CSV")
    tabele = [Tabela(nazwa=t["nazwa"], kolumny=list(t["kolumny"]), wiersze=list(t["wiersze"]), geometria=t.get("geometria"),
                     typy_geom=set(t.get("typy_geom") or []), srid=t.get("srid") or 2180)
              for t in await mod.eksport_dane(zid, user, body.parametry, body.format)]
    if sum(len(t.wiersze) for t in tabele) > MAX_WIERSZY:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Ponad {MAX_WIERSZY} wierszy — zawęź parametry")
    opis = ", ".join(f"{k}={v}" for k, v in body.parametry.items() if v) or "bez parametrów"
    teraz = datetime.now().strftime("%Y%m%d_%H%M")
    return await _plik(body.format, tabele, f"{ascii_nazwa(dostepne[pelne_id]['tytul'], 40)}_{teraz}", user, None,
                       pelne_id, opis)


# ---------------------------------------------------------------------------- konfiguracja (administracja)

class WarstwaIn(BaseModel):
    layer_id: str = Field(min_length=1, max_length=200)
    pola: list[str] = Field(default=[], max_length=200)


class KonfiguracjaIn(BaseModel):
    warstwy: list[WarstwaIn] = Field(default=[], max_length=100)


@router.get("/konfiguracja/{projekt}")
async def get_config(projekt: str, user: CurrentUser = Depends(KONFIG)):
    await load_project_context(projekt, user)
    return {"projekt": projekt, "warstwy": await _konfiguracja(projekt)}


@router.put("/konfiguracja/{projekt}")
async def set_config(projekt: str, body: KonfiguracjaIn, user: CurrentUser = Depends(KONFIG)):
    ctx = await load_project_context(projekt, user)
    for w in body.warstwy:
        lyr = ctx.project.layers.get(w.layer_id)
        if not lyr or not lyr.pg:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Warstwa {w.layer_id} nie jest tabelą PostGIS projektu")
        zle = [f for f in w.pola if f not in {x.name for x in lyr.fields}]
        if zle:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Warstwa „{lyr.name}” nie ma pól: {', '.join(zle)}")
    async with portal_conn() as conn, conn.transaction():
        await conn.execute("DELETE FROM eksport.warstwa WHERE projekt = %s", (projekt,))
        for i, w in enumerate(body.warstwy):
            await conn.execute(
                "INSERT INTO eksport.warstwa (projekt, layer_id, pola, kolejnosc, zmienil) VALUES (%s, %s, %s, %s, %s)",
                (projekt, w.layer_id, list(dict.fromkeys(w.pola)), i, user.login))
    return {"projekt": projekt, "warstwy": await _konfiguracja(projekt)}


@router.get("/dziennik")
async def download_log(limit: int = Query(500, ge=1, le=5000), _: CurrentUser = Depends(KONFIG)):
    async with portal_conn() as conn:
        return await (await conn.execute(
            """SELECT kiedy, login, projekt, zrodlo, elementy, format, zakres, wiersze, rozmiar
                 FROM eksport.dziennik ORDER BY id DESC LIMIT %s""", (limit,))).fetchall()
