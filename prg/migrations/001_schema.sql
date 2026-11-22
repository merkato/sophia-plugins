-- Rejestr PRG (Państwowy Rejestr Granic, GUGiK): stan bieżący (jeden wiersz na obiekt) + historia (wersje zamknięte).
-- Zamknięcie obiektu = przeniesienie wiersza do <tabela>_historia i usunięcie z tabeli bieżącej.
-- Układ: EPSG:2180. Klucz obiektu: identyfikator IIP „przestrzeńNazw:identyfikator”, np. „PL.PZGIK.200:9a7f…”.

CREATE SCHEMA IF NOT EXISTS prg;
CREATE SCHEMA IF NOT EXISTS prg_staging;

CREATE TABLE prg.slownik_zamkniecia (kod text PRIMARY KEY, opis text NOT NULL);
INSERT INTO prg.slownik_zamkniecia VALUES
    ('nowa_wersja',     'Zastąpiona nowszą wersją z kolejnego importu'),
    ('brak_w_zakresie', 'Brak w pełnym imporcie obszaru, który obejmuje obiekt'),
    ('zastapiony',      'Ten sam kod dostał obiekt o innym identyfikatorze IIP');

CREATE TABLE prg.import (
    id              bigserial PRIMARY KEY,
    rodzaj          text NOT NULL CHECK (rodzaj IN ('obreb', 'nadlesnictwo')),
    zrodlo          text NOT NULL CHECK (zrodlo IN ('wfs', 'plik')),
    plik            text,
    rozmiar         bigint,
    sha256          text,
    obszar          text[] NOT NULL DEFAULT '{}',        -- początki kodów (TERYT / kod RDLP); pusty = cały kraj
    zakres_typ      text NOT NULL CHECK (zakres_typ IN ('pelny', 'uzupelnienie')),
    status          text NOT NULL DEFAULT 'wysylanie'
                    CHECK (status IN ('wysylanie', 'wczytywanie', 'roboczy', 'scalanie', 'zatwierdzony', 'odrzucony', 'blad')),
    postep          text,
    statystyka      jsonb NOT NULL DEFAULT '{}',
    raport          jsonb NOT NULL DEFAULT '{}',
    utworzyl        int,
    utworzyl_nazwa  text,
    zatwierdzil     int,
    utworzono       timestamptz NOT NULL DEFAULT now(),
    zatwierdzono    timestamptz
);

-- obręby ewidencyjne (PRG A06): kod = identyfikator obrębu TERYT, np. 146501_1.0001
CREATE TABLE prg.obreb (
    klucz      text PRIMARY KEY,
    wersja_id  text,                                       -- IIP_WERSJA
    wazna_od   timestamptz NOT NULL DEFAULT now(),         -- w rejestrze Sophii od
    import_id  bigint NOT NULL REFERENCES prg.import (id),
    skrot      bytea NOT NULL,
    kod        text NOT NULL,
    nazwa      text,
    id_gminy   text GENERATED ALWAYS AS (split_part(kod, '.', 1)) STORED,
    wazny_od   date,                                       -- ważność w PRG
    atrybuty   jsonb NOT NULL DEFAULT '{}',                -- wszystkie pola źródła
    geom       geometry(MultiPolygon, 2180) NOT NULL
);

