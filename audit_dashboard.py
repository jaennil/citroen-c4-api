"""
Проверка дашборда на типовые изъяны. Запускать после каждой перегенерации.

Ловит то, на что уже наступали:
  * несопоставимые масштабы в одной панели - обороты до 4700 рядом со скоростью
    до 45 делают второй ряд плоской линией у нуля. Допустимо только если панели
    задана вторая ось;
  * разные единицы измерения в одной панели;
  * панели без тултипа (у stat его в Grafana нет вообще, поэтому stat запрещён);
  * серии, по которым в базе нет ни одного значения - панель покажет "No data".

Данные о диапазонах берутся из локального буфера car.db.

    ./.venv/bin/python audit_dashboard.py
"""

import json
import re
import sqlite3
import subprocess
import sys

RATIO_LIMIT = 20        # во столько раз ряды могут отличаться без второй оси
HERE = "/".join(__file__.split("/")[:-1]) or "."


def load_dashboard():
    out = subprocess.run([f"{HERE}/.venv/bin/python", f"{HERE}/make_dashboard.py"],
                         capture_output=True, text=True)
    return json.loads(out.stdout)


def load_ranges(db_path=f"{HERE}/car.db"):
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT p.name, p.unit, min(v.value), max(v.value) FROM param p "
            "LEFT JOIN reading v ON v.param_id = p.id GROUP BY p.id").fetchall()
        db.close()
    except sqlite3.Error as e:
        print(f"нет доступа к {db_path}: {e}")
        return {}
    return {n: (u, lo, hi) for n, u, lo, hi in rows}


def walk(dash):
    for p in dash["panels"]:
        yield p
        for q in p.get("panels", []):
            yield q


def main():
    dash = load_dashboard()
    rng = load_ranges()
    problems = []
    n_panels = n_multi = 0

    for p in walk(dash):
        kind = p.get("type")
        if kind == "row":
            continue
        n_panels += 1
        title = p.get("title", "?")

        if kind == "stat":
            problems.append(("stat без тултипа", title, "заменить на timeseries"))
            continue
        if kind == "timeseries" and "tooltip" not in p.get("options", {}):
            problems.append(("нет тултипа", title, ""))

        sql = (p.get("targets") or [{}])[0].get("rawSql", "")
        names = re.findall(r"'([A-Za-z_0-9]+)'", sql)
        known = [n for n in names if n in rng]

        empty = [n for n in known if rng[n][2] is None]
        if empty and len(empty) == len(known) and known:
            problems.append(("нет данных ни по одной серии", title, ", ".join(empty[:3])))

        # rng[name] = (unit, min, max)
        vals = [abs(rng[n][2]) for n in known if rng[n][2]]
        units = {rng[n][0] for n in known}
        # таблице масштаб и единицы безразличны - там столбцы, а не общая ось
        if kind != "timeseries" or len(known) < 2:
            continue
        n_multi += 1

        # вторая ось - законный способ совместить разные величины
        has_axis = any("axisPlacement" in json.dumps(o)
                       for o in p.get("fieldConfig", {}).get("overrides", []))
        if len(units) > 1 and not has_axis:
            problems.append(("разные единицы в одной панели", title, " / ".join(sorted(units))))

        if len(vals) >= 2 and min(vals) > 0:
            ratio = max(vals) / min(vals)
            if ratio > RATIO_LIMIT and not has_axis:
                problems.append((f"масштабы различаются в {ratio:.0f} раз", title,
                                 "нужна вторая ось или разделить панели"))

    print(f"панелей проверено: {n_panels}, из них многосерийных: {n_multi}")
    if not problems:
        print("изъянов не найдено")
        return 0
    print(f"\nнайдено проблем: {len(problems)}")
    for kind, title, detail in problems:
        print(f"  [{kind}] {title[:56]}")
        if detail:
            print(f"      {detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
