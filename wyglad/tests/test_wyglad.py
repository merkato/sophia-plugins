"""Wygląd: motywy wbudowane, sprawdzanie kolorów i CSS, arkusz stylów, logo i ikona."""
import io

import pytest
from PIL import Image
from sophia_plugin_wyglad import motyw_dla, przygotuj_logo, rodzaj_obrazka
from sophia_plugin_wyglad.motywy import IDS, WBUDOWANE, buduj_css, sprawdz_css, sprawdz_zmienne


def test_wbudowane_kompletne_i_poprawne():
    assert {"sophia", "grafit", "lasy"} <= set(WBUDOWANE)
    for m in WBUDOWANE.values():
        assert set(m["jasny"]) == set(IDS) and set(m["ciemny"]) == set(IDS)
        assert sprawdz_zmienne(m["jasny"]) == m["jasny"]
    assert WBUDOWANE["lasy"]["jasny"]["topbar-bg"] == "#005023"


def test_sprawdz_zmienne():
    assert sprawdz_zmienne({"accent": " #0a0 ", "bg": "", "line": "rgba(0, 0, 0, .2)"}) == {"accent": "#0a0", "line": "rgba(0, 0, 0, .2)"}
    with pytest.raises(ValueError):
        sprawdz_zmienne({"accent": "red; } body { display: none"})
    with pytest.raises(ValueError):
        sprawdz_zmienne({"--obca": "#fff"})


def test_sprawdz_css():
    assert sprawdz_css(" .brand-name { font-weight: 400 } ") == ".brand-name { font-weight: 400 }"
    for zly in ("@import url(x.css);", "body { background: url(https://example.com/a.png) }",
                "a { background: url( 'data:image/png;base64,AA') }", "</style><script>"):
        with pytest.raises(ValueError):
            sprawdz_css(zly)


def test_buduj_css_tryby():
    m = {"nazwa": "Test", "jasny": {"accent": "#111111"}, "ciemny": {"accent": "#eeeeee"}, "css": ".x { color: red }"}
    auto = buduj_css(m, {"tryb": "auto", "logo_wysokosc": 40})
    assert "--accent: #111111" in auto and "@media (prefers-color-scheme: dark)" in auto and "--accent: #eeeeee" in auto
    assert "--brand-logo-height: 40px" in auto and auto.rstrip().endswith(".x { color: red }")
    ciemny = buduj_css(m, {"tryb": "ciemny"})
    assert "#111111" not in ciemny and "color-scheme: dark" in ciemny and "@media" not in ciemny
    jasny = buduj_css({**m, "ciemny": {}}, {"tryb": "ciemny"})      # bez wersji ciemnej — zawsze jasny
    assert "#111111" in jasny and "color-scheme: light" in jasny
    assert ".brand-name { display: none; }" in buduj_css(m, {"logo": "logo.png", "pokaz_nazwe": False})
    assert ".brand-name { display: none; }" in buduj_css(m, {"logo": None, "pokaz_nazwe": False})   # także bez logo
    assert ".brand-name" not in buduj_css(m, {"logo": "logo.png", "pokaz_nazwe": True})


def test_motyw_projektu():
    u = {"motyw": "sophia", "projekty": {"lasy-dev": "lasy"}}
    assert motyw_dla(u, "lasy-dev") == "lasy"
    assert motyw_dla(u, "inny") == "sophia" and motyw_dla(u, None) == "sophia"


def _obraz(fmt: str, size=(300, 100)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB" if fmt == "JPEG" else "RGBA", size, (0, 80, 35)).save(b, fmt)
    return b.getvalue()


def test_logo(tmp_path):
    assert rodzaj_obrazka(_obraz("PNG")) == "png" and rodzaj_obrazka(_obraz("WEBP")) == "webp"
    assert rodzaj_obrazka(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None
    assert przygotuj_logo(_obraz("PNG"), tmp_path) == "logo.png"
    assert przygotuj_logo(_obraz("WEBP"), tmp_path) == "logo.webp"
    assert not (tmp_path / "logo.png").exists()                     # poprzednie logo usunięte
    with Image.open(tmp_path / "ikona.png") as ik:
        assert ik.size == (64, 64) and ik.mode == "RGBA"
    with pytest.raises(ValueError):
        przygotuj_logo(_obraz("JPEG"), tmp_path)
    with pytest.raises(ValueError):
        przygotuj_logo(b"\x89PNG\r\n\x1a\n" + b"uszkodzony", tmp_path)
    with pytest.raises(ValueError):
        przygotuj_logo(_obraz("PNG", (4100, 10)), tmp_path)
