"""
Проверка дашборда на типовые изъяны. Запускать после каждой перегенерации.

Ловит то, на что уже наступали:
  * несопоставимые масштабы в одной панели - обороты до 4700 рядом со скоростью
    до 45 делают второй ряд плоской линией у нуля. Допустимо только если панели
    задана вторая ось;
  * разные единицы измерения в одной панели;
  * панели без тултипа (у stat его в Grafana нет вообще, поэтому stat запрещён);
  * серии, по которым в базе нет ни одного значения - панель покажет "No data";
  * константы на графиках - значение не менялось ни разу, график вырождается в
    прямую линию и зря занимает панель, место такому в таблице;
  * данные, которых нет ни на одной панели - параметр собирается, а посмотреть
    его негде. Так пропадали 17 параметров, когда состав панелей решался по
    локальному буферу, а Grafana читала архив кластера;
  * один параметр на многих панелях - не всегда изъян (обзор дублирует нарочно),
    но четыре вхождения подряд стоит заметить.

Диапазоны берутся из локального car.db, а состав данных - из cluster_stats.psv,
снятого ./fetch-stats.sh: источник для Grafana - архив кластера, и судить о
наличии данных надо по нему.

    ./.venv/bin/python audit_dashboard.py
"""

import collections
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
        names = re.findall(r"'([A-Za-z0-9_:]+)'", sql)
        known = [n for n in names if n in rng]

        # Производная панель: рисует ОДНУ вычисленную серию (например отношение
        # оборотов к скорости), а имена параметров в запросе - только слагаемые
        # формулы. Сравнивать их диапазоны бессмысленно, это давало ложную
        # жалобу на "разные единицы" и "разные масштабы".
        derived = ("coalesce(p.label" not in sql
                   and re.search(r"'[^']+' AS metric", sql) is not None)
        if derived:
            continue

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
    # --- проверки по составу данных в архиве кластера ---
    import make_dashboard as md
    seen = collections.Counter()
    graph_of = {}
    for p in walk(dash):
        for t in p.get("targets", []):
            for n in re.findall(r"'([A-Za-z0-9_:]+)'", t.get("rawSql", "")):
                if n not in md.STATS:
                    continue
                seen[n] += 1
                if p.get("type") == "timeseries":
                    graph_of.setdefault(n, p.get("title", ""))

    # Обзорные панели отобраны руками: их постоянство временное (счётчик поездки
    # стоит на 9999, уровень масла не менялся за двое суток), и выносить их в
    # таблицу нельзя - это осознанное решение вместе с порогами.
    curated = {name for _, name, _ in md.OVERVIEW}
    for n, title in sorted(graph_of.items()):
        if n not in curated and md.is_constant(n):
            problems.append(("константа на графике", title[:44], n))
    for n, (cnt, _, _) in sorted(md.STATS.items()):
        if cnt > 0 and n not in seen:
            problems.append(("есть данные, но нет панели", n, f"{cnt} значений"))
    for n, c in sorted(seen.items()):
        # Обороты и скорость намеренно повторяются: обзор, совмещённый график с
        # двумя осями и панель отношения для расчёта передачи.
        if c > 3 and n not in curated:
            problems.append(("параметр на многих панелях", n, f"{c} панелей"))

    if not problems:
        print(f"изъянов не найдено (состав данных - {md.SOURCE}, "
              f"{len(md.STATS)} параметров)")
        return 0
    print(f"\nнайдено проблем: {len(problems)}")
    for kind, title, detail in problems:
        print(f"  [{kind}] {title[:56]}")
        if detail:
            print(f"      {detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
