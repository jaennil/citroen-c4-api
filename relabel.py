"""
Пересчёт человекочитаемых имён параметров в базах.

Зачем отдельный скрипт. Имя пишется в поле param.label один раз, когда параметр
встречается впервые, и дальше не пересматривается. Пока читалась одна BSI, это
было безобидно: имена брались из ручной таблицы NAMES по DID. Как только
добавились двигатель, ABS и насос, у их параметров DID нет, автоперевод не знал
французских слов, и в базу легли имена вида "Температура d eau двигатель d" и
"V46 32:mp facteur correction richesse amont". Именно они попадают в легенду
графиков Grafana - там запрос берёт coalesce(label, name), а не имя панели.

Скрипт пересчитывает label по нынешним таблицам ru_labels и обновляет обе базы:
локальный SQLite и архив в Postgres. Коды неисправностей пропускаются: у них в
label лежит описание из образа DiagBox, а не перевод мнемоники, и пересчёт его
затёр бы.

    ./.venv/bin/python relabel.py                    # показать, что изменится
    ./.venv/bin/python relabel.py --apply            # записать в car.db
    CAR_PG=... ./.venv/bin/python relabel.py --apply # и в архив кластера
"""

import argparse
import os
import sqlite3
import sys

from ru_labels import label as ru_label


def wanted(did, name):
    """Каким имя должно быть сейчас. None - трогать не надо."""
    if name.startswith("DTC:"):
        return None          # там описание кода, а не перевод мнемоники
    return ru_label(did, name)


def plan(rows):
    """rows: [(id, did, name, label)] -> [(id, name, было, стало)]"""
    out = []
    for pid, did, name, lab in rows:
        new = wanted(did, name)
        if new and new != (lab or ""):
            out.append((pid, name, lab or "", new))
    return out


def do_sqlite(path, apply):
    db = sqlite3.connect(path)
    rows = db.execute("SELECT id, did, name, label FROM param").fetchall()
    ch = plan(rows)
    if apply and ch:
        db.executemany("UPDATE param SET label=? WHERE id=?",
                       [(new, pid) for pid, _, _, new in ch])
        db.commit()
    db.close()
    return len(rows), ch


def do_postgres(dsn, apply):
    import psycopg
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        cur.execute("SELECT id, did, name, label FROM param")
        rows = cur.fetchall()
        ch = plan(rows)
        if apply and ch:
            # по id, а не по did: did в архиве свой и с локальным не обязан совпадать
            cur.executemany("UPDATE param SET label=%s WHERE id=%s",
                            [(new, pid) for pid, _, _, new in ch])
            con.commit()
    return len(rows), ch


def report(where, total, ch, apply):
    print(f"\n{where}: параметров {total}, имя меняется у {len(ch)}")
    for _, name, old, new in ch[:200]:
        print(f"  {name[:52]:<54} {old[:34]:<36} -> {new}")
    if len(ch) > 200:
        print(f"  ... ещё {len(ch) - 200}")
    if ch and not apply:
        print("  (ничего не записано, добавь --apply)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default="car.db")
    ap.add_argument("--apply", action="store_true", help="записать изменения")
    args = ap.parse_args()

    if os.path.exists(args.sqlite):
        total, ch = do_sqlite(args.sqlite, args.apply)
        report(args.sqlite, total, ch, args.apply)
    else:
        print(f"{args.sqlite}: нет файла, пропускаю")

    dsn = os.environ.get("CAR_PG")
    if not dsn:
        print("\nCAR_PG не задан - архив в кластере не тронут. "
              "Именно он питает Grafana, так что запустить это дома обязательно.")
        return 0
    try:
        total, ch = do_postgres(dsn, args.apply)
        report("архив в кластере", total, ch, args.apply)
    except Exception as e:
        print(f"\nархив в кластере: не вышло ({type(e).__name__}: {e})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