-- nadleśnictwa (PRG U06, granice specjalne): kod = RR-NN (region / nadleśnictwo), np. 17-02
CREATE TABLE prg.nadlesnictwo (
    klucz      text PRIMARY KEY,
    wersja_id  text,
    wazna_od   timestamptz NOT NULL DEFAULT now(),
    import_id  bigint NOT NULL REFERENCES prg.import (id),
    skrot      bytea NOT NULL,
    kod        text NOT NULL,
    nazwa      text,
    rdlp       text GENERATED ALWAYS AS (split_part(kod, '-', 1)) STORED,
    organ      text,
    wazny_od   date,
    atrybuty   jsonb NOT NULL DEFAULT '{}',
    geom       geometry(MultiPolygon, 2180) NOT NULL
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['obreb', 'nadlesnictwo'] LOOP
        EXECUTE format('CREATE UNIQUE INDEX %1$s_kod ON prg.%1$I (kod)', t);        -- bez dubli kodu w stanie bieżącym
        EXECUTE format('CREATE INDEX %1$s_geom ON prg.%1$I USING gist (geom)', t);
        EXECUTE format(
            'CREATE TABLE prg.%1$I_historia (LIKE prg.%1$I INCLUDING DEFAULTS EXCLUDING GENERATED,
                 wazna_do timestamptz NOT NULL,
                 zamkniecie text NOT NULL REFERENCES prg.slownik_zamkniecia (kod),
                 import_zamykajacy bigint REFERENCES prg.import (id),
                 PRIMARY KEY (klucz, wazna_od),
                 FOREIGN KEY (import_id) REFERENCES prg.import (id))', t);
        EXECUTE format('CREATE INDEX %1$s_historia_kod ON prg.%1$I_historia (kod, wazna_do)', t);
    END LOOP;
END $$;

-- tabela robocza importu (usuwana po zatwierdzeniu / odrzuceniu)
CREATE TABLE prg_staging.obiekt (
    import_id  bigint NOT NULL REFERENCES prg.import (id) ON DELETE CASCADE,
    nr         int NOT NULL,                               -- kolejność w źródle
    klucz      text,
    wersja_id  text,
    kod        text,
    nazwa      text,
    organ      text,
    wazny_od   date,
    atrybuty   jsonb NOT NULL DEFAULT '{}',
    geom       geometry(MultiPolygon, 2180),
    skrot      bytea,
    akcja      text,                                       -- nowy / zmieniony / bez_zmian / pomin
    uwaga      text,
    PRIMARY KEY (import_id, nr)
);
CREATE INDEX obiekt_klucz ON prg_staging.obiekt (import_id, klucz);

-- porównanie z granicami z innego źródła (np. dane udostępniane przez Lasy Państwowe)
CREATE TABLE prg.porownanie (
    id            bigserial PRIMARY KEY,
    rodzaj        text NOT NULL CHECK (rodzaj IN ('obreb', 'nadlesnictwo')),
    tabela        text NOT NULL,                           -- schemat.tabela porównywana
    pole_kodu     text,                                    -- pole z kodem (parowanie po kodzie); puste = po położeniu
    pole_nazwy    text,
    obszar        text[] NOT NULL DEFAULT '{}',
    tolerancja_m  numeric(8, 2) NOT NULL,
    status        text NOT NULL DEFAULT 'w_toku' CHECK (status IN ('w_toku', 'gotowe', 'blad')),
    postep        text,
    statystyka    jsonb NOT NULL DEFAULT '{}',
    utworzyl_nazwa text,
    utworzono     timestamptz NOT NULL DEFAULT now(),
    zakonczono    timestamptz
);

CREATE TABLE prg.porownanie_wynik (
    porownanie_id bigint NOT NULL REFERENCES prg.porownanie (id) ON DELETE CASCADE,
    nr            int NOT NULL,
    stan          text NOT NULL CHECK (stan IN ('zgodne', 'roznica', 'tylko_prg', 'tylko_porownywane')),
    prg_klucz     text,
    prg_kod       text,
    prg_nazwa     text,
    por_id        text,                                    -- identyfikator wiersza tabeli porównywanej
    por_kod       text,
    por_nazwa     text,
    pow_prg_ha    numeric(14, 4),
    pow_por_ha    numeric(14, 4),
    tylko_prg_ha  numeric(14, 4),                          -- w PRG, poza obiektem porównywanym
    tylko_por_ha  numeric(14, 4),                          -- w obiekcie porównywanym, poza PRG
    max_szer_m    numeric(10, 2),                          -- najszersza rozbieżność (średnica koła wpisanego)
    PRIMARY KEY (porownanie_id, nr)
);

-- rozbieżności jako poligony (do podglądu na mapie i w QGIS); tylko szersze niż tolerancja
CREATE TABLE prg.porownanie_roznica (
    id            bigserial PRIMARY KEY,
    porownanie_id bigint NOT NULL REFERENCES prg.porownanie (id) ON DELETE CASCADE,
    nr            int NOT NULL,
    strona        text NOT NULL CHECK (strona IN ('tylko_prg', 'tylko_porownywane')),
    pow_m2        numeric(14, 2) NOT NULL,
    szer_m        numeric(10, 2) NOT NULL,
    geom          geometry(Polygon, 2180) NOT NULL,
    FOREIGN KEY (porownanie_id, nr) REFERENCES prg.porownanie_wynik (porownanie_id, nr) ON DELETE CASCADE
);
CREATE INDEX porownanie_roznica_geom ON prg.porownanie_roznica USING gist (geom);
CREATE INDEX porownanie_roznica_nr ON prg.porownanie_roznica (porownanie_id, nr);

-- rozbieżności z ostatniego gotowego porównania każdego rodzaju (warstwa do projektu QGIS)
CREATE VIEW prg.v_roznice_ostatnie AS
SELECT r.id, p.rodzaj, w.prg_kod, w.prg_nazwa, w.por_kod, w.por_nazwa, r.strona, r.pow_m2, r.szer_m, p.utworzono, r.geom
  FROM prg.porownanie_roznica r
  JOIN prg.porownanie p ON p.id = r.porownanie_id
  JOIN prg.porownanie_wynik w ON w.porownanie_id = r.porownanie_id AND w.nr = r.nr
 WHERE p.id IN (SELECT DISTINCT ON (rodzaj) id FROM prg.porownanie WHERE status = 'gotowe' ORDER BY rodzaj, id DESC);
