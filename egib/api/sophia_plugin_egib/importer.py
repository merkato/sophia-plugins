"""Import GML do rejestru EGiB: wczytanie → walidacja → podgląd zmian → scalanie (jedna transakcja).

Pracuje w osobnym wątku na zwykłym (synchronicznym) połączeniu psycopg — parsowanie i COPY dużego pliku
nie blokują pętli asyncio API. Stan i postęp w `egib.import` (status, postep, statystyka, raport).
"""
from __future__ import annotations

import hashlib
import json
import logging
import traceback
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from . import parser

log = logging.getLogger("sophia.plugins.egib")

# kolumny danych tabel rejestru i ich typy (wartości z atrybutów obiektu w tabeli roboczej)
COLS: dict[str, dict[str, str]] = {
    "jednostka": {"id_jednostki": "text", "nazwa": "text"},
    "obreb": {"id_obrebu": "text", "nazwa": "text"},
    "dzialka": {"id_dzialki": "text", "pole_ewid_ha": "numeric", "dokladnosc_pola": "smallint",
                "dodatkowe_info": "text"},
    "uzytek": {"id_uzytku": "text", "ofu": "text"},
    "punkt": {"id_punktu": "text", "sposob_pozyskania": "smallint", "spelnienie_warunkow_dokl": "smallint",
              "rodzaj_stabilizacji": "smallint", "ozn_w_materiale": "text", "dodatkowe_info": "text"},
}
REQUIRED = {"jednostka": "id_jednostki", "obreb": "id_obrebu", "dzialka": "id_dzialki", "uzytek": "ofu",
            "punkt": "id_punktu"}
ORDER = ("jednostka", "obreb", "dzialka", "uzytek", "punkt")     # działka przed użytkiem: migawka relacji
SCOPED = ("obreb", "dzialka", "uzytek")                          # mają id_obrebu — zamykane „brak w zakresie”
MIN_M2, MIN_UDZIAL = 1.0, 0.001
MAX_SKIPPED, MAX_SKIPPED_SHARE = 100, 0.01                       # pojedyncze wadliwe obiekty: pomiń z ostrzeżeniem                                  # okruchy przecięć działka × użytek
GML_ID_RE = r"^(.+?)_([0-9A-Fa-f]{8}-[0-9A-Fa-f-]{27,})_"
STAGING = sql.SQL("egib_staging.obiekt")


def _val(col: str, typ: str) -> sql.Composable:
    raw = sql.SQL("s.atrybuty->>{}").format(sql.Literal(col))
    if typ == "numeric":
        return sql.SQL("CASE WHEN {r} ~ '^-?[0-9]+([.,][0-9]+)?$' THEN replace({r}, ',', '.')::numeric END").format(r=raw)
    if typ == "smallint":
        return sql.SQL("CASE WHEN {r} ~ '^-?[0-9]{{1,4}}$' THEN ({r})::smallint END").format(r=raw)
    return raw


def _set_status(conn, imp_id: int, status: str | None = None, postep: str | None = None, **json_fields):
    sets = [sql.SQL("postep = {}").format(sql.Literal(postep))]
    if status:
        sets.append(sql.SQL("status = {}").format(sql.Literal(status)))
    for k, v in json_fields.items():
        sets.append(sql.SQL("{} = {}").format(sql.Identifier(k), sql.Literal(Jsonb(v))))
    conn.execute(sql.SQL("UPDATE egib.import SET {} WHERE id = {}").format(sql.SQL(", ").join(sets), sql.Literal(imp_id)))
    conn.commit()


