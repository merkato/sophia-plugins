"""Parser GML EGiB na małym, sztucznym pliku w układzie schematu z 2021 r. (bez danych rzeczywistych)."""
from pathlib import Path

from sophia_plugin_egib import parser

NS = "PL.PZGiK.999.EGiB"
GML = f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
;GEO-INFO Mapa 26.10.2.0
;FORMAT: GML zgodny z rozp. GUGiK (2021)
;Schematy aplikacyjne GML: EGIB_1.9.XSD
-->
<gml:FeatureCollection xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:xlink="http://www.w3.org/1999/xlink"
 xmlns:egb="ewidencjaGruntowIBudynkow:1.0" gml:id="_test">
<gml:featureMember><egb:EGB_Zmiana gml:id="{NS}_11111111-1111-1111-1111-111111111111_2024-01-01T00-00-00">
 <egb:idIIP><egb:EGB_IdentyfikatorIIP><egb:lokalnyId>11111111-1111-1111-1111-111111111111</egb:lokalnyId>
  <egb:przestrzenNazw>{NS}</egb:przestrzenNazw><egb:wersjaId>2024-01-01T00:00:00</egb:wersjaId></egb:EGB_IdentyfikatorIIP></egb:idIIP>
 <egb:startObiekt>2024-01-01T00:00:00</egb:startObiekt><egb:nrZmiany>Z-1/2024</egb:nrZmiany><egb:opisZmiany>Podział działki 12</egb:opisZmiany>
</egb:EGB_Zmiana></gml:featureMember>
<gml:featureMember><egb:EGB_PunktGraniczny gml:id="{NS}_22222222-2222-2222-2222-222222222222_2020-01-01T00-00-00">
 <egb:idIIP><egb:EGB_IdentyfikatorIIP><egb:lokalnyId>22222222-2222-2222-2222-222222222222</egb:lokalnyId>
  <egb:przestrzenNazw>{NS}</egb:przestrzenNazw><egb:wersjaId>2020-01-01T00:00:00</egb:wersjaId></egb:EGB_IdentyfikatorIIP></egb:idIIP>
 <egb:startObiekt>2020-01-01T00:00:00</egb:startObiekt>
 <egb:geometria><gml:Point gml:id="p1" srsName="urn:ogc:def:crs:EPSG::2178"><gml:pos>5763000.00 7468000.00</gml:pos></gml:Point></egb:geometria>
 <egb:idPunktu>O.999901_2.1</egb:idPunktu><egb:sposobPozyskania>2</egb:sposobPozyskania>
</egb:EGB_PunktGraniczny></gml:featureMember>
<gml:featureMember><egb:EGB_DzialkaEwidencyjna gml:id="{NS}_33333333-3333-3333-3333-333333333333_2024-01-01T00-00-00">
 <egb:idIIP><egb:EGB_IdentyfikatorIIP><egb:lokalnyId>33333333-3333-3333-3333-333333333333</egb:lokalnyId>
  <egb:przestrzenNazw>{NS}</egb:przestrzenNazw><egb:wersjaId>2024-01-01T00:00:00</egb:wersjaId></egb:EGB_IdentyfikatorIIP></egb:idIIP>
 <egb:startObiekt>2024-01-01T00:00:00</egb:startObiekt><egb:startWersjaObiekt>2024-01-01T00:00:00</egb:startWersjaObiekt>
 <egb:podstawaUtworzeniaWersjiObiektu xlink:href="#{NS}_11111111-1111-1111-1111-111111111111_2024-01-01T00-00-00"/>
 <egb:idDzialki>999901_2.0001.12/1</egb:idDzialki>
 <egb:geometria><gml:Polygon gml:id="g1" srsName="urn:ogc:def:crs:EPSG::2178"><gml:exterior><gml:LinearRing>
  <gml:posList>5763000 7468000 5763000 7468100 5763100 7468100 5763100 7468000 5763000 7468000</gml:posList>
 </gml:LinearRing></gml:exterior></gml:Polygon></egb:geometria>
 <egb:poleEwidencyjne>1.0000</egb:poleEwidencyjne><egb:dokladnoscReprezentacjiPola>1</egb:dokladnoscReprezentacjiPola>
 <egb:klasouzytek><egb:EGB_Klasouzytek><egb:OFU>Ls</egb:OFU></egb:EGB_Klasouzytek></egb:klasouzytek>
 <egb:punktGranicyDzialki xlink:href="#{NS}_22222222-2222-2222-2222-222222222222_2020-01-01T00-00-00"/>
 <egb:punktGranicyDzialki xlink:href="#{NS}_44444444-4444-4444-4444-444444444444_2019-01-01T00-00-00"/>
