"""Źródła danych PRG: usługa WFS GUGiK (pobieranie obszaru) i plik z paczki PRG (SHP w ZIP albo GML).

Obie drogi dają te same wiersze: klucz IIP, wersja, kod, nazwa, organ, data ważności, wszystkie pola źródła
i geometria (WKB w układzie źródła). Nazwy pól są takie same w WFS i w paczkach SHP (WFS GUGiK jest
wystawiony z tych samych plików).
"""
from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries"
TIMEOUT = 300.0
POMIJANE = {"gml_id", "SHAPE_LENG", "SHAPE_AREA", "SHAPE_Leng", "SHAPE_Area"}


@dataclass(frozen=True)
class Rodzaj:
    id: str
    nazwa: str
    warstwa_wfs: str              # typeName w usłudze WFS PRG
    plik: str                     # fragment nazwy pliku SHP w paczce PRG (bez wielkości liter)
    strona: int                   # obiektów na stronę WFS (nadleśnictwa mają duże geometrie)
    obszar_re: str                # dopuszczalny początek kodu (zakres obszaru)
    obszar_opis: str


RODZAJE: dict[str, Rodzaj] = {
    "obreb": Rodzaj("obreb", "Obręby ewidencyjne", "ms:A06_Granice_obrebow_ewidencyjnych", "obreb", 1000,
                    r"^[0-9]{2}([0-9]{0,5}|[0-9]{4}_[0-9](\.[0-9]{0,4})?)$",
                    "początek identyfikatora TERYT: województwo (14), powiat (1465), gmina (146501_1)"),
    "nadlesnictwo": Rodzaj("nadlesnictwo", "Nadleśnictwa", "ms:U06_Nadlesnictwo", "nadles", 100,
                           r"^[0-9]{2}(-[0-9]{0,2})?$", "kod RDLP (17) albo nadleśnictwa (17-02)"),
}


class ZrodloError(Exception):
    """Błąd źródła danych (komunikat dla użytkownika)."""


def sprawdz_obszar(rodzaj: Rodzaj, obszar: list[str]) -> list[str]:
    out = sorted({o.strip() for o in obszar if o.strip()})
    zle = [o for o in out if not re.match(rodzaj.obszar_re, o)]
    if zle:
        raise ZrodloError(f"Niepoprawny zakres obszaru: {', '.join(zle[:10])} — {rodzaj.obszar_opis}")
    return out


def w_obszarze(kod: str | None, obszar: list[str]) -> bool:
    return not obszar or (kod is not None and any(kod.startswith(o) for o in obszar))


# ---------------------------------------------------------------------------- normalizacja


def data_prg(v) -> date | None:
    """Daty PRG: „2024-02-16T00:00:00”, „2024-02-16”, „19910928”; „0” i puste = brak."""
    s = str(v or "").strip()
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", s)
    if not m or s == "0":
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def _txt(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def wiersz(rodzaj: Rodzaj, pola: dict) -> dict:
    """Pola obiektu PRG → kolumny rejestru (klucz IIP, wersja, kod, nazwa, organ, ważność, atrybuty)."""
    przest, ident = _txt(pola.get("IIP_PRZEST")), _txt(pola.get("IIP_IDENTY"))
    jpt = _txt(pola.get("JPT_ID"))
    klucz = f"{przest}:{ident}" if przest and ident else (f"JPT:{jpt}" if jpt else None)
    return {
        "klucz": klucz,
        "wersja_id": _txt(pola.get("IIP_WERSJA")),
        "kod": _txt(pola.get("JPT_KOD_JE")),
        "nazwa": _txt(pola.get("JPT_NAZWA_")),
        "organ": _txt(pola.get("JPT_ORGAN_")) if rodzaj.id == "nadlesnictwo" else None,
        "wazny_od": data_prg(pola.get("WAZNY_OD")),
        "atrybuty": {k: _txt(v) for k, v in pola.items() if k not in POMIJANE and _txt(v) is not None},
    }


# ---------------------------------------------------------------------------- odczyt plików (pyogrio)


def czytaj(path: str | Path) -> tuple[int | None, Iterator[tuple[dict, bytes | None]]]:
    """Plik GML/SHP (także /vsizip/…) → (EPSG układu, iterator (pola, WKB))."""
    import pyogrio.raw

    meta, _fids, geoms, dane = pyogrio.raw.read(str(path))
    crs = str(meta.get("crs") or "")
    m = re.search(r"EPSG:(\d+)", crs)
    srid = int(m[1]) if m else None
    nazwy = list(meta["fields"])

    def rows():
        for i, g in enumerate(geoms):
            yield {n: dane[j][i] for j, n in enumerate(nazwy)}, (bytes(g) if g is not None else None)
    return srid, rows()


def plik_w_paczce(path: Path, rodzaj: Rodzaj) -> str:
    """Ścieżka do odczytu: GML wprost; ZIP — plik SHP rodzaju (po nazwie) albo jedyny SHP w paczce."""
    import zipfile

    if path.suffix.lower() in (".gml", ".xml"):
        return str(path)
    if path.suffix.lower() == ".shp":
        return str(path)
    if not zipfile.is_zipfile(path):
        raise ZrodloError("Oczekiwano paczki ZIP z plikami SHP albo pliku GML")
    with zipfile.ZipFile(path) as z:
        shp = [n for n in z.namelist() if n.lower().endswith(".shp")]
    pasujace = [n for n in shp if rodzaj.plik in Path(n).name.lower()]
    wybor = pasujace if pasujace else (shp if len(shp) == 1 else [])
    if len(wybor) != 1:
        nazwy = ", ".join(Path(n).name for n in shp[:15]) or "brak plików SHP"
        raise ZrodloError(f"Nie da się wskazać pliku „{rodzaj.nazwa}” w paczce ({nazwy})")
    return f"/vsizip/{path}/{wybor[0]}"


# ---------------------------------------------------------------------------- WFS


def filtr_wfs(obszar: list[str]) -> str | None:
    """Filtr FES 2.0: kod zaczyna się od któregoś z podanych początków."""
    if not obszar:
        return None
    likes = "".join(
        '<fes:PropertyIsLike wildCard="*" singleChar="?" escapeChar="!">'
        f"<fes:ValueReference>JPT_KOD_JE</fes:ValueReference><fes:Literal>{escape(o)}*</fes:Literal>"
        "</fes:PropertyIsLike>" for o in obszar)
    if len(obszar) > 1:
        likes = f"<fes:Or>{likes}</fes:Or>"
    return f'<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0">{likes}</fes:Filter>'


def parametry_wfs(rodzaj: Rodzaj, obszar: list[str], start: int) -> dict:
    p = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": rodzaj.warstwa_wfs,
         "count": str(rodzaj.strona), "startIndex": str(start), "srsName": "EPSG:2180"}
    f = filtr_wfs(obszar)
    if f:
        p["FILTER"] = f
    return p


