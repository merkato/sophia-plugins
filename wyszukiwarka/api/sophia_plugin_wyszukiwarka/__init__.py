"""Wtyczka „Wyszukiwarka”: wyszukiwanie obiektów w projekcie mapy po wskazanych warstwach i polach.

Konfiguracja (administrator): dla projektu — warstwy PostGIS z kluczem, pola przeszukiwane i pola opisu wyniku.
Wyszukiwanie: każde słowo zapytania musi wystąpić (fragment, bez wielkości liter) w którymś z pól przeszukiwanych.
Co użytkownik może znaleźć, rozstrzyga rdzeń Sophii tym samym warunkiem co dla mapy i formularzy: prawo podglądu
warstwy, filtr warstwy z projektu QGIS i zakres danych użytkownika (`app.scope.row_filter`).

Endpointy pod /api/plugins/wyszukiwarka/…:
  GET /szukaj?projekt=&q=                  — wyniki pogrupowane według warstw (wyszukiwarka.szukaj)
  GET /konfiguracja/{projekt}; PUT …       — warstwy i pola projektu (wyszukiwarka.konfiguracja)
"""
from __future__ import annotations

import logging

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg import sql
from pydantic import BaseModel, Field

from app.columns import column_info
from app.db import layer_conn, portal_conn
from app.permissions import load_project_context
from app.plugins import require_plugin
from app.scope import T, col, row_filter, table
from app.security import CurrentUser

log = logging.getLogger("sophia.wyszukiwarka")
router = APIRouter()
SZUKAJ = require_plugin("wyszukiwarka", "wyszukiwarka.szukaj")
KONFIG = require_plugin("wyszukiwarka", "wyszukiwarka.konfiguracja")
LIMIT_WARSTWY = 20
MAX_SLOW = 6


def _like(w: str) -> str:
    return "%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def _konfiguracja(projekt: str) -> list[dict]:
    async with portal_conn() as conn:
        return await (await conn.execute(
            """SELECT layer_id, pola, etykieta, kolejnosc, zmieniono, zmienil FROM wyszukiwarka.warstwa
                WHERE projekt = %s ORDER BY kolejnosc, layer_id""", (projekt,))).fetchall()