# ---------------------------------------------------------------------------- wczytanie


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(conninfo: str, imp_id: int, path: Path) -> None:
    """Wątek: plik → tabela robocza → odwołania, geometrie, skróty → walidacja → domyślny zakres → podgląd."""
    with psycopg.connect(conninfo, autocommit=False) as conn:
        try:
            _load(conn, imp_id, path)
        except Exception as exc:  # błąd zapisany w imporcie, nie wywraca API
            conn.rollback()
            log.exception("EGiB import %s: błąd wczytywania", imp_id)
            conn.execute("DELETE FROM egib_staging.obiekt WHERE import_id = %s", (imp_id,))
            _set_status(conn, imp_id, "blad", f"Błąd: {exc}",
                        raport={"blad": str(exc), "szczegoly": traceback.format_exc()[-2000:]})


def _load(conn, imp_id: int, path: Path) -> None:
    imp = conn.execute("SELECT * FROM egib.import WHERE id = %s", (imp_id,)).fetchone()
    cols = [d.name for d in conn.execute("SELECT * FROM egib.import LIMIT 0").description]
    imp = dict(zip(cols, imp, strict=True))
    _set_status(conn, imp_id, postep="Sprawdzanie pliku…")
    digest = sha256(path)
    warnings: list[str] = []
    kl = imp["klauzula"] or {}
    if kl.get("sumaKontrolna") and kl["sumaKontrolna"].lower() != digest:
        raise ValueError("Suma kontrolna pliku nie zgadza się z klauzulą — plik uszkodzony albo inna wersja")
    prev = conn.execute("SELECT id FROM egib.import WHERE sha256 = %s AND status = 'zatwierdzony' AND id <> %s",
                        (digest, imp_id)).fetchone()
    if prev:
        warnings.append(f"Ten plik był już zatwierdzony (import #{prev[0]}) — ponowne scalenie niczego nie zmieni.")
    with open(path, "rb") as f:
        head = parser.detect(f.read(16384))
    if not head.profil:
        raise ValueError("Nie rozpoznano formatu (brak obsługiwanej przestrzeni nazw EGiB) — potrzebny nowy profil")
    profile = parser.PROFILES[head.profil]
    conn.execute("UPDATE egib.import SET sha256 = %s, system_zrodlowy = %s, schemat = %s, profil = %s WHERE id = %s",
                 (digest, head.system, head.schemat, head.profil, imp_id))
    conn.commit()

    rodzaje = set(imp["warstwy"]) | {"zmiana"}
    counts: dict[str, int] = {}
    srs: dict[str, int] = {}
    _set_status(conn, imp_id, postep="Odczyt obiektów…")
    with conn.cursor() as cur, cur.copy(
            "COPY egib_staging.obiekt (import_id, rodzaj, klucz, gml_id, wersja_id, obiekt_od, wersja_od, obiekt_do, "
            "atrybuty, odwolania, gml) FROM STDIN") as copy:
        for o in parser.parse(str(path), profile, rodzaje):
            copy.write_row((imp_id, o.rodzaj, o.klucz, o.gml_id, o.wersja_id, o.obiekt_od, o.wersja_od, o.obiekt_do,
                            json.dumps(o.atrybuty, ensure_ascii=False), json.dumps(o.odwolania), o.gml))
            counts[o.rodzaj] = counts.get(o.rodzaj, 0) + 1
            if o.srs:
                srs[o.srs] = srs.get(o.srs, 0) + 1
    conn.commit()
    if not counts:
        raise ValueError("Plik nie zawiera obiektów wybranych warstw")
    uklad = max(srs, key=srs.get) if srs else None
    conn.execute("UPDATE egib.import SET uklad_zrodlowy = %s WHERE id = %s", (uklad, imp_id))

    _set_status(conn, imp_id, postep="Duplikaty i odwołania…")
    dup = conn.execute("""SELECT rodzaj, klucz, count(*) FROM egib_staging.obiekt WHERE import_id = %s
                           GROUP BY 1, 2 HAVING count(*) > 1 LIMIT 20""", (imp_id,)).fetchall()
    if dup:
        raise ValueError(f"Powtórzone identyfikatory obiektów w pliku: {[f'{r[0]} {r[1]}' for r in dup[:5]]}")
    conn.execute("""
        UPDATE egib_staging.obiekt o SET zmiana_klucz = coalesce(
            (SELECT z.klucz FROM egib_staging.obiekt z WHERE z.import_id = o.import_id AND z.gml_id = o.odwolania->>'zmiana' LIMIT 1),
            (SELECT m[1] || ':' || m[2] FROM regexp_match(o.odwolania->>'zmiana', %s) m))
         WHERE o.import_id = %s AND o.odwolania ? 'zmiana'""", (GML_ID_RE, imp_id))
    conn.execute("""
        UPDATE egib_staging.obiekt o SET punkty = ARRAY(
            SELECT coalesce(z.klucz, (SELECT m[1] || ':' || m[2] FROM regexp_match(h.href, %s) m), h.href)
              FROM jsonb_array_elements_text(o.odwolania->'punkty') WITH ORDINALITY h(href, i)
              LEFT JOIN egib_staging.obiekt z ON z.import_id = o.import_id AND z.gml_id = h.href
             ORDER BY h.i)
         WHERE o.import_id = %s AND o.rodzaj = 'dzialka' AND o.odwolania ? 'punkty'""", (GML_ID_RE, imp_id))
    conn.commit()

    _set_status(conn, imp_id, postep="Geometrie (przeliczenie do EPSG:2180)…")
    conn.execute("""UPDATE egib_staging.obiekt SET geom = ST_SnapToGrid(ST_Transform(ST_GeomFromGML(gml), 2180), 0.01)
                     WHERE import_id = %s AND gml IS NOT NULL""", (imp_id,))
    # naprawa (GEOS 3.9 na serwerze — bez 'method=structure'); geometria zdegenerowana daje wynik pusty
    # i obiekt jest niżej pomijany z ostrzeżeniem
    fixed = conn.execute("""UPDATE egib_staging.obiekt SET geom = ST_CollectionExtract(ST_MakeValid(geom), 3)
                             WHERE import_id = %s AND rodzaj <> 'punkt' AND geom IS NOT NULL AND NOT ST_IsValid(geom)""",
                         (imp_id,)).rowcount
    conn.execute("""UPDATE egib_staging.obiekt SET geom = ST_Multi(geom)
                     WHERE import_id = %s AND rodzaj NOT IN ('punkt', 'zmiana') AND geom IS NOT NULL""", (imp_id,))
    conn.execute("""UPDATE egib_staging.obiekt SET gml = NULL,
                       skrot = decode(md5(coalesce(encode(ST_AsBinary(geom), 'hex'), '') || atrybuty::text), 'hex')
                     WHERE import_id = %s""", (imp_id,))
    conn.commit()

    # obiekty, których nie da się zaimportować (brak identyfikatora/kodu albo geometria pusta po naprawie):
    # pojedyncze są pomijane z ostrzeżeniem, masowe zatrzymują import (to raczej zły profil niż wada danych)
    problems = []
    skipped: list[str] = []
    for r, col in REQUIRED.items():
        bad = conn.execute("""SELECT klucz, coalesce(atrybuty->>'id_dzialki', atrybuty->>'id_uzytku', atrybuty->>'id_obrebu',
                                       atrybuty->>'id_jednostki', atrybuty->>'id_punktu', klucz),
                                     CASE WHEN atrybuty->>%s IS NULL THEN 'brak „' || %s || '”' ELSE 'pusta geometria' END
                                FROM egib_staging.obiekt WHERE import_id = %s AND rodzaj = %s
                                 AND (atrybuty->>%s IS NULL OR geom IS NULL OR ST_IsEmpty(geom))""",
                           (col, col, imp_id, r, col)).fetchall()
        if not bad:
            continue
        total = counts.get(r, 0)
        if len(bad) > MAX_SKIPPED or len(bad) > total * MAX_SKIPPED_SHARE:
            problems.append(f"{r}: {len(bad)} z {total} obiektów bez wymaganego pola „{col}” albo geometrii")
        else:
            conn.execute("DELETE FROM egib_staging.obiekt WHERE import_id = %s AND rodzaj = %s AND klucz = ANY(%s)",
                         (imp_id, r, [b[0] for b in bad]))
            skipped += [f"{r} {b[1]} ({b[2]})" for b in bad]
    if problems:
        raise ValueError("; ".join(problems))
    if skipped:
        warnings.append(f"Pominięto {len(skipped)} obiektów, których nie da się zaimportować: {', '.join(skipped[:20])}"
                        + (" …" if len(skipped) > 20 else ""))
    if fixed:
        warnings.append(f"Naprawiono geometrię {fixed} obiektów (ST_MakeValid).")

    # domyślny zakres z zawartości pliku (pracownik może go zmienić przed zatwierdzeniem)
    if imp["zakres_typ"] == "obreb":
        conn.execute("""UPDATE egib.import SET zakres_obreby = ARRAY(
                          SELECT DISTINCT regexp_replace(atrybuty->>'id_dzialki', '\\.[^.]+$', '') FROM egib_staging.obiekt
                           WHERE import_id = %s AND rodzaj = 'dzialka' ORDER BY 1) WHERE id = %s""", (imp_id, imp_id))
    else:
        conn.execute("""UPDATE egib.import SET zakres_dzialki = ARRAY(
                          SELECT DISTINCT atrybuty->>'id_dzialki' FROM egib_staging.obiekt
                           WHERE import_id = %s AND rodzaj = 'dzialka' ORDER BY 1) WHERE id = %s""", (imp_id, imp_id))
    conn.commit()
    stats = preview(conn, imp_id)
    _set_status(conn, imp_id, "roboczy", "Gotowy do sprawdzenia i zatwierdzenia",
                statystyka=stats, raport={"obiekty_w_pliku": counts, "uklad": uklad, "ostrzezenia": warnings})