</egb:EGB_DzialkaEwidencyjna></gml:featureMember>
<gml:featureMember><egb:EGB_KonturUzytkuGruntowego gml:id="{NS}_55555555-5555-5555-5555-555555555555_2024-01-01T00-00-00">
 <egb:idIIP><egb:EGB_IdentyfikatorIIP><egb:lokalnyId>55555555-5555-5555-5555-555555555555</egb:lokalnyId>
  <egb:przestrzenNazw>{NS}</egb:przestrzenNazw><egb:wersjaId>2024-01-01T00:00:00</egb:wersjaId></egb:EGB_IdentyfikatorIIP></egb:idIIP>
 <egb:startObiekt>2010-01-01T00:00:00</egb:startObiekt><egb:koniecObiekt>2025-01-01T00:00:00</egb:koniecObiekt>
 <egb:geometria><gml:Polygon gml:id="g2" srsName="urn:ogc:def:crs:EPSG::2178"><gml:exterior><gml:LinearRing>
  <gml:posList>5763000 7468000 5763000 7468100 5763100 7468100 5763000 7468000</gml:posList>
 </gml:LinearRing></gml:exterior></gml:Polygon></egb:geometria>
 <egb:idUzytku>999901_2.0001.UG.7</egb:idUzytku><egb:OFU>Ls</egb:OFU>
</egb:EGB_KonturUzytkuGruntowego></gml:featureMember>
</gml:FeatureCollection>
"""


def _file(tmp_path: Path) -> Path:
    p = tmp_path / "test.gml"
    p.write_text(GML, encoding="utf-8")
    return p


def test_detect_header():
    head = parser.detect(GML.encode())
    assert head.profil == "egib2021"
    assert head.system.startswith("GEO-INFO Mapa")
    assert head.schemat == "EGIB_1.9.XSD"


def test_parse_types_keys_refs_and_geometry(tmp_path):
    objs = list(parser.parse(str(_file(tmp_path)), parser.EGIB2021, {"dzialka", "uzytek", "zmiana", "punkt"}))
    by = {o.rodzaj: o for o in objs}
    assert set(by) == {"zmiana", "punkt", "dzialka", "uzytek"}
    d = by["dzialka"]
    assert d.klucz == f"{NS}:33333333-3333-3333-3333-333333333333"
    assert d.atrybuty == {"id_dzialki": "999901_2.0001.12/1", "pole_ewid_ha": "1.0000", "dokladnosc_pola": "1"}
    assert len(d.odwolania["punkty"]) == 2 and d.odwolania["zmiana"].startswith(NS)
    assert d.gml.startswith("<gml:Polygon") and "posList" in d.gml and d.srs.endswith("2178")
    assert "klasouzytek" not in d.atrybuty                     # klasoużytki pomijane
    assert by["uzytek"].obiekt_do == "2025-01-01T00:00:00"     # koniecObiekt
    assert by["zmiana"].atrybuty["opis"] == "Podział działki 12" and by["zmiana"].gml is None


def test_parse_respects_selected_layers(tmp_path):
    objs = list(parser.parse(str(_file(tmp_path)), parser.EGIB2021, {"dzialka"}))
    assert [o.rodzaj for o in objs] == ["dzialka"]


def test_key_from_gml_id_fallback():
    assert parser.klucz_z_gml_id(f"{NS}_44444444-4444-4444-4444-444444444444_2019-01-01T00-00-00") == \
        f"{NS}:44444444-4444-4444-4444-444444444444"
    assert parser.klucz_z_gml_id("cos_innego") is None


def test_klauzula():
    data = ("<?xml version='1.0' encoding='UTF-8'?><schema xmlns:xs='http://www.w3.org/2001/XMLSchema'>"
            "<xs:MaterialZasobuUdostepniony xmlns='http://www.w3.org/2001/XMLSchema'>"
            "<organProwadzacyZasob>Starosta Powiatu Przykładowego</organProwadzacyZasob><identyfikatorEwidencyjny/>"
            "<dataWykonaniaKopii>2026-01-02</dataWykonaniaKopii><sumaKontrolna>abc123</sumaKontrolna>"
            "</xs:MaterialZasobuUdostepniony></schema>").encode()
    kl = parser.read_klauzula(data)
    assert kl == {"organProwadzacyZasob": "Starosta Powiatu Przykładowego", "dataWykonaniaKopii": "2026-01-02",
                  "sumaKontrolna": "abc123"}