def pobierz_strony(rodzaj: Rodzaj, obszar: list[str], katalog: Path, postep=None) -> Iterator[Path]:
    """Kolejne strony odpowiedzi WFS zapisane jako pliki GML (koniec, gdy strona niepełna)."""
    import httpx

    start, nr = 0, 0
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        while True:
            for proba in range(3):
                try:
                    r = client.get(WFS_URL, params=parametry_wfs(rodzaj, obszar, start))
                    if r.status_code == 200:
                        break
                    blad = f"HTTP {r.status_code}"
                except httpx.HTTPError as e:
                    blad = type(e).__name__
                time.sleep(5 * (proba + 1))
            else:
                raise ZrodloError(f"Usługa WFS PRG nie odpowiada ({blad}) — spróbuj później")
            if b"ExceptionReport" in r.content[:2000]:
                raise ZrodloError("WFS PRG: " + re.sub(r"<[^>]+>", " ", r.text)[:300].strip())
            ile = r.content.count(b"<wfs:member>")
            if ile:
                nr += 1
                f = katalog / f"strona_{nr:04d}.gml"
                f.write_bytes(r.content)
                if postep:
                    postep(start + ile)
                yield f
            if ile < rodzaj.strona:
                return
            start += ile


# ---------------------------------------------------------------------------- słownik jednostek (wybór obszaru)

# poziom → warstwa WFS; kody jednostek ewidencyjnych (np. 146502_8) to początki identyfikatorów obrębów
SLOWNIK_WARSTWY = {
    "wojewodztwo": "ms:A01_Granice_wojewodztw",
    "powiat": "ms:A02_Granice_powiatow",
    "jednostka_ew": "ms:A05_Granice_jednostek_ewidencyjnych",
    "rdlp": "ms:U07_Regionalna_dyrekcja_lasow_panstwowych",
    "nadlesnictwo": "ms:U06_Nadlesnictwo",
}
POZIOMY = {"obreb": ("wojewodztwo", "powiat", "jednostka_ew"), "nadlesnictwo": ("rdlp", "nadlesnictwo")}
_MEMBER_RE = re.compile(rb"<wfs:member>(.*?)</wfs:member>", re.S)
_KOD_RE = re.compile(rb"<ms:JPT_KOD_JE>([^<]*)</ms:JPT_KOD_JE>")
_NAZWA_RE = re.compile(rb"<ms:JPT_NAZWA_>([^<]*)</ms:JPT_NAZWA_>")


def parsuj_slownik(xml: bytes) -> list[tuple[str, str]]:
    """Odpowiedź GetFeature z polami JPT_KOD_JE i JPT_NAZWA_ → posortowane (kod, nazwa), bez dubli kodu."""
    from html import unescape

    out: dict[str, str] = {}
    for m in _MEMBER_RE.finditer(xml):
        k, n = _KOD_RE.search(m[1]), _NAZWA_RE.search(m[1])
        if k and n:
            kod, nazwa = unescape(k[1].decode()).strip(), unescape(n[1].decode()).strip()
            if kod and nazwa:
                out.setdefault(kod, nazwa)
    return sorted(out.items())


def pobierz_slownik(poziom: str) -> list[tuple[str, str]]:
    """Kody i nazwy jednostek poziomu z usługi WFS PRG (bez geometrii)."""
    import httpx

    p = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": SLOWNIK_WARSTWY[poziom],
         "PROPERTYNAME": "JPT_KOD_JE,JPT_NAZWA_"}
    blad = "brak odpowiedzi"
    for proba in range(3):
        try:
            r = httpx.get(WFS_URL, params=p, timeout=TIMEOUT, follow_redirects=True)
            if r.status_code == 200 and b"ExceptionReport" not in r.content[:2000]:
                wynik = parsuj_slownik(r.content)
                if wynik:
                    return wynik
                blad = "pusta lista"
            else:
                blad = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            blad = type(e).__name__
        time.sleep(5 * (proba + 1))
    raise ZrodloError(f"Nie udało się pobrać listy jednostek ({poziom}) z usługi PRG: {blad}")
