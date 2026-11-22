"""Porównanie granic z rejestru PRG z granicami z innego źródła (tabela w bazie Sophii, np. dane udostępniane
przez Lasy Państwowe). Tabelę i pola wskazuje administrator w ustawieniach wtyczki.

Parowanie: po kodzie (porównywane są same cyfry: „08-11” = „n0811”, „146502_8.1217” = „14650281217”) albo,
bez pola kodu, po położeniu (obiekt porównywany zawierający punkt wewnątrz obiektu PRG).
Dla każdej pary: część tylko w PRG i część tylko w obiekcie porównywanym; kawałki rozbieżności szersze niż
tolerancja (średnica największego koła wpisanego) trafiają do `prg.porownanie_roznica` — warstwa do QGIS
i podglądu na mapie. Węższe (np. różnica generalizacji wzdłuż całej granicy) liczą się tylko do powierzchni.
"""
from __future__ import annotations

import logging
import re
import traceback

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

log = logging.getLogger("sophia.plugins.prg")

IDENT_RE = r"^[a-z_][a-z0-9_]*$"


class PorownanieError(Exception):
    pass


def _ident(nazwa: str, co: str) -> str:
    if not re.match(IDENT_RE, nazwa or ""):
        raise PorownanieError(f"Niepoprawna nazwa ({co}): „{nazwa}” — małe litery, cyfry i _")
    return nazwa


def tabela_zrodla(conn, tabela: str, pole_kodu: str | None, pole_nazwy: str | None) -> dict:
    """Sprawdza tabelę porównywaną: istnieje, ma geometrię, pola istnieją. Zwraca opis (schemat, tabela,
    kolumna geometrii, SRID, identyfikator wiersza)."""
    if "." not in (tabela or ""):
        raise PorownanieError("Tabela porównywana: podaj „schemat.tabela” (ustawienia wtyczki)")
    schemat, nazwa = (_ident(x.strip(), "tabela") for x in tabela.split(".", 1))
    g = conn.execute("""SELECT f_geometry_column, srid FROM geometry_columns
                         WHERE f_table_schema = %s AND f_table_name = %s ORDER BY f_geometry_column LIMIT 1""",
                     (schemat, nazwa)).fetchone()
    if g is None:
        raise PorownanieError(f"Brak tabeli {schemat}.{nazwa} z geometrią")
    cols = {r[0] for r in conn.execute("""SELECT column_name FROM information_schema.columns
                                            WHERE table_schema = %s AND table_name = %s""", (schemat, nazwa)).fetchall()}
    for pole, co in ((pole_kodu, "pole kodu"), (pole_nazwy, "pole nazwy")):
        if pole and pole not in cols:
            raise PorownanieError(f"Tabela {schemat}.{nazwa} nie ma pola „{pole}” ({co})")
    pk = conn.execute("""SELECT a.attname FROM pg_index i
                           JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                          WHERE i.indrelid = to_regclass(%s) AND i.indisprimary AND cardinality(i.indkey) = 1""",
                      (f'"{schemat}"."{nazwa}"',)).fetchone()
    return {"schemat": schemat, "tabela": nazwa, "geom": g[0], "srid": g[1] or 2180, "id": pk[0] if pk else None}


def _set(conn, por_id: int, status: str | None = None, postep: str | None = None, statystyka: dict | None = None) -> None:
    conn.execute("""UPDATE prg.porownanie SET postep = %s, status = coalesce(%s, status),
                           statystyka = coalesce(%s, statystyka),
                           zakonczono = CASE WHEN %s IN ('gotowe', 'blad') THEN now() ELSE zakonczono END
                     WHERE id = %s""",
                 (postep, status, Jsonb(statystyka) if statystyka is not None else None, status, por_id))
    conn.commit()


def run(conninfo: str, por_id: int) -> None:
    with psycopg.connect(conninfo, autocommit=False) as conn:
        try:
            _run(conn, por_id)
        except Exception as exc:
            conn.rollback()
            log.exception("PRG porównanie %s: błąd", por_id)
            msg = str(exc) if isinstance(exc, PorownanieError) else f"Błąd: {exc}"
            conn.execute("DELETE FROM prg.porownanie_wynik WHERE porownanie_id = %s", (por_id,))
            _set(conn, por_id, "blad", msg, {"blad": msg, "szczegoly": traceback.format_exc()[-1500:]})


