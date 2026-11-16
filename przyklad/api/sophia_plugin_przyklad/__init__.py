"""Wtyczka wzorcowa „Przykład”: notatki. Endpointy pod /api/plugins/przyklad/… (montuje je Sophia).

Z rdzenia Sophii korzystamy przez pakiet `app` (ten sam proces API): połączenie z bazą i zależność
uprawnień `require_plugin`. Wyłączona wtyczka zwraca 404 bez udziału tego kodu (strażnik rdzenia).
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db import portal_conn
from app.plugins import require_plugin
from app.security import CurrentUser

router = APIRouter()


class NoteIn(BaseModel):
    tresc: str = Field(min_length=1, max_length=2000)


@router.get("/notatki")
async def notes(_: CurrentUser = Depends(require_plugin("przyklad", "przyklad.podglad"))):
    async with portal_conn() as conn:
        return await (await conn.execute(
            "SELECT id, tresc, autor, utworzono FROM plugin_przyklad.notatka ORDER BY id DESC LIMIT 200")).fetchall()


@router.post("/notatki", status_code=201)
async def add_note(body: NoteIn, user: CurrentUser = Depends(require_plugin("przyklad", "przyklad.zapis"))):
    async with portal_conn() as conn:
        return await (await conn.execute(
            "INSERT INTO plugin_przyklad.notatka (tresc, autor) VALUES (%s, %s) RETURNING id, tresc, autor, utworzono",
            (body.tresc.strip(), user.full_name or user.login))).fetchone()
