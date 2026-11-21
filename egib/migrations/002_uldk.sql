-- Uzupełnienie rejestru działkami z ULDK (GUGiK) tam, gdzie nie ma jeszcze danych EGiB od starostwa.
-- Rejestr `egib.dzialka` zostaje wyłącznie pod importy GML; działka z ULDK żyje obok i jest zastępowana,
-- gdy import GML przyniesie działkę o tym samym identyfikatorze.
CREATE TABLE egib.dzialka_uldk (
    id_dzialki        text PRIMARY KEY,
    id_obrebu         text GENERATED ALWAYS AS (regexp_replace(id_dzialki, '\.[^.]+$', '')) STORED,
    numer             text,
    obreb             text,
    gmina             text,
    powiat            text,
    wojewodztwo       text,
    geom              geometry(MultiPolygon, 2180) NOT NULL,
    pobrano           timestamptz NOT NULL DEFAULT now(),
    pobral_login      text,
    zastapiona_importem bigint REFERENCES egib.import (id)   -- import GML, który przyniósł tę działkę
);
CREATE INDEX ON egib.dzialka_uldk USING gist (geom);

-- Działki bieżące z obu źródeł: EGiB (import GML) ma pierwszeństwo, ULDK uzupełnia braki.
-- Klucz `klucz`: identyfikator IIP z GML albo „ULDK:<id_dzialki>” — warstwa w projekcie QGIS (klucz = klucz).
CREATE VIEW egib.v_dzialka AS
    SELECT d.klucz, d.id_dzialki, d.id_obrebu, d.numer, 'EGiB'::text AS zrodlo, d.pole_ewid_ha,
           d.wazna_od AS od, d.geom
      FROM egib.dzialka d
    UNION ALL
    SELECT 'ULDK:' || u.id_dzialki, u.id_dzialki, u.id_obrebu, u.numer, 'ULDK', NULL::numeric(14, 4),
           u.pobrano, u.geom
      FROM egib.dzialka_uldk u
     WHERE NOT EXISTS (SELECT 1 FROM egib.dzialka d WHERE d.id_dzialki = u.id_dzialki);

-- Pobieranie hurtowe (w tle, z odstępem między zapytaniami do ULDK).
CREATE TABLE egib.uldk_zadanie (
    id            bigserial PRIMARY KEY,
    status        text NOT NULL DEFAULT 'w_toku' CHECK (status IN ('w_toku', 'zakonczone', 'przerwane')),
    ids           text[] NOT NULL,
    zrobione      integer NOT NULL DEFAULT 0,
    wynik         jsonb NOT NULL DEFAULT '{}',    -- {"pobrane": n, "bylo": n, "nie_znaleziono": [...], "bledy": [...]}
    utworzyl_login text,
    utworzono     timestamptz NOT NULL DEFAULT now(),
    zakonczono    timestamptz
);
