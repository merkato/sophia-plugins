"""Uzupełnienia użytków: przyjmowanie partii obrysów (kandydatów) z regułą „w jednym miejscu jeden użytek”.

Kandydaci, którzy na nic nie nachodzą, są przyjmowani od razu. Nakładające się (część wspólna szersza niż
2 cm — egib.nakladka) tworzą grupę do decyzji: scal (suma obrysów) albo zostaw jeden (domyślnie największy).
Kandydat nachodzący na istniejący użytek (EGiB albo uzupełnienie) też czeka — z uwagą, na co nachodzi.
Kod synchroniczny (psycopg), wywoływany w wątku albo z narzędzi migracji.
"""
from __future__ import annotations

from psycopg.types.json import Jsonb


def add_candidates(conn, partia: str, rows: list[dict]) -> int:
    """rows: {"ewkt" | "wkb_hex", "ofu", "zrodla": [...], "pow_ewid_ha"} → egib.uzytek_kandydat."""
    with conn.cursor() as c:
        for r in rows:
            c.execute(
                """INSERT INTO egib.uzytek_kandydat (partia, ofu, geom, zrodla, pow_ewid_ha)
                   VALUES (%s, %s, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromEWKT(%s), 2180)), 3)), %s, %s)""",
                (partia, r.get("ofu") or "Ls", r["ewkt"], Jsonb(r.get("zrodla") or []), r.get("pow_ewid_ha")))
    return len(rows)


def _accept(conn, cand_ids: list[int], geom_sql: str, params: tuple, pochodzenie: str, login: str | None,
            status: str) -> int:
    """Nowe uzupełnienie z podanej geometrii; kandydaci dostają status i odnośnik."""
    row = conn.execute(
        f"""INSERT INTO egib.uzytek_uzupelnienie (ofu, geom, pochodzenie, zrodla, pow_ewid_ha, utworzyl_login)
            SELECT (array_agg(ofu ORDER BY ST_Area(geom) DESC))[1], {geom_sql}, %s,
                   coalesce((SELECT jsonb_agg(z) FROM egib.uzytek_kandydat k2, jsonb_array_elements(k2.zrodla) z
                              WHERE k2.id = ANY(%s)), '[]'),
                   (array_agg(pow_ewid_ha ORDER BY ST_Area(geom) DESC))[1], %s
              FROM egib.uzytek_kandydat WHERE id = ANY(%s)
            RETURNING id""", (*params, pochodzenie, cand_ids, login, cand_ids)).fetchone()
    conn.execute("""UPDATE egib.uzytek_kandydat SET status = %s, uzupelnienie_id = %s, rozstrzygnal = %s,
                           rozstrzygnieto = now() WHERE id = ANY(%s)""", (status, row[0], login, cand_ids))
    return row[0]


def process(conn, partia: str, pochodzenie: str = "migracja", login: str | None = None) -> dict:
    """Grupuje oczekujących kandydatów partii i przyjmuje tych bez konfliktów. Zwraca statystykę."""
    pending = [r[0] for r in conn.execute(
        "SELECT id FROM egib.uzytek_kandydat WHERE partia = %s AND status = 'oczekuje' ORDER BY id", (partia,))]
    pairs = conn.execute(
        """SELECT a.id, b.id FROM egib.uzytek_kandydat a JOIN egib.uzytek_kandydat b
             ON a.id < b.id AND egib.nakladka(a.geom, b.geom)
            WHERE a.partia = %s AND b.partia = %s AND a.status = 'oczekuje' AND b.status = 'oczekuje'""",
        (partia, partia)).fetchall()
    parent = {i: i for i in pending}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, list[int]] = {}
    for i in pending:
        groups.setdefault(find(i), []).append(i)

    # konflikty z użytkami już w rejestrze (EGiB albo uzupełnienia)
    clash = dict(conn.execute(
        """SELECT k.id, coalesce(
                    (SELECT 'uzupełnienie #' || s.id FROM egib.uzytek_uzupelnienie s
                      WHERE s.zastapiony_importem IS NULL AND egib.nakladka(s.geom, k.geom) LIMIT 1),
                    (SELECT 'użytek EGiB ' || u.id_uzytku FROM egib.uzytek u WHERE egib.nakladka(u.geom, k.geom) LIMIT 1))
             FROM egib.uzytek_kandydat k WHERE k.partia = %s AND k.status = 'oczekuje'""", (partia,)).fetchall())
    stats = {"kandydaci": len(pending), "przyjete": 0, "grupy_do_decyzji": 0, "w_grupach": 0, "konflikt_z_rejestrem": 0}
    for root, ids in groups.items():
        conflict = [i for i in ids if clash.get(i)]
        if len(ids) == 1 and not conflict:
            _accept(conn, ids, "ST_Union(geom)", (), pochodzenie, login, "przyjety")
            stats["przyjete"] += 1
            continue
        conn.execute("UPDATE egib.uzytek_kandydat SET grupa = %s WHERE id = ANY(%s)", (root, ids))
        for i in conflict:
            conn.execute("UPDATE egib.uzytek_kandydat SET uwaga = %s WHERE id = %s", (f"nachodzi na {clash[i]}", i))
        stats["grupy_do_decyzji"] += 1
        stats["w_grupach"] += len(ids)
        stats["konflikt_z_rejestrem"] += len(conflict)
    return stats


