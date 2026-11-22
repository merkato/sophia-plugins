-- Motyw przypisany do projektu (zamiast motywu ogólnego, gdy ten projekt jest otwarty w mapie).
CREATE TABLE wyglad.projekt (
    projekt    text PRIMARY KEY,               -- slug projektu Sophii
    motyw      text NOT NULL,                  -- identyfikator motywu wbudowanego albo wyglad.motyw.id
    zmieniono  timestamptz NOT NULL DEFAULT now(),
    zmienil    text
);
