"""Zapis tabel do plików: XLSX, CSV, GeoPackage, paczka SHP (mLas) — przez pyogrio (GDAL w kole Pythona).

Wejście: lista `Tabela` (nazwa, kolumny, wiersze, opcjonalnie geometria WKB). Typy kolumn rozpoznawane z wartości
(liczby całkowite, liczby, tak/nie, daty, znaczniki czasu, tekst), puste wartości jako NULL (maska pól).
"""
from __future__ import annotations

import re
import unicodedata
import warnings
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from pyogrio.raw import write

STREFA = ZoneInfo("Europe/Warsaw")
FORMATY = {
    "xlsx": {"tytul": "Excel (XLSX)", "przestrzenny": False},
    "csv": {"tytul": "CSV (średnik, UTF-8)", "przestrzenny": False},
    "gpkg": {"tytul": "GeoPackage", "przestrzenny": True},
    "mlas": {"tytul": "SHP (mLas, EPSG:2180)", "przestrzenny": True},
}
TYPY_GEOM = {"POINT": "Point", "MULTIPOINT": "MultiPoint", "LINESTRING": "LineString", "MULTILINESTRING": "MultiLineString",
             "POLYGON": "Polygon", "MULTIPOLYGON": "MultiPolygon"}


@dataclass
class Tabela:
    nazwa: str                                   # nazwa warstwy / arkusza / pliku w paczce
    kolumny: list[str]                           # nazwy kolumn w pliku
    wiersze: list[tuple]                         # wartości w kolejności kolumn
    geometria: list[bytes | None] | None = None  # WKB (EPSG `srid`) albo None — tabela bez geometrii
    typy_geom: set[str] = field(default_factory=set)   # GeometryType() z PostGIS
    srid: int = 2180


def ascii_nazwa(tekst: str, dl: int = 60) -> str:
    t = unicodedata.normalize("NFKD", tekst.replace("ł", "l").replace("Ł", "L")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")[:dl] or "dane"


def nazwy_shp(kolumny: list[str]) -> list[str]:
    """Nazwy pól DBF: ASCII, małe litery, do 10 znaków, bez powtórzeń (np. „Znak decyzji” → znak_decyz)."""
    out: list[str] = []
    for k in kolumny:
        base = ascii_nazwa(k).lower()[:10] or "pole"
        n, i = base, 1
        while n in out:
            suf = f"_{i}"
            n, i = base[:10 - len(suf)] + suf, i + 1
        out.append(n)
    return out


def _kolumna(wartosci: list) -> tuple[np.ndarray, np.ndarray | None]:
    """Tablica numpy o typie odpowiadającym wartościom i maska pustych (None)."""
    maska = np.array([v is None for v in wartosci], dtype=bool)
    pelne = [v for v in wartosci if v is not None]
    m = maska if maska.any() else None
    if pelne and all(isinstance(v, bool) for v in pelne):
        return np.array([bool(v) if v is not None else False for v in wartosci], dtype=bool), m
    if pelne and all(isinstance(v, int) and not isinstance(v, bool) for v in pelne):
        return np.array([v if v is not None else 0 for v in wartosci], dtype=np.int64), m
    if pelne and all(isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) for v in pelne):
        return np.array([float(v) if v is not None else 0.0 for v in wartosci], dtype=np.float64), m
    if pelne and all(isinstance(v, datetime) for v in pelne):
        lok = [(v.astimezone(STREFA).replace(tzinfo=None) if v.tzinfo else v) if v is not None else datetime(1970, 1, 1)
               for v in wartosci]
        return np.array(lok, dtype="datetime64[s]"), m
    if pelne and all(isinstance(v, date) and not isinstance(v, datetime) for v in pelne):
        return np.array([v if v is not None else date(1970, 1, 1) for v in wartosci], dtype="datetime64[D]"), m
    return np.array(["" if v is None else str(v) for v in wartosci], dtype=object), m


