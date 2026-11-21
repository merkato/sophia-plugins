-- Konfiguracja wyszukiwarki: w których warstwach projektu i po których polach szukać, co pokazać w wyniku.
CREATE SCHEMA IF NOT EXISTS wyszukiwarka;

CREATE TABLE wyszukiwarka.warstwa (
    projekt    text NOT NULL,                 -- slug projektu Sophii
    layer_id   text NOT NULL,                 -- identyfikator warstwy z projektu QGIS
    pola       text[] NOT NULL,               -- pola przeszukiwane (fragment tekstu, bez wielkości liter)
    etykieta   text[] NOT NULL DEFAULT '{}',  -- pola opisu wyniku (puste — pola przeszukiwane)
    kolejnosc  integer NOT NULL DEFAULT 0,
    zmieniono  timestamptz NOT NULL DEFAULT now(),
    zmienil    text,
    PRIMARY KEY (projekt, layer_id),
    CHECK (cardinality(pola) > 0)
);
