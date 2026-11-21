# Wtyczka EGiB

Import plików GML z ewidencji gruntów i budynków (pliki od starostw, na wniosek: obręb albo lista działek)
do rejestru w schemacie `egib` bazy Sophii — z wersjonowaniem i historią.

## Co importuje
| Rodzaj | Klasa GML (rozp. 2021) | Tabela |
|---|---|---|
| działki | `EGB_DzialkaEwidencyjna` | `egib.dzialka` |
| użytki gruntowe (całe kontury, nie cięte) | `EGB_KonturUzytkuGruntowego` | `egib.uzytek` |
| obręby, jednostki | `EGB_ObrebEwidencyjny`, `EGB_JednostkaEwidencyjna` | `egib.obreb`, `egib.jednostka` |
| punkty graniczne (opcjonalnie) | `EGB_PunktGraniczny` | `egib.punkt` |
| zmiany (podstawa wersji obiektów) | `EGB_Zmiana` | `egib.zmiana` |

Pomijane: klasoużytki, kontury klasyfikacyjne, dokumenty, operaty, budynki, podmioty (RODO).

## Zasady
- Klucz obiektu: identyfikator IIP `przestrzenNazw:lokalnyId`; bez IIP — identyfikator EGiB.
- Każda tabela: **stan bieżący** (jeden wiersz na obiekt) + `<tabela>_historia` (wersje zamknięte z
  powodem: nowa wersja / koniec obiektu / brak w zakresie). Zapytania o stan bieżący nie płacą za historię.
- Zmiana wykrywana po `wersjaId` i skrócie treści (geometria przyciągnięta do 1 cm po przeliczeniu do
  EPSG:2180 + atrybuty) — ponowny import tego samego pliku niczego nie zmienia.
- **Zakres importu**: *pełne obręby* (obiekty rejestru z tych obrębów, których nie ma w pliku, są
  zamykane) albo *lista działek z wniosku* (nic nie jest zamykane, brakujące działki trafiają do raportu).
- Relacje: `egib.dzialka_uzytek` (przecięcia działka × użytek z powierzchnią i udziałami, przeliczane dla
  zmienionych obiektów, okruchy < 1 m² / < 0,1 % pomijane), `egib.dzialka_punkt`; migawka relacji w historii
  działki. Widok `egib.v_dzialka_uzytki` (użytki działki, udział `Ls`), funkcja `egib.dzialka_na_dzien()`.
- Klauzula (`*.klauzula.xml`) — suma kontrolna sprawdzana z plikiem, organ i data kopii w dzienniku.

## Uprawnienia
- `egib.import` — strona „EGiB — importy”: wysyłka plików, podgląd zmian, zakres, zatwierdzanie.
- `egib.podglad` — strona „EGiB — rejestr”: wyszukiwanie działek, użytki, historia, zmiany;
  `POST /api/plugins/egib/zmiany` — raport zmian dla listy działek (dla wtyczek branżowych).

## Formaty
Profil `egib2021` (`parser.py`) — schemat aplikacyjny EGiB z 2021 r. (sprawdzony na eksporcie GEO-INFO).
Inny system/schemat = nowy profil (mapowanie elementów na te same kolumny).

## Działki z ULDK (od 1.1)
Gdzie nie ma jeszcze GML od starostwa, geometrię działki można pobrać z ULDK (GUGiK) po identyfikatorze
(uprawnienie `egib.uldk`): strona „EGiB — rejestr” → „Dodaj działkę z ULDK”, albo API
`POST /api/plugins/egib/uldk {"id_dzialki": …}`; pobieranie hurtowe w tle: `POST /uldk/zadania {"ids": […]}`
(~1 działka/s, pomija działki, które już mają geometrię).
- `egib.dzialka_uldk` — działki z ULDK (kto i kiedy pobrał); rejestr `egib.dzialka` zostaje tylko pod GML.
- `egib.v_dzialka` — działki bieżące z obu źródeł (`zrodlo` = `EGiB` / `ULDK`, klucz `klucz`; dla ULDK
  `ULDK:<id_dzialki>`). Warstwa działek w projekcie QGIS powinna korzystać z tego widoku.
- Import GML, który przyniesie działkę z ULDK, oznacza ją jako zastąpioną (`zastapiona_importem`);
  widok bierze wtedy dane EGiB.
- **Od 1.2 widok przyjmuje zapis** (triggery `INSTEAD OF`): warstwa `egib.v_dzialka` w projekcie może być
  celem narzędzia pobierania ULDK z Fii (główne okno mapy). Dodanie → `egib.dzialka_uldk` (klucz
  `ULDK:<id>`); zmiana i usunięcie tylko działek z ULDK; działki z importu GML są chronione, duplikaty
  odrzucane. Mapowanie w Fii: identyfikator → `id_dzialki`, numer → `numer`, obręb → `obreb`, gmina →
  `gmina`, powiat → `powiat`, województwo → `wojewodztwo`. Kto może dodawać — prawo edycji warstwy
  w projekcie (grupy projektu); autor zmiany jest w dzienniku zmian Sophii.
