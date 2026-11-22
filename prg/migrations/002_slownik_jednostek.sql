-- Słownik jednostek do wyboru obszaru importu po nazwie (z usługi WFS PRG, bez geometrii):
-- województwa, powiaty, jednostki ewidencyjne (kod = początek identyfikatora obrębu), RDLP, nadleśnictwa.
CREATE TABLE prg.jednostka (
    poziom  text NOT NULL CHECK (poziom IN ('wojewodztwo', 'powiat', 'jednostka_ew', 'rdlp', 'nadlesnictwo')),
    kod     text NOT NULL,
    nazwa   text NOT NULL,
    pobrano timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (poziom, kod)
);

CREATE TABLE prg.slownik_stan (
    id      boolean PRIMARY KEY DEFAULT true CHECK (id),
    status  text NOT NULL CHECK (status IN ('pobieranie', 'gotowy', 'blad')),
    postep  text,
    pobrano timestamptz
);
