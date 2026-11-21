-- Uzupełnienia rejestru poza importem GML („dane drugiej kategorii”):
--  * działki: pochodzenie w egib.dzialka_uldk (ULDK albo migracja ze starej bazy) — w widoku zawsze „ULDK”;
--  * użytki: egib.uzytek_uzupelnienie (migracja, cała działka Ls, ręcznie) z regułą „w jednym miejscu jeden
--    użytek” (wspólna część szersza niż 2 cm = konflikt) i widok egib.v_uzytek (GML ma pierwszeństwo);
--  * kandydaci: egib.uzytek_kandydat — obrysy do przyjęcia; nakładające się czekają na decyzję (scal / zostaw).
ALTER TABLE egib.dzialka_uldk ADD COLUMN pochodzenie text NOT NULL DEFAULT 'ULDK'
    CHECK (pochodzenie IN ('ULDK', 'migracja'));

-- Konflikt dwóch obrysów: wnętrza się przecinają, a część wspólna ma gdzieś więcej niż 2 cm szerokości
-- (po zwężeniu o 1 cm z każdej strony coś zostaje). Przesunięcia węzłów do 2 cm nie są konfliktem.
CREATE FUNCTION egib.nakladka(a geometry, b geometry) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT a && b AND ST_Relate(a, b, '2********') AND NOT ST_IsEmpty(ST_Buffer(ST_Intersection(a, b), -0.01))
$$;

CREATE TABLE egib.uzytek_uzupelnienie (
    id                  bigserial PRIMARY KEY,
    ofu                 text NOT NULL DEFAULT 'Ls',
    geom                geometry(MultiPolygon, 2180) NOT NULL,
    pochodzenie         text NOT NULL CHECK (pochodzenie IN ('migracja', 'cala_dzialka', 'recznie')),
    zrodla              jsonb NOT NULL DEFAULT '[]',        -- np. klucze ze starej bazy (działka|sprawa|nr)
    pow_ewid_ha         numeric(14, 4),                     -- z ewidencji, jeśli znana (może różnić się od geometrii)
    utworzono           timestamptz NOT NULL DEFAULT now(),
    utworzyl_login      text,
    zastapiony_importem bigint REFERENCES egib.import (id)  -- import GML, który objął to miejsce
);
CREATE INDEX ON egib.uzytek_uzupelnienie USING gist (geom) WHERE zastapiony_importem IS NULL;

CREATE FUNCTION egib.uzytek_uzupelnienie_kontrola() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    inny text;
BEGIN
    NEW.geom := ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Force2D(NEW.geom)), 3));
    IF ST_IsEmpty(NEW.geom) THEN
        RAISE EXCEPTION 'Użytek musi być poligonem' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.zastapiony_importem IS NOT NULL THEN
        RETURN NEW;
    END IF;
    SELECT 'uzupełnienie #' || s.id INTO inny FROM egib.uzytek_uzupelnienie s
     WHERE s.zastapiony_importem IS NULL AND s.id IS DISTINCT FROM NEW.id AND egib.nakladka(s.geom, NEW.geom) LIMIT 1;
    IF inny IS NULL THEN
        SELECT 'użytek EGiB ' || u.id_uzytku INTO inny FROM egib.uzytek u WHERE egib.nakladka(u.geom, NEW.geom) LIMIT 1;
    END IF;
    IF inny IS NOT NULL THEN
        RAISE EXCEPTION 'Użytek nachodzi na: % (w jednym miejscu może być tylko jeden użytek)', inny
            USING ERRCODE = 'exclusion_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER kontrola BEFORE INSERT OR UPDATE OF geom, zastapiony_importem ON egib.uzytek_uzupelnienie
    FOR EACH ROW EXECUTE FUNCTION egib.uzytek_uzupelnienie_kontrola();

