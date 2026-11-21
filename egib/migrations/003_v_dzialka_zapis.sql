-- Widok działek bieżących przyjmuje zapis, żeby jedna warstwa w projekcie służyła do przeglądania
-- i jako cel narzędzia pobierania ULDK (Fia): dodanie → egib.dzialka_uldk; zmiana i usunięcie tylko
-- działek z ULDK; dane z importu GML są chronione. Dochodzą kolumny obreb, gmina, powiat, wojewodztwo
-- (dla EGiB: nazwy obrębu i jednostki z rejestru) — do mapowania pól narzędzia ULDK.
CREATE OR REPLACE VIEW egib.v_dzialka AS
    SELECT d.klucz, d.id_dzialki, d.id_obrebu, d.numer, 'EGiB'::text AS zrodlo, d.pole_ewid_ha,
           d.wazna_od AS od, d.geom,
           (SELECT o.nazwa FROM egib.obreb o WHERE o.id_obrebu = d.id_obrebu LIMIT 1) AS obreb,
           (SELECT j.nazwa FROM egib.jednostka j WHERE j.id_jednostki = split_part(d.id_obrebu, '.', 1) LIMIT 1) AS gmina,
           NULL::text AS powiat, NULL::text AS wojewodztwo
      FROM egib.dzialka d
    UNION ALL
    SELECT 'ULDK:' || u.id_dzialki, u.id_dzialki, u.id_obrebu, u.numer, 'ULDK', NULL::numeric(14, 4),
           u.pobrano, u.geom,
           u.obreb, u.gmina, u.powiat, u.wojewodztwo
      FROM egib.dzialka_uldk u
     WHERE NOT EXISTS (SELECT 1 FROM egib.dzialka d WHERE d.id_dzialki = u.id_dzialki);

CREATE FUNCTION egib.v_dzialka_zapis() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    ident text;
    g geometry;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.zrodlo = 'EGiB' THEN
        RAISE EXCEPTION 'Działka % pochodzi z importu GML — dane EGiB zmienia tylko kolejny import', OLD.id_dzialki
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        DELETE FROM egib.dzialka_uldk WHERE id_dzialki = OLD.id_dzialki;
        RETURN OLD;
    END IF;

    IF NEW.geom IS NOT NULL THEN
        g := ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Force2D(NEW.geom)), 3));
        IF ST_SRID(NEW.geom) NOT IN (0, 2180) THEN
            g := ST_Transform(g, 2180);
        ELSIF ST_SRID(g) = 0 THEN
            g := ST_SetSRID(g, 2180);
        END IF;
        IF ST_IsEmpty(g) THEN
            RAISE EXCEPTION 'Geometria działki musi być poligonem' USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.id_dzialki IS DISTINCT FROM OLD.id_dzialki THEN
            RAISE EXCEPTION 'Nie można zmienić identyfikatora działki (%)', OLD.id_dzialki USING ERRCODE = 'check_violation';
        END IF;
        UPDATE egib.dzialka_uldk SET numer = NEW.numer, obreb = NEW.obreb, gmina = NEW.gmina, powiat = NEW.powiat,
               wojewodztwo = NEW.wojewodztwo, geom = coalesce(g, geom)
         WHERE id_dzialki = OLD.id_dzialki;
        RETURN NEW;
    END IF;

    -- INSERT
    ident := nullif(btrim(coalesce(NEW.id_dzialki, regexp_replace(NEW.klucz, '^ULDK:', ''))), '');
    IF ident IS NULL THEN
        RAISE EXCEPTION 'Brak identyfikatora działki (id_dzialki)' USING ERRCODE = 'not_null_violation';
    END IF;
    IF ident !~ '^\d{6}_\d\.\d{4}\.\S+$' THEN
        RAISE EXCEPTION 'Niepoprawny identyfikator działki: %', ident USING ERRCODE = 'check_violation';
    END IF;
    IF g IS NULL THEN
        RAISE EXCEPTION 'Brak geometrii działki' USING ERRCODE = 'not_null_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM egib.dzialka WHERE id_dzialki = ident) THEN
        RAISE EXCEPTION 'Działka % jest już w rejestrze z importu GML', ident USING ERRCODE = 'unique_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM egib.dzialka_uldk WHERE id_dzialki = ident) THEN
        RAISE EXCEPTION 'Działka % jest już dodana z ULDK', ident USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO egib.dzialka_uldk (id_dzialki, numer, obreb, gmina, powiat, wojewodztwo, geom)
    VALUES (ident, NEW.numer, NEW.obreb, NEW.gmina, NEW.powiat, NEW.wojewodztwo, g);
    NEW.klucz := 'ULDK:' || ident;
    NEW.id_dzialki := ident;
    NEW.id_obrebu := regexp_replace(ident, '\.[^.]+$', '');
    NEW.zrodlo := 'ULDK';
    NEW.pole_ewid_ha := NULL;
    NEW.od := now();
    NEW.geom := g;
    RETURN NEW;
END $$;

CREATE TRIGGER v_dzialka_zapis INSTEAD OF INSERT OR UPDATE OR DELETE ON egib.v_dzialka
    FOR EACH ROW EXECUTE FUNCTION egib.v_dzialka_zapis();
