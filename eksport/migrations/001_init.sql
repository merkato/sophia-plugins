-- Eksport danych: warstwy projektu dostępne do pobrania (i pola) oraz dziennik pobrań.
CREATE SCHEMA IF NOT EXISTS eksport;

CREATE TABLE eksport.warstwa (
    projekt    text NOT NULL,                  -- slug projektu Sophii
    layer_id   text NOT NULL,                  -- identyfikator warstwy z projektu QGIS
    pola       text[] NOT NULL DEFAULT '{}',   -- pola w pliku (puste — wszystkie widoczne pola warstwy)
    kolejnosc  integer NOT NULL DEFAULT 0,
    zmieniono  timestamptz NOT NULL DEFAULT now(),
    zmienil    text,
    PRIMARY KEY (projekt, layer_id)
);

-- kto, co i kiedy pobrał (dane mogą zawierać dane osobowe)
CREATE TABLE eksport.dziennik (
    id         bigserial PRIMARY KEY,
    kiedy      timestamptz NOT NULL DEFAULT now(),
    user_id    integer,
    login      text,
    projekt    text,
    zrodlo     text NOT NULL,                  -- „warstwy” albo identyfikator zestawienia (wtyczka.zestawienie)
    elementy   text[] NOT NULL DEFAULT '{}',   -- nazwy warstw / zestawienia
    format     text NOT NULL,
    zakres     text,                           -- „cały zakres”, „bieżący widok”, parametry zestawienia
    wiersze    integer,
    rozmiar    bigint
);
CREATE INDEX ON eksport.dziennik (kiedy DESC);
