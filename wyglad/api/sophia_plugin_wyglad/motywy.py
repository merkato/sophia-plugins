"""Motywy: zmienne CSS rdzenia Sophii, motywy wbudowane, sprawdzanie wartości i budowa arkusza stylów."""
from __future__ import annotations

import re

# zmienne CSS rdzenia (docs/WTYCZKI.md → Wygląd), w kolejności edytora
ZMIENNE: list[dict] = [
    {"id": "topbar-bg", "etykieta": "Tło", "grupa": "Pasek górny"},
    {"id": "topbar-ink", "etykieta": "Tekst", "grupa": "Pasek górny"},
    {"id": "topbar-muted", "etykieta": "Tekst pomocniczy (użytkownik)", "grupa": "Pasek górny"},
    {"id": "topbar-btn", "etykieta": "Przyciski — tło", "grupa": "Pasek górny"},
    {"id": "topbar-btn-ink", "etykieta": "Przyciski — tekst", "grupa": "Pasek górny"},
    {"id": "topbar-btn-line", "etykieta": "Przyciski — obramowanie", "grupa": "Pasek górny"},
    {"id": "topbar-line", "etykieta": "Dolna krawędź", "grupa": "Pasek górny"},
    {"id": "bg", "etykieta": "Tło aplikacji", "grupa": "Aplikacja"},
    {"id": "surface", "etykieta": "Panele, okna, pola", "grupa": "Aplikacja"},
    {"id": "ink", "etykieta": "Tekst", "grupa": "Aplikacja"},
    {"id": "muted", "etykieta": "Tekst pomocniczy", "grupa": "Aplikacja"},
    {"id": "line", "etykieta": "Linie i obramowania", "grupa": "Aplikacja"},
    {"id": "accent", "etykieta": "Akcent (przyciski główne, zaznaczenia, odnośniki)", "grupa": "Aplikacja"},
    {"id": "accent-ink", "etykieta": "Tekst na akcencie", "grupa": "Aplikacja"},
    {"id": "danger", "etykieta": "Błędy i usuwanie", "grupa": "Aplikacja"},
    {"id": "edit", "etykieta": "Edycja (warstwa edycyjna)", "grupa": "Aplikacja"},
]
IDS = [z["id"] for z in ZMIENNE]

_SOPHIA_JASNY = {
    "bg": "#f6f7f9", "surface": "#ffffff", "ink": "#1d2430", "muted": "#6b7380", "line": "#dfe3e8",
    "accent": "#1f6feb", "accent-ink": "#ffffff", "danger": "#c92a2a", "edit": "#d9480f",
    "topbar-bg": "#ffffff", "topbar-ink": "#1d2430", "topbar-muted": "#6b7380", "topbar-btn": "#ffffff",
    "topbar-btn-ink": "#1d2430", "topbar-btn-line": "#dfe3e8", "topbar-line": "#dfe3e8",
}
_SOPHIA_CIEMNY = {
    "bg": "#12151a", "surface": "#1b1f26", "ink": "#e6e9ee", "muted": "#9aa3ae", "line": "#2c323b",
    "accent": "#4c8dff", "accent-ink": "#ffffff", "danger": "#ff6b6b", "edit": "#ff8a4c",
    "topbar-bg": "#1b1f26", "topbar-ink": "#e6e9ee", "topbar-muted": "#9aa3ae", "topbar-btn": "#1b1f26",
    "topbar-btn-ink": "#e6e9ee", "topbar-btn-line": "#2c323b", "topbar-line": "#2c323b",
}

# Zieleń Lasów Państwowych: Pantone 3305 C, CMYK 100/0/60/51, RGB 0/80/35 (#005023) — kolor firmowy z ksiąg
# identyfikacji wizualnej LP. Pozostałe barwy (tła, linie, wersja ciemna) dobrane do niej, z kontrastem WCAG AA.
_LASY_JASNY = {
    "bg": "#f2f4ef", "surface": "#ffffff", "ink": "#1b2a20", "muted": "#5c6b60", "line": "#d5dcd2",
    "accent": "#005023", "accent-ink": "#ffffff", "danger": "#b3261e", "edit": "#b85c00",
    "topbar-bg": "#005023", "topbar-ink": "#ffffff", "topbar-muted": "#c6dccd", "topbar-btn": "#0b6131",
    "topbar-btn-ink": "#ffffff", "topbar-btn-line": "#3a8058", "topbar-line": "#003a19",
}
_LASY_CIEMNY = {
    "bg": "#0e1611", "surface": "#16211a", "ink": "#e3ebe5", "muted": "#97a89b", "line": "#2a3a2f",
    "accent": "#5cb882", "accent-ink": "#06210f", "danger": "#ff7b70", "edit": "#ff9d57",
    "topbar-bg": "#003a19", "topbar-ink": "#ffffff", "topbar-muted": "#a7c4b0", "topbar-btn": "#074a24",
    "topbar-btn-ink": "#ffffff", "topbar-btn-line": "#21683f", "topbar-line": "#00260f",
}
_GRAFIT_JASNY = {
    "bg": "#f4f5f7", "surface": "#ffffff", "ink": "#1f2329", "muted": "#68707c", "line": "#dcdfe4",
    "accent": "#2563c9", "accent-ink": "#ffffff", "danger": "#c92a2a", "edit": "#d9480f",
    "topbar-bg": "#252a32", "topbar-ink": "#f2f4f7", "topbar-muted": "#aab2bd", "topbar-btn": "#333a44",
    "topbar-btn-ink": "#f2f4f7", "topbar-btn-line": "#4a5360", "topbar-line": "#171a1f",
}
_GRAFIT_CIEMNY = {
    "bg": "#111317", "surface": "#1a1d22", "ink": "#e5e8ec", "muted": "#9aa2ad", "line": "#2d3239",
    "accent": "#5b93f0", "accent-ink": "#0b1220", "danger": "#ff6b6b", "edit": "#ff8a4c",
    "topbar-bg": "#0b0d10", "topbar-ink": "#e5e8ec", "topbar-muted": "#8f98a3", "topbar-btn": "#1d2127",
    "topbar-btn-ink": "#e5e8ec", "topbar-btn-line": "#353b44", "topbar-line": "#000000",
}