@router.get("/szukaj")
async def search(projekt: str = Query(min_length=1, max_length=100), q: str = Query(min_length=2, max_length=100),
                 user: CurrentUser = Depends(SZUKAJ)):
    """Obiekty pasujące do zapytania w skonfigurowanych warstwach projektu — tylko te, które użytkownik może zobaczyć."""
    ctx = await load_project_context(projekt, user)
    konf = await _konfiguracja(projekt)
    slowa = [w for w in q.split() if w][:MAX_SLOW]
    if not slowa or not konf:
        return {"projekt": projekt, "skonfigurowano": bool(konf), "wyniki": []}
    warstwy = [(c, ctx.project.layers.get(c["layer_id"])) for c in konf]
    warstwy = [(c, lyr) for c, lyr in warstwy if lyr and lyr.pg and lyr.pg.key and ctx.perms(lyr.id).view]
    kolumny = await column_info([lyr for _, lyr in warstwy])
    wyniki = []
    for c, lyr in warstwy:
        istnieja = set(kolumny.get((lyr.pg.conninfo, lyr.pg.schema, lyr.pg.table), {}))
        pola = [f for f in c["pola"] if f in istnieja]
        etyk = [f for f in (c["etykieta"] or c["pola"]) if f in istnieja] or pola
        if not pola:
            continue
        warunki = [sql.SQL("({})").format(sql.SQL(" OR ").join(
            sql.SQL("{}::text ILIKE {}").format(col(T, f), sql.Literal(_like(w))) for f in pola)) for w in slowa]
        dokladnie = sql.SQL(" OR ").join(
            sql.SQL("lower({}::text) = {}").format(col(T, f), sql.Literal(q.strip().lower())) for f in pola)
        etykieta = sql.SQL("concat_ws(' · ', {})").format(sql.SQL(", ").join(sql.SQL("{}::text").format(col(T, f)) for f in etyk))
        geom = (sql.SQL("ST_AsGeoJSON(ST_Transform(ST_Envelope({}), 4326), 7)::json").format(col(T, lyr.pg.geometry_column))
                if lyr.pg.geometry_column else sql.SQL("NULL::json"))
        zapytanie = sql.SQL(
            """SELECT {pk}::text AS pk, {etykieta} AS etykieta, {geom} AS geometry
                 FROM {tabela} AS {alias}
                WHERE {filtr} AND {warunki}
                ORDER BY CASE WHEN {dokladnie} THEN 0 ELSE 1 END, 2
                LIMIT {limit}""").format(
            pk=col(T, lyr.pg.key), etykieta=etykieta, geom=geom, tabela=table(lyr), alias=sql.Identifier(*T),
            filtr=row_filter(ctx, lyr), warunki=sql.SQL(" AND ").join(warunki), dokladnie=dokladnie,
            limit=sql.Literal(LIMIT_WARSTWY + 1))
        try:
            async with layer_conn(lyr.pg.conninfo) as conn, conn.transaction():
                await conn.execute("SET LOCAL statement_timeout = '5s'")
                rows = await (await conn.execute(zapytanie)).fetchall()
        except psycopg.Error as e:
            log.warning("Wyszukiwanie %s/%s: %s", projekt, lyr.id, e)
            wyniki.append({"layer_id": lyr.id, "warstwa": lyr.name, "blad": "wyszukiwanie w tej warstwie nie powiodło się"})
            continue
        if rows:
            wyniki.append({"layer_id": lyr.id, "warstwa": lyr.name, "table": f"{lyr.pg.schema}.{lyr.pg.table}",
                           "obiekty": rows[:LIMIT_WARSTWY], "wiecej": len(rows) > LIMIT_WARSTWY})
    return {"projekt": projekt, "skonfigurowano": True, "wyniki": wyniki}


class WarstwaIn(BaseModel):
    layer_id: str = Field(min_length=1, max_length=200)
    pola: list[str] = Field(min_length=1, max_length=20)
    etykieta: list[str] = Field(default=[], max_length=10)


class KonfiguracjaIn(BaseModel):
    warstwy: list[WarstwaIn] = Field(default=[], max_length=50)


@router.get("/konfiguracja/{projekt}")
async def get_config(projekt: str, user: CurrentUser = Depends(KONFIG)):
    await load_project_context(projekt, user)        # projekt istnieje i jest dostępny
    return {"projekt": projekt, "warstwy": await _konfiguracja(projekt)}


@router.put("/konfiguracja/{projekt}")
async def set_config(projekt: str, body: KonfiguracjaIn, user: CurrentUser = Depends(KONFIG)):
    """Zastępuje konfigurację projektu. Pola sprawdzane z warstwami projektu (PostGIS, z kluczem głównym)."""
    ctx = await load_project_context(projekt, user)
    for w in body.warstwy:
        lyr = ctx.project.layers.get(w.layer_id)
        if not lyr or not lyr.pg or not lyr.pg.key:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"Warstwa {w.layer_id} nie istnieje w projekcie albo nie jest tabelą PostGIS z kluczem")
        nazwy = {f.name for f in lyr.fields}
        zle = [f for f in [*w.pola, *w.etykieta] if f not in nazwy]
        if zle:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Warstwa „{lyr.name}” nie ma pól: {', '.join(zle)}")
    async with portal_conn() as conn, conn.transaction():
        await conn.execute("DELETE FROM wyszukiwarka.warstwa WHERE projekt = %s", (projekt,))
        for i, w in enumerate(body.warstwy):
            await conn.execute(
                """INSERT INTO wyszukiwarka.warstwa (projekt, layer_id, pola, etykieta, kolejnosc, zmienil)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (projekt, w.layer_id, list(dict.fromkeys(w.pola)), list(dict.fromkeys(w.etykieta)), i, user.login))
    return {"projekt": projekt, "warstwy": await _konfiguracja(projekt)}
