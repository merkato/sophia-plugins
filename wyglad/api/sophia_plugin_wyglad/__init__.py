"""Wtyczka „Wygląd”: motywy interfejsu (zmienne CSS rdzenia), nazwa aplikacji, logo i ikona karty.

Rdzeń Sophii (≥ 0.8) pyta o wygląd funkcją `theme()` bez logowania — dotyczy też ekranu logowania — więc arkusz,
logo i ikona są dostępne publicznie. Zmiany: uprawnienie `wyglad.konfiguracja`.

Endpointy pod /api/plugins/wyglad/…:
  GET  /motyw.css, /logo, /ikona.png  — publiczne (adresy z wersją, długa pamięć podręczna)
  GET  /konfiguracja                   — ustawienia, motywy (wbudowane i własne), lista zmiennych
  PUT  /ustawienia                     — motyw, tryb, nazwa aplikacji, nazwa obok logo, wysokość logo
  POST /motywy {nazwa, z}              — nowy motyw własny (kopia istniejącego)
  PUT  /motywy/{id}; DELETE            — edycja / usunięcie motywu własnego
  POST /logo (PNG albo WEBP); DELETE /logo
"""
from __future__ import annotations

import asyncio
import io
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.db import portal_conn
from app.plugins import data_dir, require_plugin
from app.security import CurrentUser

from .motywy import WBUDOWANE, ZMIENNE, buduj_css, sprawdz_css, sprawdz_zmienne

router = APIRouter()
KONFIG = require_plugin("wyglad", "wyglad.konfiguracja")
BAZA = "/api/plugins/wyglad"
LOGO_MAX = 2 * 1024 * 1024
LOGO_MAX_PX = 4000
IKONA_PX = 64
TYPY = {"png": "image/png", "webp": "image/webp"}
_PAMIEC: dict = {"t": 0.0, "u": None}


def _katalog() -> Path:
    return data_dir("wyglad")


async def _ustawienia(swieze: bool = False) -> dict:
    if not swieze and _PAMIEC["u"] is not None and time.monotonic() - _PAMIEC["t"] < 5:
        return _PAMIEC["u"]
    async with portal_conn() as conn:
        u = await (await conn.execute(
            """SELECT motyw, tryb, nazwa, pokaz_nazwe, logo, logo_wysokosc, wersja, zmieniono, zmienil
                 FROM wyglad.ustawienia""")).fetchone()
    u = dict(u) if u else {"motyw": "sophia", "tryb": "auto", "nazwa": "", "pokaz_nazwe": True, "logo": None,
                           "logo_wysokosc": 32, "wersja": 1}
    _PAMIEC.update(t=time.monotonic(), u=u)
    return u


def _zapomnij() -> None:
    _PAMIEC.update(t=0.0, u=None)


async def _motyw(mid: str) -> dict | None:
    if mid in WBUDOWANE:
        return {"id": mid, "wbudowany": True, **WBUDOWANE[mid]}
    async with portal_conn() as conn:
        r = await (await conn.execute("SELECT id, nazwa, jasny, ciemny, css FROM wyglad.motyw WHERE id = %s", (mid,))).fetchone()
    return {**r, "wbudowany": False} if r else None


async def _zmiana(conn, user: CurrentUser) -> None:
    """Nowa wersja wyglądu — nowe adresy arkusza i logo (przeglądarki pobiorą je od nowa)."""
    await conn.execute("UPDATE wyglad.ustawienia SET wersja = wersja + 1, zmieniono = now(), zmienil = %s", (user.login,))


# ---------------------------------------------------------------------------- dla rdzenia i publiczne pliki

async def theme() -> dict:
    u = await _ustawienia()
    v = u["wersja"]
    out = {"css": f"{BAZA}/motyw.css?v={v}"}
    if u.get("nazwa"):
        out["title"] = u["nazwa"]
    if u.get("logo"):
        out["logo"] = f"{BAZA}/logo?v={v}"
        out["favicon"] = f"{BAZA}/ikona.png?v={v}"
    return out