def groups(conn, partia: str | None = None, limit: int = 500) -> list[dict]:
    """Grupy czekające na decyzję: kandydaci (powierzchnia, źródła, uwaga), czy obrysy są identyczne (±2 cm)."""
    rows = conn.execute(
        """SELECT k.grupa, k.partia, json_agg(json_build_object(
                    'id', k.id, 'pow_m2', round(ST_Area(k.geom)::numeric, 1), 'zrodla', k.zrodla,
                    'pow_ewid_ha', k.pow_ewid_ha, 'ofu', k.ofu, 'uwaga', k.uwaga) ORDER BY ST_Area(k.geom) DESC) AS kandydaci,
                  bool_or(k.uwaga IS NOT NULL) AS konflikt_z_rejestrem,
                  ST_AsGeoJSON(ST_Transform(ST_Collect(k.geom), 4326), 7)::json AS geometry,
                  ST_IsEmpty(ST_Buffer(ST_SymDifference(ST_Union(k.geom), ST_Intersection(
                      (array_agg(k.geom ORDER BY ST_Area(k.geom) DESC))[1],
                      (array_agg(k.geom ORDER BY ST_Area(k.geom)))[1])), -0.01)) AS identyczne
             FROM egib.uzytek_kandydat k
            WHERE k.status = 'oczekuje' AND k.grupa IS NOT NULL AND (%s::text IS NULL OR k.partia = %s)
            GROUP BY k.grupa, k.partia ORDER BY k.grupa LIMIT %s""", (partia, partia, limit)).fetchall()
    cols = ["grupa", "partia", "kandydaci", "konflikt_z_rejestrem", "geometry", "identyczne"]
    return [dict(zip(cols, r, strict=True)) for r in rows]


def _pending_in_group(conn, grupa: int) -> list[int]:
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM egib.uzytek_kandydat WHERE grupa = %s AND status = 'oczekuje' ORDER BY ST_Area(geom) DESC", (grupa,))]
    if not ids:
        raise ValueError(f"Grupa {grupa} nie czeka na decyzję")
    return ids


def merge_group(conn, grupa: int, login: str | None, pochodzenie: str = "migracja") -> int:
    """Scal: jeden użytek z sumy obrysów grupy (źródła ze wszystkich)."""
    ids = _pending_in_group(conn, grupa)
    return _accept(conn, ids, "ST_Multi(ST_CollectionExtract(ST_UnaryUnion(ST_Collect(geom)), 3))", (), pochodzenie, login, "scalony")


def keep_one(conn, grupa: int, cand_id: int | None, login: str | None, pochodzenie: str = "migracja") -> int:
    """Zostaw jeden obrys (domyślnie największy); pozostałe odrzucone."""
    ids = _pending_in_group(conn, grupa)
    keep = cand_id if cand_id is not None else ids[0]
    if keep not in ids:
        raise ValueError("Kandydat nie należy do tej grupy")
    new_id = _accept(conn, [keep], "ST_Union(geom)", (), pochodzenie, login, "przyjety")
    conn.execute("""UPDATE egib.uzytek_kandydat SET status = 'odrzucony', rozstrzygnal = %s, rozstrzygnieto = now(),
                           uwaga = coalesce(uwaga || '; ', '') || %s WHERE id = ANY(%s)""",
                 (login, f"zostawiono #{keep}", [i for i in ids if i != keep]))
    return new_id


def merge_identical(conn, login: str | None, partia: str | None = None) -> dict:
    """Scal wszystkie grupy, w których obrysy są identyczne (±2 cm) i nie nachodzą na rejestr."""
    done, failed = 0, []
    for g in groups(conn, partia, limit=100000):
        if not g["identyczne"] or g["konflikt_z_rejestrem"]:
            continue
        conn.execute("SAVEPOINT g")
        try:
            merge_group(conn, g["grupa"], login)
            conn.execute("RELEASE SAVEPOINT g")
            done += 1
        except Exception as exc:   # np. suma nachodzi na coś przyjętego w międzyczasie
            conn.execute("ROLLBACK TO SAVEPOINT g")
            failed.append(f"grupa {g['grupa']}: {exc}")
    return {"scalone": done, "bledy": failed}


def summary(conn) -> dict:
    k = dict(conn.execute("SELECT status, count(*) FROM egib.uzytek_kandydat GROUP BY 1").fetchall())
    s = dict(conn.execute("""SELECT pochodzenie, count(*) FROM egib.uzytek_uzupelnienie
                              WHERE zastapiony_importem IS NULL GROUP BY 1""").fetchall())
    g = conn.execute("""SELECT count(DISTINCT grupa) FROM egib.uzytek_kandydat
                         WHERE status = 'oczekuje' AND grupa IS NOT NULL""").fetchone()[0]
    return {"kandydaci": k, "uzupelnienia": s, "grupy_do_decyzji": g}
