import zipfile
from datetime import date

import pytest
from sophia_plugin_prg import zrodla

OBR = zrodla.RODZAJE["obreb"]
NAD = zrodla.RODZAJE["nadlesnictwo"]


def test_data_prg():
    assert zrodla.data_prg("2024-02-16T00:00:00") == date(2024, 2, 16)
    assert zrodla.data_prg("19910928") == date(1991, 9, 28)
    assert zrodla.data_prg("0") is None
    assert zrodla.data_prg(None) is None
    assert zrodla.data_prg("20241399") is None


def test_wiersz_nadlesnictwo():
    w = zrodla.wiersz(NAD, {"IIP_PRZEST": "PL.PZGIK.200", "IIP_IDENTY": "abc", "IIP_WERSJA": "2026-05-07",
                            "JPT_KOD_JE": " 17-02 ", "JPT_NAZWA_": "NADLEŚNICTWO TESTOWE", "JPT_ORGAN_": "Dyrektor",
                            "WAZNY_OD": "19910928", "SHAPE_AREA": ".18", "JPT_OPIS": None, "gml_id": "x"})
    assert w["klucz"] == "PL.PZGIK.200:abc"
    assert w["kod"] == "17-02" and w["organ"] == "Dyrektor" and w["wazny_od"] == date(1991, 9, 28)
    assert "SHAPE_AREA" not in w["atrybuty"] and "gml_id" not in w["atrybuty"] and "JPT_OPIS" not in w["atrybuty"]


def test_wiersz_bez_iip():
    w = zrodla.wiersz(OBR, {"JPT_ID": "123", "JPT_KOD_JE": "146502_8.1217", "JPT_ORGAN_": "NZN"})
    assert w["klucz"] == "JPT:123" and w["organ"] is None
    assert zrodla.wiersz(OBR, {})["klucz"] is None


def test_obszar():
    assert zrodla.sprawdz_obszar(OBR, [" 14", "1465", "146502_8", "146502_8.12", ""]) == ["14", "1465", "146502_8", "146502_8.12"]
    assert zrodla.sprawdz_obszar(NAD, ["17", "17-02"]) == ["17", "17-02"]
    for zle in (["1"], ["14%"], ["17-02'"]):
        with pytest.raises(zrodla.ZrodloError):
            zrodla.sprawdz_obszar(NAD if "-" in zle[0] else OBR, zle)
    assert zrodla.w_obszarze("146502_8.1217", ["1465"]) and not zrodla.w_obszarze("146502_8.1217", ["12"])
    assert zrodla.w_obszarze(None, [])


def test_filtr_wfs():
    assert zrodla.filtr_wfs([]) is None
    f = zrodla.filtr_wfs(["17"])
    assert "<fes:Literal>17*</fes:Literal>" in f and "<fes:Or>" not in f
    assert "<fes:Or>" in zrodla.filtr_wfs(["17", "18"])
    p = zrodla.parametry_wfs(NAD, [], 200)
    assert p["startIndex"] == "200" and p["typeNames"] == "ms:U06_Nadlesnictwo" and "FILTER" not in p


def test_plik_w_paczce(tmp_path):
    z = tmp_path / "prg.zip"
    with zipfile.ZipFile(z, "w") as f:
        for n in ("PRG/A06_Granice_obrebow_ewidencyjnych.shp", "PRG/A03_Granice_gmin.shp", "PRG/A03_Granice_gmin.dbf"):
            f.writestr(n, b"")
    assert zrodla.plik_w_paczce(z, OBR) == f"/vsizip/{z}/PRG/A06_Granice_obrebow_ewidencyjnych.shp"
    with pytest.raises(zrodla.ZrodloError):
        zrodla.plik_w_paczce(z, NAD)
    g = tmp_path / "x.gml"
    g.write_text("<x/>")
    assert zrodla.plik_w_paczce(g, NAD) == str(g)
    bad = tmp_path / "x.zip"
    bad.write_text("nie zip")
    with pytest.raises(zrodla.ZrodloError):
        zrodla.plik_w_paczce(bad, NAD)


def test_parsuj_slownik():
    xml = ("<wfs:FeatureCollection><wfs:member><ms:A02><ms:JPT_KOD_JE>1001</ms:JPT_KOD_JE>"
           "<ms:JPT_NAZWA_>powiat bełchatowski</ms:JPT_NAZWA_></ms:A02></wfs:member>"
           "<wfs:member><ms:A02><ms:JPT_NAZWA_>Kraków &amp; okolice</ms:JPT_NAZWA_><ms:JPT_KOD_JE>1261</ms:JPT_KOD_JE></ms:A02></wfs:member>"
           "<wfs:member><ms:A02><ms:JPT_KOD_JE>1001</ms:JPT_KOD_JE><ms:JPT_NAZWA_>dubel</ms:JPT_NAZWA_></ms:A02></wfs:member>"
           "<wfs:member><ms:A02><ms:JPT_KOD_JE></ms:JPT_KOD_JE><ms:JPT_NAZWA_>bez kodu</ms:JPT_NAZWA_></ms:A02></wfs:member>"
           "</wfs:FeatureCollection>").encode()
    assert zrodla.parsuj_slownik(xml) == [("1001", "powiat bełchatowski"), ("1261", "Kraków & okolice")]
    assert set(zrodla.POZIOMY["obreb"]) | set(zrodla.POZIOMY["nadlesnictwo"]) == set(zrodla.SLOWNIK_WARSTWY)
