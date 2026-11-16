-- Każda wtyczka ma własny schemat; migracje wykonuje Sophia przy włączeniu wtyczki (jednorazowo każdy plik).
CREATE SCHEMA IF NOT EXISTS plugin_przyklad;

CREATE TABLE plugin_przyklad.notatka (
    id         serial PRIMARY KEY,
    tresc      text NOT NULL CHECK (length(tresc) BETWEEN 1 AND 2000),
    autor      text NOT NULL,
    utworzono  timestamptz NOT NULL DEFAULT now()
);
