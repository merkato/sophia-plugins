-- Rejestr EGiB: stan bieżący (jeden wiersz na obiekt) + historia (wersje zamknięte, tylko dopisywane).
-- Zamknięcie obiektu = przeniesienie wiersza do <tabela>_historia i usunięcie z tabeli bieżącej.
-- Układ: EPSG:2180. Klucz obiektu: identyfikator IIP „przestrzenNazw:lokalnyId”.

CREATE SCHEMA IF NOT EXISTS egib;
CREATE SCHEMA IF NOT EXISTS egib_staging;

CREATE TABLE egib.slownik_zamkniecia (kod text PRIMARY KEY, opis text NOT NULL);
INSERT INTO egib.slownik_zamkniecia VALUES
    ('nowa_wersja',    'Zastąpiona nowszą wersją z kolejnego eksportu'),
    ('koniec_obiektu', 'Eksport zawiera koniecObiekt'),
    ('brak_w_zakresie','Brak w pełnym eksporcie obrębu, który obejmuje obiekt'),
    ('recznie',        'Zamknięta ręcznie przez pracownika (z uzasadnieniem)');

CREATE TABLE egib.import (
    id              bigserial PRIMARY KEY,
    plik            text NOT NULL,
    rozmiar         bigint NOT NULL,
    sha256          text,
    klauzula        jsonb,                                -- organ, nazwa materiału, data kopii, suma kontrolna
    system_zrodlowy text,
    schemat         text,
    profil          text,
    uklad_zrodlowy  text,
    zakres_typ      text NOT NULL CHECK (zakres_typ IN ('obreb', 'lista_dzialek')),
    zakres_obreby   text[] NOT NULL DEFAULT '{}',
    zakres_dzialki  text[] NOT NULL DEFAULT '{}',
    warstwy         text[] NOT NULL,
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
CREATE INDEX import_sha256 ON egib.import (sha256);

CREATE TABLE egib.zmiana (
    klucz            text PRIMARY KEY,
    nr_zmiany        text,
    opis             text,
    data_zgloszenia  timestamptz,
    data_akceptacji  timestamptz,
    import_id        bigint NOT NULL REFERENCES egib.import (id)
);

CREATE TABLE egib.jednostka (
    klucz text PRIMARY KEY, wersja_id text, obiekt_od timestamptz, wersja_od timestamptz,
    wazna_od timestamptz NOT NULL DEFAULT now(), import_id bigint NOT NULL REFERENCES egib.import (id),
    zmiana_klucz text, skrot bytea NOT NULL,
    id_jednostki text NOT NULL, nazwa text,
    geom geometry(MultiPolygon, 2180)
);

CREATE TABLE egib.obreb (
    klucz text PRIMARY KEY, wersja_id text, obiekt_od timestamptz, wersja_od timestamptz,
    wazna_od timestamptz NOT NULL DEFAULT now(), import_id bigint NOT NULL REFERENCES egib.import (id),
    zmiana_klucz text, skrot bytea NOT NULL,
    id_obrebu text NOT NULL,
    id_jednostki text GENERATED ALWAYS AS (split_part(id_obrebu, '.', 1)) STORED,
    nazwa text,
    geom geometry(MultiPolygon, 2180)
);

CREATE TABLE egib.dzialka (
    klucz text PRIMARY KEY, wersja_id text, obiekt_od timestamptz, wersja_od timestamptz,
    wazna_od timestamptz NOT NULL DEFAULT now(), import_id bigint NOT NULL REFERENCES egib.import (id),
    zmiana_klucz text, skrot bytea NOT NULL,
    id_dzialki text NOT NULL,
    id_obrebu text GENERATED ALWAYS AS (regexp_replace(id_dzialki, '\.[^.]+$', '')) STORED,
    numer text GENERATED ALWAYS AS (substring(id_dzialki from '[^.]+$')) STORED,
    pole_ewid_ha numeric(14, 4), dokladnosc_pola smallint, dodatkowe_info text,
    geom geometry(MultiPolygon, 2180) NOT NULL
);

CREATE TABLE egib.uzytek (
    klucz text PRIMARY KEY, wersja_id text, obiekt_od timestamptz, wersja_od timestamptz,
    wazna_od timestamptz NOT NULL DEFAULT now(), import_id bigint NOT NULL REFERENCES egib.import (id),
    zmiana_klucz text, skrot bytea NOT NULL,
    id_uzytku text NOT NULL,
    id_obrebu text GENERATED ALWAYS AS (split_part(id_uzytku, '.UG.', 1)) STORED,
    ofu text NOT NULL,
    geom geometry(MultiPolygon, 2180) NOT NULL
);

CREATE TABLE egib.punkt (
    klucz text PRIMARY KEY, wersja_id text, obiekt_od timestamptz, wersja_od timestamptz,
    wazna_od timestamptz NOT NULL DEFAULT now(), import_id bigint NOT NULL REFERENCES egib.import (id),
    zmiana_klucz text, skrot bytea NOT NULL,
    id_punktu text NOT NULL,
    sposob_pozyskania smallint, spelnienie_warunkow_dokl smallint, rodzaj_stabilizacji smallint,
    ozn_w_materiale text, dodatkowe_info text,
    geom geometry(Point, 2180) NOT NULL
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['jednostka', 'obreb', 'dzialka', 'uzytek', 'punkt'] LOOP
        EXECUTE format(
            'CREATE TABLE egib.%1$I_historia (LIKE egib.%1$I INCLUDING DEFAULTS EXCLUDING GENERATED,
                 wazna_do timestamptz NOT NULL,
                 zamkniecie text NOT NULL REFERENCES egib.slownik_zamkniecia (kod),
                 import_zamykajacy bigint REFERENCES egib.import (id),
                 PRIMARY KEY (klucz, wazna_od),
                 FOREIGN KEY (import_id) REFERENCES egib.import (id))', t);
        EXECUTE format('CREATE INDEX %1$s_historia_okres ON egib.%1$I_historia (klucz, wazna_do)', t);
    END LOOP;
END $$;
ALTER TABLE egib.dzialka_historia ADD COLUMN punkty text[], ADD COLUMN uzytki jsonb;

CREATE TABLE egib.dzialka_punkt (
    dzialka_klucz text NOT NULL REFERENCES egib.dzialka (klucz) ON DELETE CASCADE,
    punkt_klucz   text NOT NULL,
    kolejnosc     int,
    PRIMARY KEY (dzialka_klucz, punkt_klucz)
);

CREATE TABLE egib.dzialka_uzytek (
    dzialka_klucz  text NOT NULL REFERENCES egib.dzialka (klucz) ON DELETE CASCADE,
    uzytek_klucz   text NOT NULL REFERENCES egib.uzytek (klucz) ON DELETE CASCADE,
    ofu            text NOT NULL,
    pow_m2         numeric(14, 2) NOT NULL,
    udzial_dzialki numeric(7, 6) NOT NULL,
    udzial_uzytku  numeric(7, 6) NOT NULL,
    import_id      bigint NOT NULL REFERENCES egib.import (id),
    PRIMARY KEY (dzialka_klucz, uzytek_klucz)
);

CREATE INDEX jednostka_id     ON egib.jednostka (id_jednostki);
CREATE INDEX obreb_id         ON egib.obreb (id_obrebu);
CREATE INDEX dzialka_id       ON egib.dzialka (id_dzialki);
CREATE INDEX dzialka_obreb    ON egib.dzialka (id_obrebu);
CREATE INDEX uzytek_id        ON egib.uzytek (id_uzytku);
CREATE INDEX uzytek_obreb     ON egib.uzytek (id_obrebu);
CREATE INDEX uzytek_ofu       ON egib.uzytek (ofu);
CREATE INDEX punkt_id         ON egib.punkt (id_punktu);
CREATE INDEX dzialka_uzytek_u ON egib.dzialka_uzytek (uzytek_klucz);
CREATE INDEX dzialka_punkt_p  ON egib.dzialka_punkt (punkt_klucz);
CREATE INDEX jednostka_geom   ON egib.jednostka USING gist (geom);
CREATE INDEX obreb_geom       ON egib.obreb USING gist (geom);
CREATE INDEX dzialka_geom     ON egib.dzialka USING gist (geom);
CREATE INDEX uzytek_geom      ON egib.uzytek USING gist (geom);
CREATE INDEX punkt_geom       ON egib.punkt USING gist (geom);
CREATE INDEX dzialka_hist_id  ON egib.dzialka_historia (id_dzialki);

CREATE FUNCTION egib.dzialka_na_dzien(chwila timestamptz)
RETURNS TABLE (klucz text, id_dzialki text, pole_ewid_ha numeric, geom geometry, wazna_od timestamptz,
               wazna_do timestamptz)
LANGUAGE sql STABLE AS $$
    SELECT klucz, id_dzialki, pole_ewid_ha, geom, wazna_od, NULL::timestamptz
      FROM egib.dzialka WHERE wazna_od <= chwila
    UNION ALL
    SELECT klucz, id_dzialki, pole_ewid_ha, geom, wazna_od, wazna_do
      FROM egib.dzialka_historia WHERE wazna_od <= chwila AND chwila < wazna_do
$$;

CREATE VIEW egib.v_dzialka_uzytki AS
SELECT d.klucz, d.id_dzialki, d.id_obrebu, d.pole_ewid_ha, d.geom,
       coalesce(jsonb_agg(jsonb_build_object('ofu', du.ofu, 'pow_m2', du.pow_m2, 'udzial', du.udzial_dzialki,
                                             'uzytek', du.uzytek_klucz) ORDER BY du.pow_m2 DESC)
                FILTER (WHERE du.uzytek_klucz IS NOT NULL), '[]') AS uzytki,
       coalesce(sum(du.udzial_dzialki) FILTER (WHERE du.ofu = 'Ls'), 0) AS udzial_ls
  FROM egib.dzialka d LEFT JOIN egib.dzialka_uzytek du ON du.dzialka_klucz = d.klucz
 GROUP BY d.klucz, d.id_dzialki, d.id_obrebu, d.pole_ewid_ha, d.geom;

-- Tabele robocze importu (UNLOGGED: szybko, dane i tak są w pliku). Wiersze kasowane po zatwierdzeniu/odrzuceniu.
CREATE UNLOGGED TABLE egib_staging.obiekt (
    import_id   bigint NOT NULL,
    rodzaj      text NOT NULL,          -- dzialka | uzytek | obreb | jednostka | punkt | zmiana
    klucz       text NOT NULL,
    gml_id      text,
    wersja_id   text,
    obiekt_od   timestamptz,
    wersja_od   timestamptz,
    obiekt_do   timestamptz,
    atrybuty    jsonb NOT NULL,
    odwolania   jsonb NOT NULL DEFAULT '{}',   -- surowe xlink:href: {"zmiana": "...", "punkty": [...]}
    gml         text,                   -- geometria GML (do ST_GeomFromGML)
    geom        geometry,               -- 2180, siatka 1 cm
    skrot       bytea,
    zmiana_klucz text,
    punkty      text[]
);
CREATE INDEX obiekt_klucz ON egib_staging.obiekt (import_id, rodzaj, klucz);
CREATE INDEX obiekt_gml_id ON egib_staging.obiekt (import_id, gml_id);