def _like(obszar: list[str], kol: sql.Composable) -> sql.Composable:
    if not obszar:
        return sql.SQL("true")
    return sql.SQL("({})").format(sql.SQL(" OR ").join(
        sql.SQL("{} LIKE {}").format(kol, sql.Literal(o.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"))
        for o in obszar))


def _run(conn, por_id: int) -> None:
    cur = conn.execute("SELECT * FROM prg.porownanie WHERE id = %s", (por_id,))
    p = dict(zip([d.name for d in cur.description], cur.fetchone(), strict=True))
    src = tabela_zrodla(conn, p["tabela"], p["pole_kodu"], p["pole_nazwy"])
    tol = float(p["tolerancja_m"])
    obszar = list(p["obszar"] or [])
    _set(conn, por_id, postep="Przygotowanie danych…")

    # PRG z obszaru
    conn.execute(sql.SQL("""CREATE TEMP TABLE t_prg AS
                            SELECT klucz, kod, nazwa, regexp_replace(kod, '\\D', '', 'g') AS kodn, geom
                              FROM {t} r WHERE {wo}""").format(
        t=sql.Identifier("prg", p["rodzaj"]), wo=_like(obszar, sql.SQL("r.kod"))))
    n_prg = conn.execute("SELECT count(*) FROM t_prg").fetchone()[0]
    if n_prg == 0:
        raise PorownanieError("Rejestr PRG nie ma obiektów tego rodzaju w podanym obszarze — najpierw import")
    conn.execute("CREATE INDEX ON t_prg USING gist (geom)")

    # tabela porównywana: geometria w EPSG:2180, poprawiona; przy obszarze — tylko obiekty przy obiektach PRG
    g = sql.Identifier(src["geom"])
    geom = sql.SQL("ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Force2D(ST_Transform(ST_SetSRID({g}, {s}), 2180))), 3))").format(
        g=g, s=sql.Literal(src["srid"]))
    ident = sql.SQL("{}::text").format(sql.Identifier(src["id"])) if src["id"] else sql.SQL("ctid::text")
    kod = sql.SQL("{}::text").format(sql.Identifier(p["pole_kodu"])) if p["pole_kodu"] else sql.SQL("NULL::text")
    nazwa = sql.SQL("{}::text").format(sql.Identifier(p["pole_nazwy"])) if p["pole_nazwy"] else sql.SQL("NULL::text")
    near = (sql.SQL("WHERE {g} && ST_Transform((SELECT ST_SetSRID(ST_Extent(geom), 2180) FROM t_prg), {s})").format(
        g=g, s=sql.Literal(src["srid"])) if obszar else sql.SQL(""))
    conn.execute(sql.SQL("""CREATE TEMP TABLE t_por AS
                            SELECT {id} AS id, {kod} AS kod, {nazwa} AS nazwa,
                                   regexp_replace({kod}, '\\D', '', 'g') AS kodn, {geom} AS geom
                              FROM {t} {near}""").format(
        id=ident, kod=kod, nazwa=nazwa, geom=geom, t=sql.Identifier(src["schemat"], src["tabela"]), near=near))
    conn.execute("DELETE FROM t_por WHERE geom IS NULL OR ST_IsEmpty(geom)")
    conn.execute("CREATE INDEX ON t_por USING gist (geom)")

    # pary: po kodzie (obiekty porównywane z tym samym kodem łączone) albo po położeniu
    if p["pole_kodu"]:
        conn.execute("""CREATE TEMP TABLE t_por_k AS
                        SELECT kodn, string_agg(id, ', ' ORDER BY id) AS id, min(kod) AS kod, min(nazwa) AS nazwa,
                               ST_Multi(ST_Union(geom)) AS geom
                          FROM t_por WHERE kodn <> '' GROUP BY kodn""")
        conn.execute("""CREATE TEMP TABLE t_pary AS
                        SELECT row_number() OVER (ORDER BY coalesce(a.kod, b.kod)) AS nr, a.klucz, b.kodn AS por
                          FROM t_prg a FULL JOIN t_por_k b ON b.kodn = a.kodn""")
        por_src, por_key = "t_por_k", "kodn"
    else:
        conn.execute("""CREATE TEMP TABLE t_pary AS
                        WITH m AS (SELECT a.klucz, (SELECT b.id FROM t_por b WHERE ST_Intersects(b.geom, ST_PointOnSurface(a.geom))
                                                     ORDER BY ST_Area(b.geom) DESC LIMIT 1) AS por FROM t_prg a)
                        SELECT row_number() OVER (ORDER BY x.kod NULLS LAST, x.por) AS nr, x.klucz, x.por
                          FROM (SELECT m.klucz, m.por, a.kod FROM m JOIN t_prg a USING (klucz)
                                UNION ALL SELECT NULL, b.id, NULL FROM t_por b WHERE b.id NOT IN (SELECT por FROM m WHERE por IS NOT NULL)) x""")
        por_src, por_key = "t_por", "id"

    pary = conn.execute("SELECT nr, klucz, por FROM t_pary ORDER BY nr").fetchall()
    conn.execute(sql.SQL("""INSERT INTO prg.porownanie_wynik (porownanie_id, nr, stan, prg_klucz, prg_kod, prg_nazwa,
                                   por_id, por_kod, por_nazwa, pow_prg_ha, pow_por_ha)
                            SELECT {pid}, x.nr,
                                   CASE WHEN a.klucz IS NULL THEN 'tylko_porownywane' WHEN b.{k} IS NULL THEN 'tylko_prg' ELSE 'zgodne' END,
                                   a.klucz, a.kod, a.nazwa, b.id, b.kod, b.nazwa,
                                   round((ST_Area(a.geom) / 10000)::numeric, 4), round((ST_Area(b.geom) / 10000)::numeric, 4)
                              FROM t_pary x LEFT JOIN t_prg a ON a.klucz = x.klucz LEFT JOIN {src} b ON b.{k} = x.por""").format(
        pid=sql.Literal(por_id), k=sql.Identifier(por_key), src=sql.Identifier(por_src)))
    conn.commit()

    # rozbieżności par: kawałki szersze niż tolerancja (koło o średnicy tol ma pole π·(tol/2)² — mniejsze pomijamy od razu)
    q = sql.SQL("""
        WITH g AS (SELECT a.geom AS pa, b.geom AS pb FROM t_prg a, {src} b WHERE a.klucz = %(klucz)s AND b.{k} = %(por)s),
             d AS (SELECT 'tylko_prg' AS strona, (ST_Dump(ST_Difference(pa, pb))).geom AS geom FROM g
                   UNION ALL SELECT 'tylko_porownywane', (ST_Dump(ST_Difference(pb, pa))).geom FROM g),
             m AS (SELECT strona, geom, ST_Area(geom) AS pow,
                          CASE WHEN ST_Area(geom) >= pi() * (%(tol)s / 2.0) ^ 2
                               THEN 2 * (ST_MaximumInscribedCircle(geom)).radius ELSE 0 END AS szer
                     FROM d WHERE ST_Area(geom) > 0),
             ins AS (INSERT INTO prg.porownanie_roznica (porownanie_id, nr, strona, pow_m2, szer_m, geom)
                     SELECT %(pid)s, %(nr)s, strona, round(pow::numeric, 2), round(szer::numeric, 2), geom
                       FROM m WHERE szer >= %(tol)s RETURNING 1)
        SELECT coalesce(sum(pow) FILTER (WHERE strona = 'tylko_prg'), 0) / 10000,
               coalesce(sum(pow) FILTER (WHERE strona = 'tylko_porownywane'), 0) / 10000,
               coalesce(max(szer), 0), (SELECT count(*) FROM ins) FROM m""").format(
        src=sql.Identifier(por_src), k=sql.Identifier(por_key))
    pelne = [(nr, k, por) for nr, k, por in pary if k is not None and por is not None]
    for i, (nr, klucz, por) in enumerate(pelne, 1):
        a_ha, b_ha, szer, n = conn.execute(q, {"klucz": klucz, "por": por, "tol": tol, "pid": por_id, "nr": nr}).fetchone()
        conn.execute("""UPDATE prg.porownanie_wynik SET tylko_prg_ha = round(%s::numeric, 4), tylko_por_ha = round(%s::numeric, 4),
                               max_szer_m = round(%s::numeric, 2), stan = CASE WHEN %s > 0 THEN 'roznica' ELSE 'zgodne' END
                         WHERE porownanie_id = %s AND nr = %s""", (a_ha, b_ha, szer, n, por_id, nr))
        if i % 10 == 0 or i == len(pelne):
            conn.execute("UPDATE prg.porownanie SET postep = %s WHERE id = %s", (f"Porównano {i} z {len(pelne)} par", por_id))
            conn.commit()

    stats = dict(conn.execute("SELECT stan, count(*) FROM prg.porownanie_wynik WHERE porownanie_id = %s GROUP BY stan",
                              (por_id,)).fetchall())
    stats["obiekty_prg"] = n_prg
    stats["obiekty_porownywane"] = conn.execute("SELECT count(*) FROM t_por").fetchone()[0]
    stats["rozbieznosci"] = conn.execute("SELECT count(*) FROM prg.porownanie_roznica WHERE porownanie_id = %s",
                                         (por_id,)).fetchone()[0]
    conn.commit()
    _set(conn, por_id, "gotowe", "Gotowe", stats)