# ---------------------------------------------------------------------------- klasyfikacja i podgląd


def _classification(imp: dict) -> sql.Composable:
    """(rodzaj, klucz, akcja) dla obiektów pliku i obiektów rejestru w zakresie, których brak w pliku."""
    parts = []
    iid = sql.Literal(imp["id"])
    for r in ORDER:
        if r not in imp["warstwy"]:
            continue
        t = sql.Identifier("egib", r)
        parts.append(sql.SQL("""
            SELECT {r}::text AS rodzaj, s.klucz,
                   CASE WHEN s.obiekt_do IS NOT NULL THEN CASE WHEN c.klucz IS NULL THEN 'pomin' ELSE 'koniec' END
                        WHEN c.klucz IS NULL THEN 'nowy'
                        WHEN s.skrot <> c.skrot OR s.wersja_id IS DISTINCT FROM c.wersja_id THEN 'zmieniony'
                        ELSE 'bez_zmian' END AS akcja
              FROM egib_staging.obiekt s LEFT JOIN {t} c ON c.klucz = s.klucz
             WHERE s.import_id = {iid} AND s.rodzaj = {r}""").format(r=sql.Literal(r), t=t, iid=iid))
        if imp["zakres_typ"] == "obreb" and r in SCOPED and imp["zakres_obreby"]:
            parts.append(sql.SQL("""
                SELECT {r}::text, c.klucz, 'brak_w_zakresie' FROM {t} c
                 WHERE c.id_obrebu = ANY({obr}) AND NOT EXISTS (
                       SELECT 1 FROM egib_staging.obiekt s WHERE s.import_id = {iid} AND s.rodzaj = {r} AND s.klucz = c.klucz)""").format(
                r=sql.Literal(r), t=t, iid=iid, obr=sql.Literal(list(imp["zakres_obreby"]))))
    return sql.SQL(" UNION ALL ").join(parts) if parts else sql.SQL("SELECT NULL::text, NULL::text, NULL::text WHERE false")


