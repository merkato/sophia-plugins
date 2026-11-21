-- Uzupełnienia spoza GML dostają te same atrybuty identyfikujące co obiekty z importu:
--  * użytek: id_obrebu (obręb działki, na której leży największa część) i id_uzytku = <obręb>.UZU.<nr>
--    (jak identyfikator EGiB <obręb>.UG.<nr>, z oznaczeniem uzupełnienia);
--  * działka ze starej bazy: nazwy obrębu i gminy z innych działek tego obrębu/jednostki (ULDK albo GML).
ALTER TABLE egib.uzytek_uzupelnienie ADD COLUMN id_obrebu text, ADD COLUMN id_uzytku text;

CREATE FUNCTION egib.obreb_pod(g geometry) RETURNS text LANGUAGE sql STABLE AS $$
    SELECT coalesce(
        (SELECT v.id_obrebu FROM egib.v_dzialka v WHERE v.geom && g AND ST_Intersects(v.geom, g)
          ORDER BY ST_Area(ST_Intersection(v.geom, g)) DESC LIMIT 1),
        (SELECT o.id_obrebu FROM egib.obreb o WHERE o.geom && g AND ST_Intersects(o.geom, g)
          ORDER BY ST_Area(ST_Intersection(o.geom, g)) DESC LIMIT 1))
$$;

UPDATE egib.uzytek_uzupelnienie SET id_obrebu = egib.obreb_pod(geom);
UPDATE egib.uzytek_uzupelnienie SET id_uzytku = coalesce(id_obrebu || '.', '') || 'UZU.' || id;
ALTER TABLE egib.uzytek_uzupelnienie ALTER COLUMN id_uzytku SET NOT NULL, ADD UNIQUE (id_uzytku);

-- kontrola przy zapisie: obręb i identyfikator wyliczane z geometrii (identyfikator zostaje, gdy obręb bez zmian)
CREATE OR REPLACE FUNCTION egib.uzytek_uzupelnienie_kontrola() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    inny text;
BEGIN
    NEW.geom := ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Force2D(NEW.geom)), 3));
    IF ST_IsEmpty(NEW.geom) THEN
        RAISE EXCEPTION 'Użytek musi być poligonem' USING ERRCODE = 'check_violation';
    END IF;
    NEW.id_obrebu := egib.obreb_pod(NEW.geom);
    IF TG_OP = 'INSERT' OR NEW.id_uzytku IS NULL OR NEW.id_obrebu IS DISTINCT FROM OLD.id_obrebu THEN
        NEW.id_uzytku := coalesce(NEW.id_obrebu || '.', '') || 'UZU.' || NEW.id;
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

CREATE OR REPLACE VIEW egib.v_uzytek AS
    SELECT u.klucz, u.id_uzytku, u.id_obrebu, u.ofu, 'EGiB'::text AS zrodlo, NULL::text AS pochodzenie,
           NULL::numeric(14, 4) AS pow_ewid_ha, u.geom
      FROM egib.uzytek u
    UNION ALL
    SELECT 'UZU:' || s.id, s.id_uzytku, s.id_obrebu, s.ofu, 'uzupełnienie',
           CASE s.pochodzenie WHEN 'migracja' THEN 'migracja' WHEN 'cala_dzialka' THEN 'cała działka (Ls)' ELSE 'ręcznie' END,
           s.pow_ewid_ha, s.geom
      FROM egib.uzytek_uzupelnienie s
     WHERE s.zastapiony_importem IS NULL;

-- dodanie przez widok: identyfikator i obręb wracają do formularza (RETURNING)
CREATE OR REPLACE FUNCTION egib.v_uzytek_zapis() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    nid bigint;
    nidu text;
    nob text;
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
         WHERE id = substr(OLD.klucz, 5)::bigint
        RETURNING id_uzytku, id_obrebu INTO nidu, nob;
        NEW.id_uzytku := nidu;
        NEW.id_obrebu := nob;
        RETURN NEW;
    END IF;
    IF NEW.geom IS NULL THEN
        RAISE EXCEPTION 'Brak geometrii użytku' USING ERRCODE = 'not_null_violation';
    END IF;
    INSERT INTO egib.uzytek_uzupelnienie (ofu, geom, pochodzenie, pow_ewid_ha)
    VALUES (coalesce(nullif(btrim(NEW.ofu), ''), 'Ls'),
            CASE WHEN ST_SRID(NEW.geom) IN (0, 2180) THEN ST_SetSRID(NEW.geom, 2180) ELSE ST_Transform(NEW.geom, 2180) END,
            'recznie', NEW.pow_ewid_ha)
    RETURNING id, id_uzytku, id_obrebu INTO nid, nidu, nob;
    NEW.klucz := 'UZU:' || nid;
    NEW.id_uzytku := nidu;
    NEW.id_obrebu := nob;
    NEW.zrodlo := 'uzupełnienie';
    NEW.pochodzenie := 'ręcznie';
    NEW.ofu := coalesce(nullif(btrim(NEW.ofu), ''), 'Ls');
    RETURN NEW;
END $$;

-- działki ze starej bazy: brakujące nazwy z działek tego samego obrębu / jednostki
UPDATE egib.dzialka_uldk u SET
    obreb = coalesce(u.obreb,
        (SELECT x.obreb FROM egib.dzialka_uldk x WHERE x.id_obrebu = u.id_obrebu AND x.obreb IS NOT NULL LIMIT 1),
        (SELECT o.nazwa FROM egib.obreb o WHERE o.id_obrebu = u.id_obrebu LIMIT 1)),
    gmina = coalesce(u.gmina,
        (SELECT x.gmina FROM egib.dzialka_uldk x WHERE split_part(x.id_dzialki, '.', 1) = split_part(u.id_dzialki, '.', 1)
           AND x.gmina IS NOT NULL LIMIT 1),
        (SELECT j.nazwa FROM egib.jednostka j WHERE j.id_jednostki = split_part(u.id_dzialki, '.', 1) LIMIT 1)),
    powiat = coalesce(u.powiat,
        (SELECT x.powiat FROM egib.dzialka_uldk x WHERE left(x.id_dzialki, 4) = left(u.id_dzialki, 4) AND x.powiat IS NOT NULL LIMIT 1)),
    wojewodztwo = coalesce(u.wojewodztwo,
        (SELECT x.wojewodztwo FROM egib.dzialka_uldk x WHERE left(x.id_dzialki, 2) = left(u.id_dzialki, 2) AND x.wojewodztwo IS NOT NULL LIMIT 1))
 WHERE u.pochodzenie = 'migracja' AND (u.obreb IS NULL OR u.gmina IS NULL OR u.powiat IS NULL OR u.wojewodztwo IS NULL);