CREATE TABLE egib.uzytek_kandydat (
    id               bigserial PRIMARY KEY,
    partia           text NOT NULL,                 -- np. 'migracja' — jedna paczka obrysów do przyjęcia
    grupa            integer,                        -- obrysy nakładające się na siebie (do jednej decyzji)
    ofu              text NOT NULL DEFAULT 'Ls',
    geom             geometry(MultiPolygon, 2180) NOT NULL,
    zrodla           jsonb NOT NULL DEFAULT '[]',
    pow_ewid_ha      numeric(14, 4),
    status           text NOT NULL DEFAULT 'oczekuje' CHECK (status IN ('oczekuje', 'przyjety', 'scalony', 'odrzucony')),
    uwaga            text,                           -- np. konflikt z istniejącym użytkiem
    uzupelnienie_id  bigint REFERENCES egib.uzytek_uzupelnienie (id),
    rozstrzygnal     text,
    rozstrzygnieto   timestamptz
);
CREATE INDEX ON egib.uzytek_kandydat (partia, status, grupa);

-- Użytki bieżące: z importu GML + uzupełnienia (tylko niezastąpione). Klucz: IIP z GML albo „UZU:<id>”.
CREATE VIEW egib.v_uzytek AS
    SELECT u.klucz, u.id_uzytku, u.id_obrebu, u.ofu, 'EGiB'::text AS zrodlo, NULL::text AS pochodzenie,
           NULL::numeric(14, 4) AS pow_ewid_ha, u.geom
      FROM egib.uzytek u
    UNION ALL
    SELECT 'UZU:' || s.id, NULL, NULL, s.ofu, 'uzupełnienie',
           CASE s.pochodzenie WHEN 'migracja' THEN 'migracja' WHEN 'cala_dzialka' THEN 'cała działka (Ls)' ELSE 'ręcznie' END,
           s.pow_ewid_ha, s.geom
      FROM egib.uzytek_uzupelnienie s
     WHERE s.zastapiony_importem IS NULL;

-- Zapis przez widok (warstwa w projekcie): dodanie = uzupełnienie „ręcznie”; użytki z GML chronione.
CREATE FUNCTION egib.v_uzytek_zapis() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    nid bigint;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.zrodlo = 'EGiB' THEN
        RAISE EXCEPTION 'Użytek % pochodzi z importu GML — dane EGiB zmienia tylko kolejny import', OLD.id_uzytku
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        DELETE FROM egib.uzytek_uzupelnienie WHERE id = substr(OLD.klucz, 5)::bigint;
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        UPDATE egib.uzytek_uzupelnienie SET ofu = coalesce(NEW.ofu, ofu), geom = coalesce(NEW.geom, geom),
               pow_ewid_ha = NEW.pow_ewid_ha
         WHERE id = substr(OLD.klucz, 5)::bigint;
        RETURN NEW;
    END IF;
    IF NEW.geom IS NULL THEN
        RAISE EXCEPTION 'Brak geometrii użytku' USING ERRCODE = 'not_null_violation';
    END IF;
    INSERT INTO egib.uzytek_uzupelnienie (ofu, geom, pochodzenie, pow_ewid_ha)
    VALUES (coalesce(nullif(btrim(NEW.ofu), ''), 'Ls'),
            CASE WHEN ST_SRID(NEW.geom) IN (0, 2180) THEN ST_SetSRID(NEW.geom, 2180) ELSE ST_Transform(NEW.geom, 2180) END,
            'recznie', NEW.pow_ewid_ha)
    RETURNING id INTO nid;
    NEW.klucz := 'UZU:' || nid;
    NEW.zrodlo := 'uzupełnienie';
    NEW.pochodzenie := 'ręcznie';
    NEW.id_uzytku := NULL;
    NEW.id_obrebu := NULL;
    NEW.ofu := coalesce(nullif(btrim(NEW.ofu), ''), 'Ls');
    RETURN NEW;
END $$;
CREATE TRIGGER v_uzytek_zapis INSTEAD OF INSERT OR UPDATE OR DELETE ON egib.v_uzytek
    FOR EACH ROW EXECUTE FUNCTION egib.v_uzytek_zapis();
