"""Import PRG do rejestru: pobranie (WFS albo plik) → tabela robocza → walidacja i duplikaty → podgląd → scalanie.

Pracuje w osobnym wątku na synchronicznym połączeniu psycopg. Stan i postęp w `prg.import`
(status, postep, statystyka, raport). Scalanie: jedna transakcja, rejestr zablokowany na czas zapisu.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import traceback
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from . import zrodla

log = logging.getLogger("sophia.plugins.prg")

STAGING = "prg_staging.obiekt"
PARTIA = 500
# kolumny danych rejestru (poza kluczem, wersją, importem, skrótem i geometrią)
KOLUMNY = {"obreb": ("kod", "nazwa", "wazny_od", "atrybuty"),
           "nadlesnictwo": ("kod", "nazwa", "organ", "wazny_od", "atrybuty")}
MAX_POMINIETE = 200                       # tyle pominiętych obiektów trafia do raportu z nazwy


def _tab(rodzaj: str, hist: bool = False) -> sql.Identifier:
    if rodzaj not in KOLUMNY:
        raise ValueError(rodzaj)
    return sql.Identifier("prg", f"{rodzaj}_historia" if hist else rodzaj)


def _set(conn, imp_id: int, status: str | None = None, postep: str | None = None, **json_fields) -> None:
    sets = [sql.SQL("postep = {}").format(sql.Literal(postep))]
    if status:
        sets.append(sql.SQL("status = {}").format(sql.Literal(status)))
    for k, v in json_fields.items():
        sets.append(sql.SQL("{} = {}").format(sql.Identifier(k), sql.Literal(Jsonb(v))))
    conn.execute(sql.SQL("UPDATE prg.import SET {} WHERE id = {}").format(sql.SQL(", ").join(sets), sql.Literal(imp_id)))
    conn.commit()


def _imp(conn, imp_id: int) -> dict:
    cur = conn.execute("SELECT * FROM prg.import WHERE id = %s", (imp_id,))
    row = cur.fetchone()
    return dict(zip([d.name for d in cur.description], row, strict=True))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------- wczytanie


def load(conninfo: str, imp_id: int, path: Path | None, katalog: Path) -> None:
    """Wątek: źródło → tabela robocza → walidacja → podgląd (status „roboczy” albo „blad”)."""
    with psycopg.connect(conninfo, autocommit=False) as conn:
        try:
            _load(conn, imp_id, path, katalog)
        except Exception as exc:  # błąd zapisany w imporcie, nie wywraca API
            conn.rollback()
            log.exception("PRG import %s: błąd wczytywania", imp_id)
            conn.execute("DELETE FROM prg_staging.obiekt WHERE import_id = %s", (imp_id,))
            msg = str(exc) if isinstance(exc, zrodla.ZrodloError) else f"Błąd: {exc}"
            _set(conn, imp_id, "blad", msg, raport={"blad": msg, "szczegoly": traceback.format_exc()[-2000:]})
        finally:
            shutil.rmtree(katalog, ignore_errors=True)


def _insert(conn, imp_id: int, rodzaj: zrodla.Rodzaj, srid: int | None, rows, obszar: list[str], nr: int) -> tuple[int, int]:
    """Wiersze źródła → tabela robocza (tylko z obszaru). Zwraca (następny nr, ile poza obszarem)."""
    if srid is None:
        raise zrodla.ZrodloError("Plik bez układu współrzędnych (brak .prj) — oczekiwano EPSG:2180")
    q = (f"INSERT INTO {STAGING} (import_id, nr, klucz, wersja_id, kod, nazwa, organ, wazny_od, atrybuty, geom) "
         "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
         "  ST_Multi(ST_CollectionExtract(ST_Force2D(ST_Transform(ST_GeomFromWKB(%s, %s), 2180)), 3)))")
    poza, batch = 0, []
    with conn.cursor() as cur:
        for pola, wkb in rows:
            w = zrodla.wiersz(rodzaj, pola)
            if not zrodla.w_obszarze(w["kod"], obszar):
                poza += 1
                continue
            nr += 1
            batch.append((imp_id, nr, w["klucz"], w["wersja_id"], w["kod"], w["nazwa"], w["organ"], w["wazny_od"],
                          Jsonb(w["atrybuty"]), wkb, srid))
            if len(batch) >= PARTIA:
                cur.executemany(q, batch)
                batch.clear()
        if batch:
            cur.executemany(q, batch)
    return nr, poza


def _load(conn, imp_id: int, path: Path | None, katalog: Path) -> None:
    imp = _imp(conn, imp_id)
    rodzaj = zrodla.RODZAJE[imp["rodzaj"]]
    obszar = list(imp["obszar"] or [])
    conn.execute("DELETE FROM prg_staging.obiekt WHERE import_id = %s", (imp_id,))
    nr, poza = 0, 0
    if imp["zrodlo"] == "wfs":
        katalog.mkdir(parents=True, exist_ok=True)
        _set(conn, imp_id, postep="Pobieranie z usługi WFS PRG…")

        def postep(n: int) -> None:
            _set(conn, imp_id, postep=f"Pobieranie z usługi WFS PRG… {n} obiektów")
        for strona in zrodla.pobierz_strony(rodzaj, obszar, katalog, postep):
            srid, rows = zrodla.czytaj(strona)
            nr, p = _insert(conn, imp_id, rodzaj, srid, rows, obszar, nr)
            poza += p
            strona.unlink(missing_ok=True)
            conn.commit()
    else:
        assert path is not None
        _set(conn, imp_id, postep="Sprawdzanie pliku…")
        conn.execute("UPDATE prg.import SET sha256 = %s WHERE id = %s", (sha256(path), imp_id))
        srid, rows = zrodla.czytaj(zrodla.plik_w_paczce(path, rodzaj))
        _set(conn, imp_id, postep="Wczytywanie obiektów…")
        nr, poza = _insert(conn, imp_id, rodzaj, srid, rows, obszar, nr)
        conn.commit()
    if nr == 0:
        raise zrodla.ZrodloError("Źródło nie zawiera obiektów z podanego obszaru" if obszar else "Źródło nie zawiera obiektów")
    _set(conn, imp_id, postep="Walidacja…")
    raport = validate(conn, imp_id)
    raport["obiekty_w_zrodle"] = nr
    if poza:
        raport["poza_obszarem"] = poza
    stats = preview(conn, imp_id)
    _set(conn, imp_id, "roboczy", "Do zatwierdzenia", statystyka=stats, raport=raport)


def validate(conn, imp_id: int) -> dict:
    """Pomija obiekty bez klucza, kodu albo geometrii i duble; poprawia geometrie niepoprawne; liczy skróty."""
    p = {"i": imp_id}
    conn.execute(f"""UPDATE {STAGING} SET akcja = 'pomin', uwaga = CASE
                         WHEN klucz IS NULL THEN 'brak identyfikatora IIP'
                         WHEN kod IS NULL THEN 'brak kodu'
                         ELSE 'brak geometrii' END
                     WHERE import_id = %(i)s AND (klucz IS NULL OR kod IS NULL OR geom IS NULL OR ST_IsEmpty(geom))""", p)
    # ten sam obiekt dwa razy (np. strony WFS) — zostaje pierwszy
    conn.execute(f"""UPDATE {STAGING} s SET akcja = 'pomin', uwaga = 'powtórzony identyfikator IIP'
                       FROM (SELECT nr, row_number() OVER (PARTITION BY klucz ORDER BY nr) AS k
                               FROM {STAGING} WHERE import_id = %(i)s AND akcja IS NULL) d
                      WHERE s.import_id = %(i)s AND s.nr = d.nr AND d.k > 1""", p)
    # dwa różne obiekty z tym samym kodem — zostaje nowsza wersja, reszta do raportu
    conn.execute(f"""UPDATE {STAGING} s SET akcja = 'pomin', uwaga = 'ten sam kod co inny obiekt w źródle'
                       FROM (SELECT nr, row_number() OVER (PARTITION BY kod ORDER BY wersja_id DESC NULLS LAST, nr) AS k
                               FROM {STAGING} WHERE import_id = %(i)s AND akcja IS NULL) d
                      WHERE s.import_id = %(i)s AND s.nr = d.nr AND d.k > 1""", p)
    naprawione = conn.execute(
        f"""UPDATE {STAGING} SET geom = ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3)), uwaga = 'poprawiona geometria'
             WHERE import_id = %(i)s AND akcja IS NULL AND NOT ST_IsValid(geom)""", p).rowcount
    conn.execute(f"""UPDATE {STAGING} SET akcja = 'pomin', uwaga = 'geometria pusta po poprawieniu'
                      WHERE import_id = %(i)s AND akcja IS NULL AND ST_IsEmpty(geom)""", p)
    conn.execute(f"""UPDATE {STAGING} SET skrot = sha256(
                         ST_AsBinary(ST_SnapToGrid(geom, 0.01))
                         || convert_to(concat_ws('|', wersja_id, kod, nazwa, organ, wazny_od::text), 'UTF8'))
                      WHERE import_id = %(i)s AND akcja IS NULL""", p)
    conn.commit()
    pom = conn.execute(f"""SELECT coalesce(kod, klucz, '#' || nr) AS obiekt, uwaga FROM {STAGING}
                            WHERE import_id = %(i)s AND akcja = 'pomin' ORDER BY nr LIMIT {MAX_POMINIETE}""", p).fetchall()
    out: dict = {"pominiete": [{"obiekt": o, "uwaga": u} for o, u in pom]}
    if naprawione:
        out["ostrzezenia"] = [f"Poprawiono {naprawione} niepoprawnych geometrii (ST_MakeValid)"]
    return out


# ---------------------------------------------------------------------------- podgląd i scalanie


def _closures(imp: dict) -> sql.Composable:
    """Obiekty rejestru do zamknięcia: (klucz, powód). Pełny import: brak w źródle w obrębie obszaru;
    zawsze: kod przejęty przez obiekt o innym identyfikatorze."""
    t = _tab(imp["rodzaj"])
    i = sql.Literal(imp["id"])
    obszar = list(imp["obszar"] or [])
    w_obszarze = sql.SQL("true") if not obszar else sql.SQL("({})").format(sql.SQL(" OR ").join(
        sql.SQL("r.kod LIKE {}").format(sql.Literal(o.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"))
        for o in obszar))
    brak = sql.SQL("""SELECT r.klucz, 'brak_w_zakresie' AS powod FROM {t} r
                       WHERE {wo} AND NOT EXISTS (SELECT 1 FROM prg_staging.obiekt s
                                                   WHERE s.import_id = {i} AND s.klucz = r.klucz AND s.akcja IS DISTINCT FROM 'pomin')""")
    zast = sql.SQL("""SELECT r.klucz, 'zastapiony' AS powod FROM {t} r
                       JOIN prg_staging.obiekt s ON s.import_id = {i} AND s.kod = r.kod AND s.klucz <> r.klucz
                                                AND s.akcja IS DISTINCT FROM 'pomin'""")
    if imp["zakres_typ"] == "pelny":
        return sql.SQL("SELECT DISTINCT ON (klucz) klucz, powod FROM ({b} UNION ALL {z}) x ORDER BY klucz, powod").format(
            b=brak.format(t=t, wo=w_obszarze, i=i), z=zast.format(t=t, i=i))
    return zast.format(t=t, i=i)


def preview(conn, imp_id: int, commit: bool = True) -> dict:
    """Akcje obiektów roboczych (nowy / zmieniony / bez zmian) i liczba zamknięć — bez zmian w rejestrze."""
    imp = _imp(conn, imp_id)
    t = _tab(imp["rodzaj"])
    conn.execute(sql.SQL("""UPDATE prg_staging.obiekt s SET akcja = NULL
                             WHERE s.import_id = {i} AND s.akcja IS DISTINCT FROM 'pomin'""").format(i=sql.Literal(imp_id)))
    conn.execute(sql.SQL("""UPDATE prg_staging.obiekt s SET akcja = coalesce(
                                 (SELECT CASE WHEN r.skrot = s.skrot THEN 'bez_zmian' ELSE 'zmieniony' END
                                    FROM {t} r WHERE r.klucz = s.klucz), 'nowy')
                             WHERE s.import_id = {i} AND s.akcja IS NULL""").format(t=t, i=sql.Literal(imp_id)))
    stats = dict(conn.execute(sql.SQL("SELECT akcja, count(*) FROM prg_staging.obiekt WHERE import_id = {} GROUP BY akcja").format(
        sql.Literal(imp_id))).fetchall())
    for powod, n in conn.execute(sql.SQL("SELECT powod, count(*) FROM ({}) c GROUP BY powod").format(_closures(imp))).fetchall():
        stats[powod] = n
    stats["w_rejestrze"] = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(t)).fetchone()[0]
    if commit:
        conn.commit()
    return stats


def merge(conninfo: str, imp_id: int, user_id: int | None) -> None:
    with psycopg.connect(conninfo, autocommit=False) as conn:
        try:
            stats = _merge(conn, imp_id, user_id)
            conn.commit()
            _set(conn, imp_id, "zatwierdzony", "Zatwierdzony", statystyka=stats)
        except Exception as exc:
            conn.rollback()
            log.exception("PRG import %s: błąd scalania", imp_id)
            _set(conn, imp_id, "roboczy", f"Scalanie nie powiodło się (rejestr bez zmian): {exc}")


def _merge(conn, imp_id: int, user_id: int | None) -> dict:
    imp = _imp(conn, imp_id)
    rodzaj = imp["rodzaj"]
    t, th = _tab(rodzaj), _tab(rodzaj, True)
    conn.execute(sql.SQL("LOCK TABLE {} IN EXCLUSIVE MODE").format(t))
    stats = preview(conn, imp_id, commit=False)       # stan rejestru mógł się zmienić od podglądu
    i = sql.Literal(imp_id)
    cols = ("klucz", "wersja_id", "wazna_od", "import_id", "skrot", *KOLUMNY[rodzaj], "geom")
    c = sql.SQL(", ").join(map(sql.Identifier, cols))
    rc = sql.SQL(", ").join(sql.SQL("r.{}").format(sql.Identifier(x)) for x in cols)

    def close(sel: sql.Composable) -> None:
        conn.execute(sql.SQL("""INSERT INTO {th} ({c}, wazna_do, zamkniecie, import_zamykajacy)
                                SELECT {rc}, now(), z.powod, {i} FROM {t} r JOIN ({sel}) z ON z.klucz = r.klucz""").format(
            th=th, c=c, rc=rc, i=i, t=t, sel=sel))
        conn.execute(sql.SQL("DELETE FROM {t} r USING ({sel}) z WHERE z.klucz = r.klucz").format(t=t, sel=sel))

    close(sql.SQL("SELECT * FROM ({}) c").format(_closures(imp)))
    close(sql.SQL("SELECT klucz, 'nowa_wersja' AS powod FROM prg_staging.obiekt WHERE import_id = {} AND akcja = 'zmieniony'").format(i))
    data = sql.SQL(", ").join(map(sql.Identifier, KOLUMNY[rodzaj]))
    conn.execute(sql.SQL("""INSERT INTO {t} (klucz, wersja_id, import_id, skrot, {data}, geom)
                            SELECT klucz, wersja_id, import_id, skrot, {data}, geom FROM prg_staging.obiekt
                             WHERE import_id = {i} AND akcja IN ('nowy', 'zmieniony')""").format(t=t, data=data, i=i))
    conn.execute("DELETE FROM prg_staging.obiekt WHERE import_id = %s", (imp_id,))
    conn.execute("UPDATE prg.import SET zatwierdzil = %s, zatwierdzono = now() WHERE id = %s", (user_id, imp_id))
    stats["w_rejestrze"] = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(t)).fetchone()[0]
    return stats
