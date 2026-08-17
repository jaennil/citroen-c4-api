"""
Генератор дашборда Grafana по каталогу параметров машины.

316 отдельных панелей никто не будет сопровождать и открываться это будет вечно,
поэтому структура другая:
  - обзор: несколько крупных показателей, которые смотришь чаще всего;
  - обозреватель: одна панель с выпадающим списком, через который доступны ВСЕ
    параметры разом - список берётся из базы, поэтому новые появляются сами;
  - тематические ряды: температуры, напряжения, проценты, счётчики, состояния.

Категории выводятся из единиц измерения и мнемоник каталога, так что при
пополнении каталога дашборд достаточно перегенерировать.

Запуск:
    ./.venv/bin/python make_dashboard.py > dashboard.json
    ./.venv/bin/python make_dashboard.py --configmap > citroen-dashboard.yaml
"""

import argparse
import json
import sys

from did_catalog import BY_DID, CATALOG
from ru_labels import label as ru_label

try:
    from live_dids import LIVE
except ImportError:
    LIVE = []

DS = {"type": "postgres", "uid": "citroen-postgres"}

# coalesce: если ярлык почему-то не заполнен, показываем мнемонику
SERIES_SQL = (
    'SELECT r.ts AS "time", coalesce(p.label, p.name) AS metric, r.value\n'
    "FROM reading r JOIN param p ON p.id = r.param_id\n"
    "WHERE p.name IN ({names}) AND $__timeFilter(r.ts)\n"
    "ORDER BY 1"
)


def sql_in(names):
    return ", ".join("'" + n.replace("'", "''") + "'" for n in names)


def target(names, ref="A"):
    return {
        "refId": ref,
        "datasource": DS,
        "format": "time_series",
        "rawQuery": True,
        "rawSql": SERIES_SQL.format(names=sql_in(names)),
    }


def panel(pid, title, gx, gy, gw, gh, targets, unit="", kind="timeseries", extra=None):
    p = {
        "id": pid,
        "type": kind,
        "title": title,
        "datasource": DS,
        "gridPos": {"h": gh, "w": gw, "x": gx, "y": gy},
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {"lineWidth": 1, "fillOpacity": 8, "showPoints": "never"},
            },
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "table", "placement": "bottom",
                               "calcs": ["last", "min", "max", "mean"]},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
    }
    if extra:
        p.update(extra)
    return p


def mini(pid, title, name, gx, gy, unit="", gw=6, gh=6):
    """Компактный график для обзора.

    Раньше здесь была панель stat с большой цифрой, но у неё в Grafana НЕТ
    тултипа - спарклайн декоративный и на наведение не реагирует. Поэтому обзор
    собран из обычных timeseries: текущее значение видно в легенде (Last),
    а по наведению доступно значение в любой момент времени.
    """
    p = panel(pid, title, gx, gy, gw, gh, [target([name], "A")], unit)
    p["options"] = {
        "legend": {"showLegend": True, "displayMode": "list",
                   "placement": "bottom", "calcs": ["lastNotNull"]},
        "tooltip": {"mode": "single", "sort": "none"},
    }
    p["fieldConfig"]["defaults"]["custom"] = {
        "lineWidth": 2, "fillOpacity": 15, "showPoints": "never",
        "spanNulls": True,
    }
    return p


LATEST_SQL = (
    'SELECT coalesce(p.label, p.name) AS "Параметр", p.unit AS "Ед.",\n'
    '       l.value AS "Значение", l.ts AS "Обновлено"\n'
    "FROM param p\n"
    "JOIN LATERAL (SELECT value, ts FROM reading WHERE param_id = p.id\n"
    "              ORDER BY ts DESC LIMIT 1) l ON true\n"
    "WHERE p.name IN ({names})\n"
    "ORDER BY 1"
)


def latest_table(pid, title, names, gh=16):
    """Таблица последних значений: для дискретных состояний это полезнее графика."""
    return {
        "id": pid,
        "type": "table",
        "title": title,
        "datasource": DS,
        "gridPos": {"h": gh, "w": 24, "x": 0, "y": 0},
        "targets": [{"refId": "A", "datasource": DS, "format": "table",
                     "rawQuery": True,
                     "rawSql": LATEST_SQL.format(names=sql_in(names))}],
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": []},
        "options": {"showHeader": True, "footer": {"show": False},
                    "sortBy": [{"displayName": "Параметр", "desc": False}]},
    }