@router.get("/motyw.css")
async def motyw_css():
    u = await _ustawienia()
    m = await _motyw(u["motyw"]) or {"id": "sophia", **WBUDOWANE["sophia"]}
    return Response(buduj_css(m, u), media_type="text/css; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/logo")
async def logo():
    u = await _ustawienia()
    p = _katalog() / (u.get("logo") or "-")
    if not u.get("logo") or not p.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brak logo")
    return FileResponse(p, media_type=TYPY[p.suffix[1:]], headers={"Cache-Control": "public, max-age=86400"})


@router.get("/ikona.png")
async def ikona():
    p = _katalog() / "ikona.png"
    if not (await _ustawienia()).get("logo") or not p.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brak ikony")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------------------- konfiguracja

@router.get("/konfiguracja")
async def konfiguracja(user: CurrentUser = Depends(KONFIG)):
    u = await _ustawienia(swieze=True)
    async with portal_conn() as conn:
        wlasne = await (await conn.execute(
            "SELECT id, nazwa, jasny, ciemny, css, zmieniono, zmienil FROM wyglad.motyw ORDER BY nazwa, id")).fetchall()
    motywy = [{"id": k, "wbudowany": True, **v} for k, v in WBUDOWANE.items()] + [{**m, "wbudowany": False} for m in wlasne]
    return {"ustawienia": {**u, "logo_url": f"{BAZA}/logo?v={u['wersja']}" if u.get("logo") else None},
            "motywy": motywy, "zmienne": ZMIENNE}


class Ustawienia(BaseModel):
    motyw: str
    tryb: str = Field("auto", pattern="^(auto|jasny|ciemny)$")
    nazwa: str = Field("", max_length=80)
    pokaz_nazwe: bool = True
    logo_wysokosc: int = Field(32, ge=16, le=64)


@router.put("/ustawienia")
async def zapisz_ustawienia(body: Ustawienia, user: CurrentUser = Depends(KONFIG)):
    if await _motyw(body.motyw) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nie ma takiego motywu")
    async with portal_conn() as conn:
        await conn.execute(
            "UPDATE wyglad.ustawienia SET motyw = %s, tryb = %s, nazwa = %s, pokaz_nazwe = %s, logo_wysokosc = %s",
            (body.motyw, body.tryb, body.nazwa.strip(), body.pokaz_nazwe, body.logo_wysokosc))
        await _zmiana(conn, user)
    _zapomnij()
    return await konfiguracja(user)


class NowyMotyw(BaseModel):
    nazwa: str = Field(min_length=1, max_length=60)
    z: str = "sophia"


class Motyw(BaseModel):
    nazwa: str = Field(min_length=1, max_length=60)
    jasny: dict[str, str] = {}
    ciemny: dict[str, str] = {}
    css: str = ""


