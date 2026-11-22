-- Wygląd interfejsu: ustawienia (jeden wiersz) i własne motywy. Motywy wbudowane są w kodzie wtyczki.
CREATE SCHEMA IF NOT EXISTS wyglad;

CREATE SEQUENCE wyglad.motyw_nr;
CREATE TABLE wyglad.motyw (
    id         text PRIMARY KEY DEFAULT 'wlasny-' || nextval('wyglad.motyw_nr'),
    nazwa      text NOT NULL,
    jasny      jsonb NOT NULL DEFAULT '{}',    -- zmienne CSS trybu jasnego: {"accent": "#005023", …}
    ciemny     jsonb NOT NULL DEFAULT '{}',    -- zmienne trybu ciemnego (puste — motyw tylko jasny)
    css        text NOT NULL DEFAULT '',       -- dodatkowe reguły CSS
    zmieniono  timestamptz NOT NULL DEFAULT now(),
    zmienil    text
);

CREATE TABLE wyglad.ustawienia (
    id             boolean PRIMARY KEY DEFAULT true CHECK (id),
    motyw          text NOT NULL DEFAULT 'sophia',   -- identyfikator motywu wbudowanego albo wyglad.motyw.id
    tryb           text NOT NULL DEFAULT 'auto' CHECK (tryb IN ('auto', 'jasny', 'ciemny')),
    nazwa          text NOT NULL DEFAULT '',          -- nazwa aplikacji (pusta — „Sophia”)
    pokaz_nazwe    boolean NOT NULL DEFAULT true,     -- nazwa obok logo
    logo           text,                              -- plik w katalogu wtyczki: logo.png / logo.webp
    logo_wysokosc  integer NOT NULL DEFAULT 32 CHECK (logo_wysokosc BETWEEN 16 AND 64),
    wersja         integer NOT NULL DEFAULT 1,        -- do adresów arkusza i logo (odświeżenie w przeglądarkach)
    zmieniono      timestamptz NOT NULL DEFAULT now(),
    zmienil        text
);
INSERT INTO wyglad.ustawienia DEFAULT VALUES;
