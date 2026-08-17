"""
Удаление недостоверных значений, записанных до появления проверки маркеров.

PSA помечает отсутствие данных максимальным сырым кодом (0xFF, 0xFFFF) или на
единицу меньше (0xFE, 0xFFFE). Раньше они писались как обычные числа, из-за чего
на графиках появлялись напряжение АКБ 65.5 В, заряд 254% и уровень масла 255%.
Теперь такие значения не записываются, а этот скрипт убирает уже накопленные.

Удаляются только строки, точно совпадающие с расшифровкой маркера для своего
параметра, плюс выходящие за физические границы (проценты вне 0..100 и т.п.).
Ничего другого не трогается.

    ./.venv/bin/python cleanup.py                      # локальный SQLite
    ./.venv/bin/python cleanup.py --pg                 # ещё и Postgres (CAR_PG)
    ./.venv/bin/python cleanup.py --dry-run            # только показать
"""

import argparse
import os
import sqlite3
import sys

from did_catalog import BY_DID
from telemetry import PLAUSIBLE


def bad_values(did: int):
    """Значения, которые для этого параметра означают "нет данных"."""
    entries = BY_DID.get(did)
    if not entries:
        return set(), (None, None)
    e = entries[0]
    if e["mask"] is not None or not e["unit"]:
        return set(), (None, None)          # флаги и безразмерные не трогаем
    ln = e["ln"] or 1
    full = (1 << (8 * ln)) - 1
    factor = e["factor"] or 1.0
    offset = e.get("offset", 0.0)
    vals = {round(full * factor + offset, 3), round((full - 1) * factor + offset, 3)}
    return vals, PLAUSIBLE.get(e["unit"], (None, None))


def sweep(rows):
    """rows: [(param_id, did, value)] -> param_id/value, подлежащие удалению."""
    doomed = []
    for pid, did, value in rows:
        vals, (lo, hi) = bad_values(did)
        if value in vals or (lo is not None and not (lo <= value <= hi)):
            doomed.append((pid, value))
    return doomed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default="car.db")
    ap.add_argument("--pg", action="store_true", help="почистить и Postgres (переменная CAR_PG)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    total = 0
    if os.path.exists(args.sqlite):
        db = sqlite3.connect(args.sqlite)
        rows = db.execute(
            "SELECT DISTINCT r.param_id, p.did, r.value "
            "FROM reading r JOIN param p ON p.id = r.param_id").fetchall()
        doomed = sweep(rows)
        n = 0
        for pid, value in doomed:
            cur = db.execute("SELECT count(*) FROM reading WHERE param_id=? AND value=?",
                             (pid, value))
            n += cur.fetchone()[0]
        print(f"SQLite {args.sqlite}: под удаление {n} значений "
              f"({len(doomed)} пар параметр/значение)")
        if not args.dry_run and doomed:
            db.executemany("DELETE FROM reading WHERE param_id=? AND value=?", doomed)
            db.commit()
            db.execute("VACUUM")
            print("  удалено")
        total += n
        db.close()

    if args.pg:
        import psycopg
        dsn = os.environ.get("CAR_PG")
        if not dsn:
            print("нет переменной CAR_PG - Postgres пропущен")
            return 0
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT r.param_id, p.did, r.value "
                        "FROM reading r JOIN param p ON p.id = r.param_id")
            doomed = sweep(cur.fetchall())
            if doomed:
                cur.execute("SELECT count(*) FROM reading r WHERE (r.param_id, r.value) IN "
                            "(SELECT * FROM unnest(%s::int[], %s::float8[]))",
                            ([p for p, _ in doomed], [v for _, v in doomed]))
                n = cur.fetchone()[0]
                print(f"Postgres: под удаление {n} значений")
                if not args.dry_run:
                    cur.execute("DELETE FROM reading r WHERE (r.param_id, r.value) IN "
                                "(SELECT * FROM unnest(%s::int[], %s::float8[]))",
                                ([p for p, _ in doomed], [v for _, v in doomed]))
                    conn.commit()
                    print("  удалено")
                total += n
            else:
                print("Postgres: чисто")
    print(f"итого недостоверных значений: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