def _zapisz_tabele(path: Path, t: Tabela, driver: str, *, append: bool, z_geometria: bool,
                   layer_options: dict | None = None) -> None:
    kolumny = list(zip(*t.wiersze, strict=True)) if t.wiersze else [[] for _ in t.kolumny]
    dane, maski = [], []
    for wart in kolumny:
        arr, m = _kolumna(list(wart))
        dane.append(arr)
        maski.append(m if m is not None else np.zeros(len(arr), dtype=bool))
    geom, typ, crs = None, None, None
    if z_geometria and t.geometria is not None:
        geom = np.array([bytes(g) if g is not None else None for g in t.geometria], dtype=object)
        typy = {TYPY_GEOM.get((x or "").upper()) for x in t.typy_geom} - {None}
        if len(typy) == 1:
            typ = typy.pop()
        elif typy and typy <= {"Polygon", "MultiPolygon"}:
            typ = "MultiPolygon"
        elif typy and typy <= {"LineString", "MultiLineString"}:
            typ = "MultiLineString"
        elif typy and typy <= {"Point", "MultiPoint"}:
            typ = "MultiPoint"
        else:
            typ = "Unknown"
        crs = f"EPSG:{t.srid}"
    with warnings.catch_warnings():     # GDAL: zamiana typów pól (np. data → tekst w SHP) — oczekiwana
        warnings.simplefilter("ignore", RuntimeWarning)
        write(str(path), geom, dane, t.kolumny, field_mask=maski, layer=t.nazwa, driver=driver,
              geometry_type=typ if geom is not None else None, crs=crs, append=append,
              promote_to_multi=typ in ("MultiPolygon", "MultiLineString", "MultiPoint") or None,
              layer_options=layer_options or {})


def zapisz(fmt: str, tabele: list[Tabela], katalog: Path, nazwa: str, kodowanie_shp: str = "UTF-8") -> tuple[Path, str]:
    """Plik do pobrania (ścieżka, typ MIME). Kilka tabel: GeoPackage / XLSX — warstwy / arkusze w jednym pliku,
    CSV i SHP — archiwum ZIP (SHP mLas zawsze ZIP). `katalog` — nowy, pusty katalog tymczasowy."""
    base = ascii_nazwa(nazwa)
    for stary in katalog.glob(f"{base}*"):      # ponowny zapis w tym samym katalogu (testy) — od zera
        stary.unlink()
    if fmt == "gpkg":
        out = katalog / f"{base}.gpkg"
        for i, t in enumerate(tabele):
            _zapisz_tabele(out, Tabela(ascii_nazwa(t.nazwa), t.kolumny, t.wiersze, t.geometria, t.typy_geom, t.srid),
                           "GPKG", append=i > 0, z_geometria=True)
        return out, "application/geopackage+sqlite3"
    if fmt == "xlsx":
        out = katalog / f"{base}.xlsx"
        uzyte: set[str] = set()
        for i, t in enumerate(tabele):
            arkusz = re.sub(r"[\[\]:*?/\\]", " ", t.nazwa)[:31] or f"Arkusz{i + 1}"
            while arkusz in uzyte:
                arkusz = f"{arkusz[:28]}_{i + 1}"
            uzyte.add(arkusz)
            _zapisz_tabele(out, Tabela(arkusz, t.kolumny, t.wiersze), "XLSX", append=i > 0, z_geometria=False)
        return out, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if fmt == "csv":
        pliki = []
        for t in tabele:
            p = katalog / f"{ascii_nazwa(t.nazwa)}.csv"
            _zapisz_tabele(p, Tabela(p.stem, t.kolumny, t.wiersze), "CSV", append=False, z_geometria=False,
                           layer_options={"SEPARATOR": "SEMICOLON", "WRITE_BOM": "YES"})
            pliki.append(p)
        if len(pliki) == 1:
            return pliki[0], "text/csv"
        return _zip(katalog / f"{base}.zip", pliki), "application/zip"
    if fmt == "mlas":
        pliki = []
        for t in tabele:
            if t.geometria is None:
                continue
            p = katalog / f"{ascii_nazwa(t.nazwa, 30)}.shp"
            _zapisz_tabele(p, Tabela(p.stem, nazwy_shp(t.kolumny), t.wiersze, t.geometria, t.typy_geom, t.srid), "ESRI Shapefile",
                           append=False, z_geometria=True, layer_options={"ENCODING": kodowanie_shp or "UTF-8"})
            pliki.extend(sorted(katalog.glob(f"{p.stem}.*")))
        return _zip(katalog / f"{base}_mLas.zip", pliki), "application/zip"
    raise ValueError(f"Nieznany format: {fmt}")


def _zip(out: Path, pliki: list[Path]) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in pliki:
            z.write(p, arcname=p.name)
    return out
