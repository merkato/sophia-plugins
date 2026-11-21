-- W jednym miejscu tylko jedna aktywna działka (część wspólna szersza niż 2 cm = konflikt — egib.nakladka).
-- Działki z ULDK, które przestały być aktualne (w ULDK w tym miejscu jest inna działka), są wycofywane, nie
-- usuwane: zostaje informacja, kiedy, dlaczego i która działka je zastąpiła. Widok v_dzialka pokazuje tylko aktywne.
ALTER TABLE egib.dzialka_uldk
    ADD COLUMN wycofana timestamptz,
    ADD COLUMN wycofanie_powod text,
    ADD COLUMN zastapiona_przez text,       -- identyfikator działki, która zajęła to miejsce
    ADD COLUMN uwagi text;                  -- np. docięcie do sąsiedniej działki (rozbieżność w danych źródłowych)

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
     WHERE u.wycofana IS NULL AND u.zastapiona_importem IS NULL
       AND NOT EXISTS (SELECT 1 FROM egib.dzialka d WHERE d.id_dzialki = u.id_dzialki);

CREATE FUNCTION egib.dzialka_uldk_kontrola() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    inny text;
BEGIN
    IF NEW.wycofana IS NOT NULL OR NEW.zastapiona_importem IS NOT NULL THEN
        RETURN NEW;
    END IF;
    -- zmniejszenie aktywnej działki (docięcie) nie tworzy nowych nakładek — tylko je usuwa
    IF TG_OP = 'UPDATE' AND OLD.wycofana IS NULL AND OLD.zastapiona_importem IS NULL
       AND ST_CoveredBy(NEW.geom, ST_Buffer(OLD.geom, 0.01)) THEN
        RETURN NEW;
    END IF;
    SELECT d.id_dzialki || ' (import GML)' INTO inny FROM egib.dzialka d
     WHERE d.id_dzialki <> NEW.id_dzialki AND egib.nakladka(d.geom, NEW.geom) LIMIT 1;
    IF inny IS NULL THEN
        SELECT u.id_dzialki || ' (ULDK)' INTO inny FROM egib.dzialka_uldk u
         WHERE u.id_dzialki <> NEW.id_dzialki AND u.wycofana IS NULL AND u.zastapiona_importem IS NULL
           AND egib.nakladka(u.geom, NEW.geom) LIMIT 1;
    END IF;
    IF inny IS NOT NULL THEN
        RAISE EXCEPTION 'Działka % nachodzi na aktywną działkę % (w jednym miejscu może być tylko jedna działka)',
            NEW.id_dzialki, inny USING ERRCODE = 'exclusion_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER kontrola BEFORE INSERT OR UPDATE OF geom, wycofana, zastapiona_importem ON egib.dzialka_uldk
    FOR EACH ROW EXECUTE FUNCTION egib.dzialka_uldk_kontrola();

-- zapis przez widok: działka wycofana może wrócić jako aktywna
CREATE OR REPLACE FUNCTION egib.v_dzialka_zapis() RETURNS trigger LANGUAGE plpgsql AS $$
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
    IF EXISTS (SELECT 1 FROM egib.dzialka_uldk WHERE id_dzialki = ident AND wycofana IS NULL AND zastapiona_importem IS NULL) THEN
        RAISE EXCEPTION 'Działka % jest już dodana z ULDK', ident USING ERRCODE = 'unique_violation';
    END IF;
    -- działka wycofana wcześniej wraca jako aktywna (nowa geometria i dane)
    INSERT INTO egib.dzialka_uldk (id_dzialki, numer, obreb, gmina, powiat, wojewodztwo, geom)
    VALUES (ident, NEW.numer, NEW.obreb, NEW.gmina, NEW.powiat, NEW.wojewodztwo, g)
    ON CONFLICT (id_dzialki) DO UPDATE SET numer = EXCLUDED.numer, obreb = EXCLUDED.obreb, gmina = EXCLUDED.gmina,
        powiat = EXCLUDED.powiat, wojewodztwo = EXCLUDED.wojewodztwo, geom = EXCLUDED.geom, pobrano = now(),
        wycofana = NULL, wycofanie_powod = NULL, zastapiona_przez = NULL, zastapiona_importem = NULL;
    NEW.klucz := 'ULDK:' || ident;
    NEW.id_dzialki := ident;
    NEW.id_obrebu := regexp_replace(ident, '\.[^.]+$', '');
    NEW.zrodlo := 'ULDK';
    NEW.pole_ewid_ha := NULL;
    NEW.od := now();
    NEW.geom := g;
    RETURN NEW;
END $$;