def row(pid, title, gy, collapsed=True, panels=None):
    return {"id": pid, "type": "row", "title": title, "collapsed": collapsed,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": gy}, "panels": panels or []}


# единицы каталога -> единицы Grafana
UNIT_MAP = {"°C": "celsius", "V": "volt", "%": "percent", "km": "lengthkm",
            "km/h": "velocitykmh", "Rpm": "rotrpm", "ms": "ms", "L": "litre",
            "month(s)": "", "A": "amp", "Nm": "", "s": "s"}


def categorise():
    """Разложить живые параметры по смысловым группам."""
    names = {}
    for did, name in LIVE:
        e = BY_DID.get(did)
        names[name] = (e[0]["unit"] if e else "", did)
    groups = {"Температуры": [], "Напряжения и ток": [], "Проценты": [],
              "Пробег и обслуживание": [], "Состояния и флаги": [],
              "Конфигурация": []}
    for name, (unit, did) in sorted(names.items(), key=lambda kv: ru_label(kv[1][1], kv[0])):
        item = (name, unit, did)
        if name.startswith("CFG_"):
            groups["Конфигурация"].append(item)
        elif unit == "°C" or "TEMPERATURE" in name:
            groups["Температуры"].append(item)
        elif unit in ("V", "A") or "TENSION" in name or "COURANT" in name:
            groups["Напряжения и ток"].append(item)
        elif unit == "%":
            groups["Проценты"].append(item)
        elif unit in ("km", "month(s)") or "KILOMETR" in name or "MAINT" in name:
            groups["Пробег и обслуживание"].append(item)
        else:
            groups["Состояния и флаги"].append(item)
    return groups


def build():
    pid = 1
    panels = []
    y = 0

    # --- обзор ---
    panels.append(row(pid, "Обзор", y, collapsed=False)); pid += 1; y += 1
    # Коды enum (положение ключа, состояние ГМП) из обзора убраны: без таблицы
    # расшифровок это просто "2.0" и смысла не несёт. Они есть в таблице состояний.
    # Убраны параметры, которые на этой машине не отдаются вообще: напряжение АКБ
    # (D8/DA46), заряд АКБ (DA21) и мгновенный расход (D8C7) всегда возвращают
    # маркер "нет данных", поэтому в обзоре давали "No data".
    # Пробег - через suffix, иначе Grafana масштабирует 195446 км в "195.4 Mm".
    # Скорость рыскания тоже убрана: BSI отдаёт по ней только знаковый маркер
    # 0x7FFF (3276.7 °/s), реальных данных на этой машине нет.
    OVERVIEW = [
        ("Обороты", "MP_REGIME_MOTEUR_AFFICHE", "rotrpm"),
        ("Скорость", "MP_VITESSE_VEHICULE_a", "velocitykmh"),
        ("Температура масла", "MP_TEMPERATURE_HUILE_MOTEUR_CALCULEE", "celsius"),
        ("Питание BSI", "MP_TENSION_ALIMENTION_BSI", "volt"),
        ("Пробег общий", "MP_KILOMETRAGE_TOTAL", "suffix: км"),
        ("Топливо в баке", "MP_NIVEAU_CARBURANT_AFFICHE", "litre"),
        ("Запас хода", "MP_AUTONOMIE_CARBURANT_CALCULE", "suffix: км"),
        ("Температура за бортом", "MP_TEMPERATURE_EXTERIEURE", "celsius"),
        ("Напряжение АКБ в покое", "MP_TENSION_BATTERIE_AU_REPOS", "volt"),
        ("Уровень масла", "MP_NIVEAU_HUILE_MOTEUR_MOYENNE", "percent"),
        ("Пробег поездки 1", "MP_KILOMETRAGE_TRAJET1", "suffix: км"),
        ("Расход текущий", "MP_CONSOMMATION_CARBURANT_RAPIDE", "suffix: л/100км"),
    ]
    known = {e["name"] for e in CATALOG}
    col = 0
    for title, name, unit in OVERVIEW:
        if name not in known:
            continue          # параметра нет на этой машине - панель не рисуем
        panels.append(mini(pid, title, name, (col % 4) * 6, y + (col // 4) * 6, unit))
        pid += 1
        col += 1
    y += 6 * ((col + 3) // 4)
    # Две оси обязательны: обороты доходят до 4700, скорость до 45, и на общей
    # шкале скорость превращается в плоскую линию у нуля.
    combo = panel(pid, "Обороты и скорость", 0, y, 24, 8,
                  [target(["MP_REGIME_MOTEUR_AFFICHE", "MP_VITESSE_VEHICULE_a"])],
                  "rotrpm")
    combo["fieldConfig"]["defaults"]["custom"]["axisLabel"] = "об/мин"
    combo["fieldConfig"]["overrides"] = [{
        "matcher": {"id": "byName", "options": "Скорость автомобиля"},
        "properties": [
            {"id": "unit", "value": "velocitykmh"},
            {"id": "custom.axisPlacement", "value": "right"},
            {"id": "custom.axisLabel", "value": "км/ч"},
            {"id": "custom.lineWidth", "value": 2},
            {"id": "color", "value": {"mode": "fixed", "fixedColor": "yellow"}},
        ],
    }]
    panels.append(combo)
    pid += 1; y += 8

    # --- обозреватель: через него доступны ВСЕ параметры ---
    panels.append(row(pid, "Обозреватель - любой из параметров", y, collapsed=False))
    pid += 1; y += 1
    explorer = panel(pid, "Выбранные параметры", 0, y, 24, 10, [{
        "refId": "A", "datasource": DS, "format": "time_series", "rawQuery": True,
        "rawSql": 'SELECT r.ts AS "time", coalesce(p.label, p.name) AS metric, r.value\n'
                  "FROM reading r JOIN param p ON p.id = r.param_id\n"
                  "WHERE p.name IN (${param:sqlstring}) AND $__timeFilter(r.ts)\n"
                  "ORDER BY 1",
    }])
    panels.append(explorer); pid += 1; y += 10

    # --- тематические ряды ---
    # Непрерывные величины показываем графиками, а дискретные состояния и
    # конфигурацию - одной таблицей текущих значений: 256 булевых флагов в виде
    # временных рядов нечитаемы и раздувают дашборд в разы.
    TABLE_GROUPS = {"Состояния и флаги", "Конфигурация"}
    for title, items in categorise().items():
        if not items:
            continue
        if title in TABLE_GROUPS:
            inner = [latest_table(pid, f"{title}: текущие значения",
                                  [n for n, _, _ in items])]
            pid += 1
        else:
            inner = []
            iy = 0
            # Сначала разбиваем по единицам измерения: иначе в одну панель попадают
            # пробег в 195000 км и "дней до ТО", и второе не видно вообще.
            by_unit = {}
            for it in items:
                by_unit.setdefault(it[1], []).append(it)
            # по 4 параметра на панель, чтобы легенда оставалась читаемой
            chunks = []
            for u in sorted(by_unit):
                grp = by_unit[u]
                chunks += [grp[j:j + 4] for j in range(0, len(grp), 4)]
            for i, chunk in enumerate(chunks):
                i = i * 4
                unit = UNIT_MAP.get(chunk[0][1], "")
                # заголовок панели - из ручных имён; DID обязателен, иначе
                # ru_label не найдёт запись и свалится в грубый автоперевод
                inner.append(panel(pid, " · ".join(ru_label(d, n)[:26] for n, _, d in chunk),
                                   (i // 4 % 2) * 12, iy, 12, 7,
                                   [target([n for n, _, _ in chunk])], unit))
                pid += 1
                if i // 4 % 2:
                    iy += 7
        panels.append(row(pid, f"{title} ({len(items)})", y, collapsed=True, panels=inner))
        pid += 1
        y += 1

    return {
        "uid": "citroen-c4",
        "title": "Citroen C4",
        "tags": ["citroen", "car", "telemetry"],
        "timezone": "browser",
        "schemaVersion": 39,
        "refresh": "30s",
        # 1 - общий курсор на всех панелях, чтобы читать значения в один момент времени
        "graphTooltip": 1,
        "time": {"from": "now-24h", "to": "now"},
        "templating": {"list": [{
            "name": "param",
            "label": "Параметр",
            "type": "query",
            "datasource": DS,
            # __text - что видно в списке, __value - что уходит в запрос
            "query": "SELECT coalesce(label, name) AS \"__text\", name AS \"__value\" "
                     "FROM param ORDER BY 1",
            "multi": True,
            "includeAll": False,
            "refresh": 1,
            "current": {"text": ["Обороты двигателя"],
                        "value": ["MP_REGIME_MOTEUR_AFFICHE"]},
        }]},
        "panels": panels,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configmap", action="store_true", help="обернуть в ConfigMap для кластера")
    args = ap.parse_args()
    dash = build()
    js = json.dumps(dash, ensure_ascii=False, indent=2)
    if not args.configmap:
        print(js)
        return 0
    body = "\n".join("    " + line for line in js.splitlines())
    print("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: grafana-dashboard-citroen\n"
          "  namespace: monitoring\n  labels:\n    grafana_dashboard: \"1\"\n"
          "data:\n  citroen.json: |\n" + body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