WBUDOWANE: dict[str, dict] = {
    "sophia": {"nazwa": "Sophia (domyślny)", "jasny": _SOPHIA_JASNY, "ciemny": _SOPHIA_CIEMNY, "css": "",
               "opis": "Wygląd standardowy: jasny pasek, niebieski akcent."},
    "grafit": {"nazwa": "Grafit", "jasny": _GRAFIT_JASNY, "ciemny": _GRAFIT_CIEMNY, "css": "",
               "opis": "Ciemny pasek górny, spokojne szarości, niebieski akcent."},
    "lasy": {"nazwa": "Lasy Państwowe", "jasny": _LASY_JASNY, "ciemny": _LASY_CIEMNY, "css": "",
             "opis": "Zieleń firmowa LP (Pantone 3305 C, #005023) w pasku i akcentach."},
}

KOLOR_RE = re.compile(r"^(#[0-9a-fA-F]{3,4}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}|rgba?\(\s*[\d.]+%?\s*,\s*[\d.]+%?\s*,\s*[\d.]+%?"
                      r"\s*(,\s*[\d.]+%?\s*)?\)|transparent)$")
# dodatkowy CSS: bez importów i zasobów spoza Sophii (arkusz jest publiczny — także na ekranie logowania)
CSS_ZAKAZANE = re.compile(r"@import|url\(\s*['\"]?\s*(https?:|//|data:|javascript:)|expression\(|</", re.IGNORECASE)
CSS_MAX = 20_000


def sprawdz_zmienne(z: dict) -> dict[str, str]:
    """Tylko znane zmienne i poprawne kolory (#rgb, #rrggbb, #rrggbbaa, rgb(), rgba(), transparent)."""
    out: dict[str, str] = {}
    for k, v in (z or {}).items():
        if k not in IDS:
            raise ValueError(f"Nieznana zmienna: {k}")
        v = str(v).strip()
        if not v:
            continue
        if not KOLOR_RE.match(v):
            raise ValueError(f"Niepoprawny kolor „{v}” ({k})")
        out[k] = v
    return out


def sprawdz_css(css: str) -> str:
    css = (css or "").strip()
    if len(css) > CSS_MAX:
        raise ValueError(f"Dodatkowy CSS jest za długi (najwyżej {CSS_MAX} znaków)")
    if CSS_ZAKAZANE.search(css):
        raise ValueError("Dodatkowy CSS nie może importować arkuszy ani wczytywać zasobów spoza Sophii")
    return css


def _blok(zmienne: dict, schemat: str) -> str:
    linie = [f"  --{k}: {zmienne[k]};" for k in IDS if zmienne.get(k)]
    return ":root {\n" + "\n".join(linie) + f"\n  color-scheme: {schemat};\n}}"


def buduj_css(motyw: dict, ustawienia: dict) -> str:
    """Arkusz dołączany po stylach rdzenia: zmienne trybu jasnego / ciemnego wg ustawienia `tryb`
    (auto — za ustawieniem systemu; motyw bez wersji ciemnej — zawsze jasny), wysokość logo, nazwa w pasku (wyłączalna),
    dodatkowy CSS motywu."""
    jasny, ciemny = motyw.get("jasny") or {}, motyw.get("ciemny") or {}
    tryb = ustawienia.get("tryb") or "auto"
    czesci = [f"/* Sophia — wygląd: motyw „{motyw.get('nazwa', '')}” (wtyczka wyglad) */"]
    if tryb == "ciemny" and ciemny:
        czesci.append(_blok(ciemny, "dark"))
    else:
        czesci.append(_blok(jasny, "light"))
        if tryb == "auto" and ciemny:
            czesci.append("@media (prefers-color-scheme: dark) {\n" + _blok(ciemny, "dark") + "\n}")
    czesci.append(f":root {{ --brand-logo-height: {int(ustawienia.get('logo_wysokosc') or 32)}px; }}")
    if not ustawienia.get("pokaz_nazwe", True):
        czesci.append(".brand-name { display: none; }")
    if motyw.get("css"):
        czesci.append("/* dodatkowy CSS motywu */\n" + motyw["css"])
    return "\n".join(czesci) + "\n"
