# sophia-plugins

Ogólne wtyczki do [Sophii](https://github.com/merkato/sophia-gis). Jedna wtyczka = jeden katalog
z manifestem `sophia-plugin.json`. Opis systemu wtyczek, manifestu, API i interfejsu:
[docs/WTYCZKI.md w repozytorium Sophii](https://github.com/merkato/sophia-gis/blob/main/docs/WTYCZKI.md).

| Wtyczka | Opis |
|---|---|
| [`egib`](egib/) | Import danych EGiB (GML wg rozporządzenia 2021) z wersjonowaniem: działki, użytki gruntowe, obręby, jednostki, punkty graniczne, zmiany; rejestr ze stanem bieżącym i historią; raport zmian dla listy działek |
| [`wyszukiwarka`](wyszukiwarka/) | Okno „Szukaj” nad mapą: wyszukiwanie w warstwach i polach wskazanych dla projektu; użytkownik znajduje tylko to, co może zobaczyć (prawa warstw, filtr warstwy, zakres danych) |
| [`eksport`](eksport/) | Pobieranie danych do XLSX, CSV, GeoPackage i paczki SHP (mLas): warstwy projektu (cały zakres albo bieżący widok mapy) i zestawienia innych wtyczek; zakres danych użytkownika, dziennik pobrań |
| [`przyklad`](przyklad/) | Wtyczka wzorcowa (notatki) — manifest, migracje, API, uprawnienia, strona WebUI |

## Instalacja na serwerze Sophii
```bash
cd /opt/sophia
scripts/server/plugin.sh install https://github.com/merkato/sophia-plugins egib egib-v1.0.0
```
Potem panel administratora → **Wtyczki**: Włącz, Uprawnienia (grupy / użytkownicy).

## Wersje
Tag per wtyczka: `<id>-v<wersja>` (np. `egib-v1.0.0`), wersja zgodna z `version` w manifeście.

## Testy
```bash
SOPHIA=../sophia scripts/test.sh
```
pytest i ruff w obrazie API Sophii (`sophia-api`), z kodem rdzenia z katalogu `$SOPHIA/api`.
