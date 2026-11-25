-- Bezpiecznik Sophii (≥ 0.10, app/protection.py): działki i użytki z importu GML są obiektami chronionymi
-- (manifest: "protected") — WebUI pokazuje je tylko do odczytu, API odrzuca zapis. Gdy administrator odblokuje
-- projekt na określony czas, administrator i zarządzający mogą zmienić bieżący wiersz rejestru; Sophia ustawia
-- wtedy w transakcji sophia.odblokowanie = 'on'. Usunięcie danych GML — nadal tylko kolejnym importem.
-- Zmiana ręczna zostaje do importu, który przyniesie nową wersję obiektu (porównanie skrótu GML).

CREATE OR REPLACE FUNCTION egib.odblokowane() RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('sophia.odblokowanie', true), '') = 'on'
$$;

-- geometria z formularza / mapy: poligon(y) w EPSG:2180, MultiPolygon; NULL — bez zmiany
CREATE OR REPLACE FUNCTION egib.geom_2180(g geometry) RETURNS geometry LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    r geometry;
BEGIN
    IF g IS NULL THEN
        RETURN NULL;
    END IF;
    r := ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Force2D(g)), 3));
    IF ST_SRID(g) NOT IN (0, 2180) THEN
        r := ST_Transform(r, 2180);
    ELSIF ST_SRID(r) = 0 THEN
        r := ST_SetSRID(r, 2180);
    END IF;
    IF ST_IsEmpty(r) THEN
        RAISE EXCEPTION 'Geometria musi być poligonem' USING ERRCODE = 'check_violation';
    END IF;
    RETURN r;
END $$;

CREATE OR REPLACE FUNCTION egib.v_dzialka_zapis() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    ident text;
    g geometry;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.zrodlo = 'EGiB' THEN
        -- bezpiecznik Sophii (odblokowanie w administracji, tylko administrator / zarządzający): zmiana bieżącego
        -- wiersza rejestru — powierzchnia ewidencyjna, geometria (numer wynika z identyfikatora); usunięcie zawsze tylko importem
        IF TG_OP = 'UPDATE' AND egib.odblokowane() THEN
            IF NEW.id_dzialki IS DISTINCT FROM OLD.id_dzialki THEN
                RAISE EXCEPTION 'Nie można zmienić identyfikatora działki (%)', OLD.id_dzialki USING ERRCODE = 'check_violation';
            END IF;
            UPDATE egib.dzialka SET pole_ewid_ha = NEW.pole_ewid_ha,
                   geom = coalesce(egib.geom_2180(NEW.geom), geom)
             WHERE klucz = OLD.klucz;
            RETURN NEW;
        END IF;
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

CREATE OR REPLACE FUNCTION egib.v_uzytek_zapis() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    nid bigint;
    nidu text;
    nob text;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.zrodlo = 'EGiB' THEN
        -- bezpiecznik Sophii: zmiana bieżącego wiersza rejestru (OFU, geometria); usunięcie zawsze tylko importem
        IF TG_OP = 'UPDATE' AND egib.odblokowane() THEN
            UPDATE egib.uzytek SET ofu = coalesce(NEW.ofu, ofu), geom = coalesce(egib.geom_2180(NEW.geom), geom)
             WHERE klucz = OLD.klucz;
            RETURN NEW;
        END IF;
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