def _imp(conn, imp_id: int) -> dict:
    cur = conn.execute("SELECT * FROM egib.import WHERE id = %s", (imp_id,))
    return dict(zip([d.name for d in cur.description], cur.fetchone(), strict=True))


def preview(conn, imp_id: int) -> dict:
    imp = _imp(conn, imp_id)
    rows = conn.execute(sql.SQL("SELECT rodzaj, akcja, count(*) FROM ({}) k GROUP BY 1, 2").format(
        _classification(imp))).fetchall()
    out: dict = {}
    for r, a, n in rows:
        out.setdefault(r, {})[a] = n
    out["zmiany_nowe"] = conn.execute(
        """SELECT count(*) FROM egib_staging.obiekt s WHERE s.import_id = %s AND s.rodzaj = 'zmiana'
              AND NOT EXISTS (SELECT 1 FROM egib.zmiana z WHERE z.klucz = s.klucz)""", (imp_id,)).fetchone()[0]
    if imp["zakres_typ"] == "lista_dzialek":
        missing = conn.execute(
            """SELECT array_agg(x ORDER BY x) FROM unnest(%s::text[]) x WHERE NOT EXISTS (
                 SELECT 1 FROM egib_staging.obiekt s WHERE s.import_id = %s AND s.rodzaj = 'dzialka'
                    AND s.atrybuty->>'id_dzialki' = x)""", (list(imp["zakres_dzialki"]), imp_id)).fetchone()[0]
        out["brak_w_pliku"] = missing or []
    return out


