# Wtyczka PRG

Granice z **Państwowego Rejestru Granic** (GUGiK) w schemacie `prg` bazy Sophii — z wersjonowaniem, kontrolą
duplikatów i porównaniem z granicami z innego źródła (np. danymi udostępnianymi przez Lasy Państwowe).

## Co importuje
| Rodzaj | Warstwa PRG | Tabela | Kod |
|---|---|---|---|
| obręby ewidencyjne | `A06_Granice_obrebow_ewidencyjnych` | `prg.obreb` | identyfikator TERYT obrębu, np. `146502_8.1217` |
| nadleśnictwa (granice specjalne) | `U06_Nadlesnictwo` | `prg.nadlesnictwo` | `RR-NN` (region / nadleśnictwo), np. `17-02` |

Źródła (te same pola w obu):
- **usługa WFS PRG** (`mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries`) — pobranie
  obszaru w tle, stronami (obręby po 1000, nadleśnictwa po 100); cały kraj nadleśnictw ≈ 70 s;
- **plik z paczki PRG** z geoportalu — ZIP z plikami SHP (wtyczka wybiera plik rodzaju po nazwie: `…obreb…`,
  `…nadles…`, albo jedyny SHP w paczce) albo GML; wysyłany w częściach (do 3 GB).

**Obszar** (od 1.0.1 wybierany po nazwie): obręby — województwo → powiat → jednostka ewidencyjna (gmina, część
gminy, dzielnica); nadleśnictwa — RDLP → nadleśnictwo. „Dodaj do obszaru” dopisuje jednostkę, można dodać kilka;
pusto = cały kraj. Pod spodem obszar to początki kodów (np. powiat `1001`, jednostka `146502_8`, RDLP `17`) — można
je też wpisać. Lista jednostek pochodzi z usługi WFS PRG (bez geometrii) i jest pobierana w tle przy pierwszym
użyciu (kilka minut — usługa PRG odpowiada wolno), potem trzymana w `prg.jednostka`; „odśwież” pobiera ją ponownie. Z pliku trafiają tylko
obiekty z obszaru.

Przykładowe czasy pobrania z WFS: powiat — 243 obręby w 6 s; miasto na prawach powiatu (1742 obręby) — 8 s;
województwo (4732 obręby) — 78 s; wszystkie nadleśnictwa (429) — 81 s.

## Zasady
- Klucz obiektu: identyfikator IIP `przestrzeńNazw:identyfikator` (bez IIP — `JPT:<JPT_ID>`).
- Każda tabela: **stan bieżący** + `<tabela>_historia` (wersje zamknięte z powodem: `nowa_wersja`,
  `brak_w_zakresie`, `zastapiony`). Wszystkie pola źródła zostają w `atrybuty` (jsonb).
- Zmiana wykrywana po skrócie treści (geometria przyciągnięta do 1 cm + wersja, kod, nazwa, organ, ważność) —
  ponowny import tych samych danych niczego nie zmienia.
- **Podgląd przed zapisem**: nowe, zmienione, bez zmian, zamykane, pominięte; zapis dopiero po „Zatwierdź”,
  w jednej transakcji.
- **Zakres**: *pełny* — obiekty rejestru z obszaru, których nie ma w źródle, są zamykane; *uzupełnienie* —
  nic nie jest zamykane, poza obiektem, którego kod przejął obiekt o innym identyfikatorze IIP.
- **Duplikaty**: w stanie bieżącym kod jest unikalny (indeks). W źródle: powtórzony identyfikator — zostaje
  pierwszy; dwa obiekty z tym samym kodem — zostaje nowsza wersja; obiekty bez identyfikatora, kodu albo
  geometrii są pomijane. Pominięte trafiają do raportu importu. Niepoprawne geometrie są poprawiane
  (`ST_MakeValid`) z ostrzeżeniem.

## Porównanie
Strona „PRG — Porównanie”: granice z rejestru zestawiane z tabelą wskazaną przez administratora
(Wtyczki → PRG → Ustawienia: `…_tabela` = `schemat.tabela` z geometrią, opcjonalnie pole kodu i nazwy,
tolerancja w metrach, domyślnie 1 m).
- Parowanie **po kodzie** (porównywane same cyfry: `17-02` = `n1702` = `1702`; obiekty z tym samym kodem są
  łączone) albo, bez pola kodu, **po położeniu** (obiekt zawierający punkt wewnątrz obiektu PRG).
- Wynik dla pary: powierzchnie, część tylko w PRG, część tylko w drugim źródle, najszersza rozbieżność
  (średnica największego koła wpisanego). Stan: *zgodne* (brak rozbieżności szerszych niż tolerancja),
  *rozbieżność*, *tylko w PRG*, *tylko w porównywanych*.
- Obszary rozbieżności szersze niż tolerancja: `prg.porownanie_roznica`; widok `prg.v_roznice_ostatnie`
  (ostatnie gotowe porównanie każdego rodzaju) — warstwa do projektu QGIS. „Na mapie” otwiera rozbieżności
  w projekcie z ustawienia `projekt`.

## Uprawnienia
- `prg.podglad` — „PRG — Rejestr” (wyszukiwanie, historia, pokazanie na mapie) i wyniki porównań.
- `prg.import` — „PRG — Import” (pobieranie, podgląd, zatwierdzanie) i uruchamianie porównań.