@router.post("/motywy")
async def nowy_motyw(body: NowyMotyw, user: CurrentUser = Depends(KONFIG)):
    wzor = await _motyw(body.z)
    if wzor is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nie ma motywu, który miał być wzorem")
    async with portal_conn() as conn:
        r = await (await conn.execute(
            "INSERT INTO wyglad.motyw (nazwa, jasny, ciemny, css, zmienil) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (body.nazwa.strip(), Jsonb(wzor["jasny"]), Jsonb(wzor["ciemny"]), wzor.get("css") or "", user.login))).fetchone()
    return {"id": r["id"], **(await konfiguracja(user))}


@router.put("/motywy/{mid}")
async def zapisz_motyw(mid: str, body: Motyw, user: CurrentUser = Depends(KONFIG)):
    if mid in WBUDOWANE:
        raise HTTPException(status.HTTP_409_CONFLICT, "Motywu wbudowanego nie można zmieniać — utwórz własny na jego podstawie")
    try:
        jasny, ciemny, css = sprawdz_zmienne(body.jasny), sprawdz_zmienne(body.ciemny), sprawdz_css(body.css)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if not jasny:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Motyw musi mieć kolory trybu jasnego")
    async with portal_conn() as conn:
        cur = await conn.execute(
            """UPDATE wyglad.motyw SET nazwa = %s, jasny = %s, ciemny = %s, css = %s, zmieniono = now(), zmienil = %s
                WHERE id = %s""", (body.nazwa.strip(), Jsonb(jasny), Jsonb(ciemny), css, user.login, mid))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Nie ma takiego motywu")
        await _zmiana(conn, user)
    _zapomnij()
    return await konfiguracja(user)


@router.delete("/motywy/{mid}")
async def usun_motyw(mid: str, user: CurrentUser = Depends(KONFIG)):
    if mid in WBUDOWANE:
        raise HTTPException(status.HTTP_409_CONFLICT, "Motywu wbudowanego nie można usunąć")
    async with portal_conn() as conn:
        cur = await conn.execute("DELETE FROM wyglad.motyw WHERE id = %s", (mid,))
        if cur.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Nie ma takiego motywu")
        await conn.execute("UPDATE wyglad.ustawienia SET motyw = 'sophia' WHERE motyw = %s", (mid,))
        await _zmiana(conn, user)
    _zapomnij()
    return await konfiguracja(user)


# ---------------------------------------------------------------------------- logo

def rodzaj_obrazka(dane: bytes) -> str | None:
    """„png” / „webp” po sygnaturze pliku (rozszerzenie i typ z przeglądarki się nie liczą)."""
    if dane.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(dane) > 12 and dane[:4] == b"RIFF" and dane[8:12] == b"WEBP":
        return "webp"
    return None


def przygotuj_logo(dane: bytes, katalog: Path) -> str:
    """Sprawdza obraz (Pillow), zapisuje logo.<png|webp> i ikonę karty ikona.png (64×64, przezroczyste tło)."""
    from PIL import Image

    rodzaj = rodzaj_obrazka(dane)
    if rodzaj is None:
        raise ValueError("Logo musi być plikiem PNG albo WEBP")
    try:
        with Image.open(io.BytesIO(dane)) as im:
            im.verify()
        with Image.open(io.BytesIO(dane)) as im:
            if max(im.size) > LOGO_MAX_PX:
                raise ValueError(f"Obraz jest za duży (najwyżej {LOGO_MAX_PX} px w dłuższym boku)")
            im.load()
            mini = im.convert("RGBA")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Plik nie jest poprawnym obrazem PNG ani WEBP") from exc
    mini.thumbnail((IKONA_PX, IKONA_PX), Image.LANCZOS)
    kanwa = Image.new("RGBA", (IKONA_PX, IKONA_PX), (0, 0, 0, 0))
    kanwa.paste(mini, ((IKONA_PX - mini.width) // 2, (IKONA_PX - mini.height) // 2), mini)
    nazwa = f"logo.{rodzaj}"
    for plik, zawartosc in ((nazwa, dane), ("ikona.png", None)):
        tmp = katalog / f".{plik}.tmp"
        if zawartosc is None:
            kanwa.save(tmp, "PNG")
        else:
            tmp.write_bytes(zawartosc)
        os.replace(tmp, katalog / plik)
    for inny in TYPY:
        if inny != rodzaj:
            (katalog / f"logo.{inny}").unlink(missing_ok=True)
    return nazwa


@router.post("/logo")
async def wgraj_logo(plik: UploadFile = File(...), user: CurrentUser = Depends(KONFIG)):
    dane = await plik.read(LOGO_MAX + 1)
    if len(dane) > LOGO_MAX:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Logo może mieć najwyżej 2 MB")
    try:
        nazwa = await asyncio.to_thread(przygotuj_logo, dane, _katalog())
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    async with portal_conn() as conn:
        await conn.execute("UPDATE wyglad.ustawienia SET logo = %s", (nazwa,))
        await _zmiana(conn, user)
    _zapomnij()
    return await konfiguracja(user)


@router.delete("/logo")
async def usun_logo(user: CurrentUser = Depends(KONFIG)):
    for p in [*(f"logo.{t}" for t in TYPY), "ikona.png"]:
        (_katalog() / p).unlink(missing_ok=True)
    async with portal_conn() as conn:
        await conn.execute("UPDATE wyglad.ustawienia SET logo = NULL")
        await _zmiana(conn, user)
    _zapomnij()
    return await konfiguracja(user)