# ---------------------------------------------------------------------------- scalanie


def merge(conninfo: str, imp_id: int, user_id: int | None) -> None:
    with psycopg.connect(conninfo, autocommit=False) as conn:
        try:
            stats = _merge(conn, imp_id, user_id)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            log.exception("EGiB import %s: błąd scalania", imp_id)
            _set_status(conn, imp_id, "roboczy", f"Scalanie nie powiodło się: {exc} — rejestr bez zmian")
            return
        conn.execute("DELETE FROM egib_staging.obiekt WHERE import_id = %s", (imp_id,))
        conn.commit()
        log.info("EGiB import %s: zatwierdzony %s", imp_id, stats)


def _merge(conn, imp_id: int, user_id: int | None) -> dict:
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('sophia_egib_merge'))")
    imp = _imp(conn, imp_id)
    iid = sql.Literal(imp_id)
    conn.execute(sql.SQL("CREATE TEMP TABLE kl ON COMMIT DROP AS {}").format(_classification(imp)))
    conn.execute("CREATE INDEX ON kl (rodzaj, klucz)")

    conn.execute("""INSERT INTO egib.zmiana (klucz, nr_zmiany, opis, data_zgloszenia, data_akceptacji, import_id)
                    SELECT klucz, atrybuty->>'nr_zmiany', atrybuty->>'opis',
                           (atrybuty->>'data_zgloszenia')::timestamptz, (atrybuty->>'data_akceptacji')::timestamptz, import_id
                      FROM egib_staging.obiekt WHERE import_id = %s AND rodzaj = 'zmiana'
                    ON CONFLICT (klucz) DO NOTHING""", (imp_id,))

    for r in ORDER:
        if r not in imp["warstwy"]:
            continue
        t, h = sql.Identifier("egib", r), sql.Identifier("egib", f"{r}_historia")
        cols = COLS[r]
        extra = sql.SQL("")
        if r == "dzialka":   # migawka relacji wersji, która przechodzi do historii
            extra = sql.SQL(""", (SELECT array_agg(p.punkt_klucz ORDER BY p.kolejnosc) FROM egib.dzialka_punkt p
                                   WHERE p.dzialka_klucz = c.klucz),
                                (SELECT jsonb_agg(jsonb_build_object('klucz', u.uzytek_klucz, 'ofu', u.ofu, 'pow_m2', u.pow_m2))
                                   FROM egib.dzialka_uzytek u WHERE u.dzialka_klucz = c.klucz)""")
        conn.execute(sql.SQL("""
            INSERT INTO {h}
            SELECT c.*, now(), CASE k.akcja WHEN 'zmieniony' THEN 'nowa_wersja' WHEN 'koniec' THEN 'koniec_obiektu'
                                ELSE 'brak_w_zakresie' END, {iid}{extra}
              FROM {t} c JOIN kl k ON k.rodzaj = {r} AND k.klucz = c.klucz
             WHERE k.akcja IN ('zmieniony', 'koniec', 'brak_w_zakresie')""").format(
            h=h, t=t, iid=iid, r=sql.Literal(r), extra=extra))
        conn.execute(sql.SQL("""DELETE FROM {t} c USING kl k
                                 WHERE k.rodzaj = {r} AND k.klucz = c.klucz AND k.akcja IN ('koniec', 'brak_w_zakresie')""").format(
            t=t, r=sql.Literal(r)))
        sets = sql.SQL(", ").join([sql.SQL("{} = {}").format(sql.Identifier(c), _val(c, ty)) for c, ty in cols.items()])
        conn.execute(sql.SQL("""
            UPDATE {t} c SET {sets}, wersja_id = s.wersja_id, obiekt_od = s.obiekt_od, wersja_od = s.wersja_od,
                   wazna_od = now(), import_id = {iid}, zmiana_klucz = s.zmiana_klucz, skrot = s.skrot, geom = s.geom
              FROM egib_staging.obiekt s JOIN kl k ON k.rodzaj = {r} AND k.klucz = s.klucz AND k.akcja = 'zmieniony'
             WHERE s.import_id = {iid} AND s.rodzaj = {r} AND c.klucz = s.klucz""").format(
            t=t, sets=sets, iid=iid, r=sql.Literal(r)))
        names = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
        vals = sql.SQL(", ").join(_val(c, ty) for c, ty in cols.items())
        conn.execute(sql.SQL("""
            INSERT INTO {t} (klucz, wersja_id, obiekt_od, wersja_od, import_id, zmiana_klucz, skrot, {names}, geom)
            SELECT s.klucz, s.wersja_id, s.obiekt_od, s.wersja_od, {iid}, s.zmiana_klucz, s.skrot, {vals}, s.geom
              FROM egib_staging.obiekt s JOIN kl k ON k.rodzaj = {r} AND k.klucz = s.klucz AND k.akcja = 'nowy'
             WHERE s.import_id = {iid} AND s.rodzaj = {r}""").format(t=t, names=names, vals=vals, iid=iid, r=sql.Literal(r)))

    if "dzialka" in imp["warstwy"]:
        # punkty graniczne działek (odwołania z pliku — także gdy warstwa punktów nie była importowana)
        conn.execute("""DELETE FROM egib.dzialka_punkt p USING kl k
                         WHERE k.rodzaj = 'dzialka' AND k.akcja IN ('nowy', 'zmieniony') AND p.dzialka_klucz = k.klucz""")
        conn.execute("""INSERT INTO egib.dzialka_punkt (dzialka_klucz, punkt_klucz, kolejnosc)
                        SELECT s.klucz, p.k, min(p.i) FROM egib_staging.obiekt s
                          JOIN kl k ON k.rodzaj = 'dzialka' AND k.klucz = s.klucz AND k.akcja IN ('nowy', 'zmieniony')
                          CROSS JOIN LATERAL unnest(s.punkty) WITH ORDINALITY p(k, i)
                         WHERE s.import_id = %s AND s.rodzaj = 'dzialka'
                         GROUP BY s.klucz, p.k""", (imp_id,))
    # działka × użytek: przeliczenie dla działek nowych/zmienionych i działek pod zmienionymi użytkami
    conn.execute("""
        CREATE TEMP TABLE aff ON COMMIT DROP AS
        SELECT klucz FROM kl WHERE rodzaj = 'dzialka' AND akcja IN ('nowy', 'zmieniony')
        UNION
        SELECT d.klucz FROM egib.dzialka d JOIN (
               SELECT u.geom FROM egib.uzytek u JOIN kl k ON k.rodzaj = 'uzytek' AND k.klucz = u.klucz
                WHERE k.akcja IN ('nowy', 'zmieniony')
               UNION ALL
               SELECT h.geom FROM egib.uzytek_historia h WHERE h.import_zamykajacy = %s) g
            ON d.geom && g.geom AND ST_Intersects(d.geom, g.geom)""", (imp_id,))
    conn.execute("DELETE FROM egib.dzialka_uzytek du USING aff a WHERE du.dzialka_klucz = a.klucz")
    conn.execute("""
        INSERT INTO egib.dzialka_uzytek (dzialka_klucz, uzytek_klucz, ofu, pow_m2, udzial_dzialki, udzial_uzytku, import_id)
        SELECT d.klucz, u.klucz, u.ofu, round(x.a::numeric, 2),
               least(1, x.a / nullif(ST_Area(d.geom), 0)), least(1, x.a / nullif(ST_Area(u.geom), 0)), %s
          FROM egib.dzialka d JOIN aff USING (klucz)
          JOIN egib.uzytek u ON u.id_obrebu = d.id_obrebu AND u.geom && d.geom AND ST_Intersects(u.geom, d.geom)
          CROSS JOIN LATERAL (SELECT ST_Area(ST_Intersection(u.geom, d.geom)) AS a) x
         WHERE x.a >= %s AND x.a / nullif(ST_Area(d.geom), 0) >= %s""", (imp_id, MIN_M2, MIN_UDZIAL))

    stats: dict = {}
    for r, a, n in conn.execute("SELECT rodzaj, akcja, count(*) FROM kl GROUP BY 1, 2").fetchall():
        stats.setdefault(r, {})[a] = n
    stats["relacje_dzialka_uzytek_przeliczone"] = conn.execute("SELECT count(*) FROM aff").fetchone()[0]
    old = conn.execute("SELECT statystyka FROM egib.import WHERE id = %s", (imp_id,)).fetchone()[0] or {}
    stats["zmiany_nowe"] = old.get("zmiany_nowe")
    if "brak_w_pliku" in old:
        stats["brak_w_pliku"] = old["brak_w_pliku"]
    # działki z ULDK, które przyszły teraz z GML, przestają być używane (widok v_dzialka bierze EGiB)
    stats["zastapione_uldk"] = conn.execute(
        """UPDATE egib.dzialka_uldk u SET zastapiona_importem = %s
            WHERE zastapiona_importem IS NULL AND wycofana IS NULL
              AND (EXISTS (SELECT 1 FROM egib.dzialka d WHERE d.id_dzialki = u.id_dzialki)
                   OR EXISTS (SELECT 1 FROM egib.dzialka d WHERE d.import_id = %s AND egib.nakladka(d.geom, u.geom)))""",
        (imp_id, imp_id)).rowcount
    # uzupełnienia użytków w miejscach, które objął teraz GML (nowe/zmienione użytki tego importu)
    stats["zastapione_uzupelnienia"] = conn.execute(
        """UPDATE egib.uzytek_uzupelnienie s SET zastapiony_importem = %s
            WHERE s.zastapiony_importem IS NULL
              AND EXISTS (SELECT 1 FROM egib.uzytek u WHERE u.import_id = %s AND egib.nakladka(u.geom, s.geom))""",
        (imp_id, imp_id)).rowcount
    conn.execute("""UPDATE egib.import SET status = 'zatwierdzony', postep = 'Zatwierdzony', statystyka = %s,
                           zatwierdzil = %s, zatwierdzono = now() WHERE id = %s""", (Jsonb(stats), user_id, imp_id))
    return stats
