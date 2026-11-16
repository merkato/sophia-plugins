"""Strumieniowy odczyt GML EGiB.

Pliki od starostw różnią się w zależności od systemu (GEO-INFO, EwMapa…), więc mapowanie elementów
na kolumny rejestru jest w **profilach** (`PROFILES`). Profil `egib2021` obsługuje schemat aplikacyjny
z rozporządzenia o EGiB z 2021 r. (przestrzeń nazw `ewidencjaGruntowIBudynkow:1.0`, klasy `EGB_*`).
Nowy system = nowy profil (te same kolumny wyjściowe), reszta importu bez zmian.

Wynik: słowniki `Obiekt` (rodzaj, klucz, wersja, daty, atrybuty, surowe odwołania, geometria jako tekst
GML) — geometrię przelicza i waliduje PostGIS (`ST_GeomFromGML` respektuje kolejność osi z `srsName`).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from lxml import etree

GML_NS = "http://www.opengis.net/gml/3.2"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


@dataclass
class TypeMap:
    rodzaj: str                              # dzialka | uzytek | obreb | jednostka | punkt | zmiana
    fields: dict[str, str]                   # element → kolumna
    refs: dict[str, str] = field(default_factory=dict)   # element z xlink:href → klucz odwołania (lista)
    geometry: str | None = "geometria"


@dataclass
class Profile:
    id: str
    namespace: str
    types: dict[str, TypeMap]
    version_ref: str = "podstawaUtworzeniaWersjiObiektu"   # → EGB_Zmiana


EGIB2021 = Profile(
    id="egib2021",
    namespace="ewidencjaGruntowIBudynkow:1.0",
    types={
        "EGB_DzialkaEwidencyjna": TypeMap("dzialka", {
            "idDzialki": "id_dzialki", "poleEwidencyjne": "pole_ewid_ha",
            "dokladnoscReprezentacjiPola": "dokladnosc_pola", "dodatkoweInformacje": "dodatkowe_info"},
            refs={"punktGranicyDzialki": "punkty"}),
        "EGB_KonturUzytkuGruntowego": TypeMap("uzytek", {"idUzytku": "id_uzytku", "OFU": "ofu"}),
        "EGB_ObrebEwidencyjny": TypeMap("obreb", {"idObrebu": "id_obrebu", "nazwaWlasna": "nazwa"}),
        "EGB_JednostkaEwidencyjna": TypeMap("jednostka", {"idJednostkiEwid": "id_jednostki", "nazwaWlasna": "nazwa"}),
        "EGB_PunktGraniczny": TypeMap("punkt", {
            "idPunktu": "id_punktu", "sposobPozyskania": "sposob_pozyskania",
            "spelnienieWarunkowDokl": "spelnienie_warunkow_dokl", "rodzajStabilizacji": "rodzaj_stabilizacji",
            "oznWMaterialeZrodlowym": "ozn_w_materiale", "dodatkoweInformacje": "dodatkowe_info"}),
        "EGB_Zmiana": TypeMap("zmiana", {
            "nrZmiany": "nr_zmiany", "opisZmiany": "opis",
            "dataPrzyjeciaZgloszeniaZmiany": "data_zgloszenia", "dataAkceptacjiZmiany": "data_akceptacji"},
            geometry=None),
    },
)
PROFILES = {p.id: p for p in (EGIB2021,)}
RODZAJE = ("jednostka", "obreb", "dzialka", "uzytek", "punkt", "zmiana")


@dataclass
class Naglowek:
    profil: str | None
    system: str | None
    schemat: str | None


def detect(head: bytes) -> Naglowek:
    """Rozpoznanie formatu z początku pliku: przestrzeń nazw → profil, komentarz → system, XSD → schemat."""
    text = head.decode("utf-8", "replace")
    profil = next((p.id for p in PROFILES.values() if f'"{p.namespace}"' in text), None)
    system = None
    m = re.search(r";\s*((?:GEO-INFO|EwMapa|EWMAPA|GEOMEDIA|Geo-System|iGeoMap|OŚRODEK|C-GEO)[^\n\r]*)", text, re.I)
    if m:
        system = m.group(1).strip()
    else:
        m = re.search(r"<!--\s*\n?\s*;?\s*([^\n\r]{3,80})", text)
        system = m.group(1).strip() if m else None
    sch = re.search(r"(EGIB_[\w.]+\.XSD)", text, re.I)
    return Naglowek(profil, system, sch.group(1) if sch else None)


@dataclass
class Obiekt:
    rodzaj: str
    klucz: str
    gml_id: str | None
    wersja_id: str | None
    obiekt_od: str | None
    wersja_od: str | None
    obiekt_do: str | None
    atrybuty: dict
    odwolania: dict
    gml: str | None
    srs: str | None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def klucz_z_gml_id(gml_id: str) -> str | None:
    """Zapas: gml:id w eksportach EGiB ma postać <przestrzenNazw>_<lokalnyId>_<wersja>."""
    m = re.match(r"^(.+?)_([0-9A-Fa-f]{8}-[0-9A-Fa-f-]{27,})_", gml_id or "")
    return f"{m.group(1)}:{m.group(2)}" if m else None


def parse(path: str, profile: Profile, rodzaje: set[str],
          progress: Callable[[int], None] | None = None) -> Iterator[Obiekt]:
    """Obiekty wybranych rodzajów z pliku, po kolei, przy stałej pamięci."""
    wanted = {name: tm for name, tm in profile.types.items() if tm.rodzaj in rodzaje}
    ns = "{" + profile.namespace + "}"
    n = 0
    for _ev, el in etree.iterparse(path, events=("end",), huge_tree=True, remove_blank_text=True):
        parent = el.getparent()
        if parent is None or _local(parent.tag) != "featureMember":
            continue
        name = _local(el.tag)
        tm = wanted.get(name) if el.tag.startswith(ns) else None
        if tm is not None:
            yield _obiekt(el, tm, profile, ns)
            n += 1
            if progress and n % 5000 == 0:
                progress(n)
        # zwolnienie pamięci: element i jego poprzednicy w kolekcji
        el.clear()
        fm = parent
        root = fm.getparent()
        if root is not None:
            while fm.getprevious() is not None:
                del root[0]


def _obiekt(el, tm: TypeMap, profile: Profile, ns: str) -> Obiekt:
    iip = el.find(f"{ns}idIIP/{ns}EGB_IdentyfikatorIIP")
    lokalny = iip.findtext(f"{ns}lokalnyId") if iip is not None else None
    przestrzen = iip.findtext(f"{ns}przestrzenNazw") if iip is not None else None
    wersja = iip.findtext(f"{ns}wersjaId") if iip is not None else None
    gml_id = el.get(f"{{{GML_NS}}}id")
    atr: dict = {}
    odw: dict = {}
    gml = srs = None
    for c in el:
        n = _local(c.tag)
        if n in tm.fields:
            atr[tm.fields[n]] = (c.text or "").strip() or None
        elif n in tm.refs:
            href = c.get(XLINK_HREF)
            if href:
                odw.setdefault(tm.refs[n], []).append(href.lstrip("#"))
        elif n == profile.version_ref and c.get(XLINK_HREF):
            odw["zmiana"] = c.get(XLINK_HREF).lstrip("#")
        elif tm.geometry and n == tm.geometry and len(c):
            g = c[0]
            srs = g.get("srsName")
            gml = etree.tostring(g, encoding="unicode")
    if lokalny and przestrzen:
        klucz = f"{przestrzen}:{lokalny}"
    else:   # eksport bez IIP: klucz naturalny z identyfikatora EGiB
        ident = next((atr.get(k) for k in ("id_dzialki", "id_uzytku", "id_obrebu", "id_jednostki", "id_punktu",
                                            "nr_zmiany") if atr.get(k)), None)
        klucz = f"ID:{tm.rodzaj}:{ident}" if ident else f"GML:{gml_id}"
    return Obiekt(
        rodzaj=tm.rodzaj, klucz=klucz, gml_id=gml_id, wersja_id=wersja,
        obiekt_od=el.findtext(f"{ns}startObiekt"), wersja_od=el.findtext(f"{ns}startWersjaObiekt"),
        obiekt_do=el.findtext(f"{ns}koniecObiekt"), atrybuty=atr, odwolania=odw, gml=gml, srs=srs)


def read_klauzula(data: bytes) -> dict:
    """Klauzula `*.klauzula.xml` (MaterialZasobuUdostepniony): organ, materiał, data kopii, suma kontrolna."""
    root = etree.fromstring(data)
    out = {}
    for el in root.iter():
        n = _local(el.tag) if isinstance(el.tag, str) else ""
        if n in ("organProwadzacyZasob", "nazwaMaterialuZasobu", "dataWykonaniaKopii", "sumaKontrolna",
                 "identyfikatorEwidencyjny") and (el.text or "").strip():
            out[n] = el.text.strip()
    return out
