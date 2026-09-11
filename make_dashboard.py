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
import math
import os
import sqlite3
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
    'SELECT r.ts AS "time", {metric} AS metric, r.value\n'
    "FROM reading r JOIN param p ON p.id = r.param_id\n"
    "WHERE p.name IN ({names}) AND $__timeFilter(r.ts)\n"
    "ORDER BY 1"
)


def sql_in(names):
    return ", ".join("'" + n.replace("'", "''") + "'" for n in names)


# Имя -> DID для параметров BSI: ручная таблица имён ru_labels.NAMES ключуется
# по DID, а в запрос приходит только имя.
DID_OF = {e["name"]: e["did"] for e in CATALOG}


def human_name(name: str) -> str:
    """Русское имя параметра по одному его имени, без DID на входе."""
    if name.startswith("DTC:"):
        # у кода неисправности осмысленное имя - только описание из базы DiagBox,
        # оно лежит в label и собрать его из мнемоники нельзя
        return ""
    d = DID_OF.get(name)
    from ru_labels import label as ru_label
    return ru_label(d, name) if d else short_label(name)


def metric_sql(names):
    """Имя серии - подставляется в запрос, а не берётся из поля label в базе.

    Поле label заполняется один раз при первой встрече параметра и потом не
    пересматривается, поэтому в архиве кластера лежат старые автопереводы вида
    "V46 32:mp facteur correction richesse amont" - именно они и попадали в
    легенду. Переписать их можно только с доступом к Postgres (relabel.py), а
    дашборд должен читаться и до этого. Поэтому имя вычисляется здесь и уезжает
    в запрос: тогда легенда верна независимо от состояния базы.

    ELSE оставлен на случай, если в панель попадёт параметр, которого не было
    при генерации.
    """
    parts = []
    for n in names:
        lab = norms.title(n) or human_name(n)
        if not lab:
            continue      # коды неисправностей: описание есть только в базе
        parts.append("WHEN '%s' THEN '%s'" % (n.replace("'", "''"),
                                              lab.replace("'", "''")))
    if not parts:
        return "coalesce(p.label, p.name)"
    return "CASE p.name\n       " + "\n       ".join(parts) + \
           "\n       ELSE coalesce(p.label, p.name) END"


def target(names, ref="A"):
    return {
        "refId": ref,
        "datasource": DS,
        "format": "time_series",
        "rawQuery": True,
        "rawSql": SERIES_SQL.format(names=sql_in(names),
                                    metric=metric_sql(names)),
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


# Границы для параметров, где есть смысл в пороговых линиях.
# Температура масла: до 110 норма, 110-125 высокая нагрузка, 125-140 "опасненько",
# выше 140 масло деградирует быстро. Скорость окисления примерно удваивается
# на каждые 10 °C выше сотни, отсюда и шаг.
THRESHOLDS = {
    "MP_TEMPERATURE_HUILE_MOTEUR_CALCULEE": [
        (None, "green"), (110, "#EAB839"), (125, "orange"), (140, "red")],
    "TEMPERATURE_HUILE_MESUREE": [
        (None, "green"), (110, "#EAB839"), (125, "orange"), (140, "red")],
    "MP_TENSION_ALIMENTION_BSI": [
        (None, "red"), (11.5, "orange"), (12.4, "green"), (15.0, "orange")],
    # Топливо: чем меньше, тем хуже, поэтому базовый цвет красный,
    # а зелёный начинается сверху. Объём бака посчитан по данным машины:
    # 49.46 л при указателе 81% даёт 61 л.
    "MP_NIVEAU_CARBURANT_MESURE": [
        (None, "red"), (5, "orange"), (10, "green")],
    # Напряжение покоя свинцового аккумулятора: 12.7 - полный заряд,
    # 12.4 - около половины, ниже 12.0 - глубокий разряд и сульфатация.
    "MP_TENSION_BATTERIE_AU_REPOS": [
        (None, "red"), (12.0, "orange"), (12.4, "#EAB839"), (12.7, "green")],
    # Уровень масла: BSI отдаёт процент между метками min и max на щупе.
    "MP_NIVEAU_HUILE_MOTEUR_MOYENNE": [
        (None, "red"), (20, "orange"), (40, "green")],
    # Км до ТО: чем меньше, тем ближе обслуживание.
    "MP_NOMBRE_KILOMETRE_AVANT_MAINTENANCE": [
        (None, "red"), (500, "orange"), (1500, "green")],
    # Расход: тут наоборот, чем больше тем хуже.
    "MP_CONSOMMATION_CARBURANT_MOYENNE_TRAJET1": [
        (None, "green"), (9, "#EAB839"), (11, "orange"), (14, "red")],
}

# Пределы оси для панелей с порогами. Без них Grafana масштабирует ось по данным
# (99..101 °C), и линии на 110/125/140 просто не попадают в кадр. Платим тем, что
# рабочие колебания видны мельче - зато границы на месте.
AXIS_RANGE = {
    "MP_TEMPERATURE_HUILE_MOTEUR_CALCULEE": (80, 150),
    "TEMPERATURE_HUILE_MESUREE": (80, 150),
    "MP_TENSION_ALIMENTION_BSI": (10, 16),
    "MP_NIVEAU_CARBURANT_MESURE": (0, 61),
    # Верх оси 17, а не 13, чтобы в кадр попало и фактическое показание 16.1 В -
    # иначе панель выглядела бы пустой. См. предупреждение в подсказке.
    "MP_TENSION_BATTERIE_AU_REPOS": (11, 17),
    "MP_NIVEAU_HUILE_MOTEUR_MOYENNE": (0, 100),
    # интервал ТО у этой машины около 10 400 км
    "MP_NOMBRE_KILOMETRE_AVANT_MAINTENANCE": (0, 11000),
    "MP_CONSOMMATION_CARBURANT_MOYENNE_TRAJET1": (0, 20),
}

# Что означают цветные линии - выводится в подсказке панели (значок i в углу).
THRESHOLD_HELP = {
    "MP_TENSION_BATTERIE_AU_REPOS":
        "ВНИМАНИЕ: сам ряд отмасштабирован неверно. Показывает 16.1 В, что для "
        "покоящегося свинцового аккумулятора невозможно - физический предел около "
        "13 В. В базе DiagBox factor=0.01, offset=11.0, byte_length=2. Но при "
        "таких множителе и смещении ДВУХбайтовое поле охватывает 11-666 В, что "
        "бессмысленно, а ОДНОбайтовое - ровно 11.00-13.55 В, то есть именно "
        "диапазон напряжения покоя. Похоже, длина поля в базе неверна; "
        "подтверждается сырыми байтами через probe_raw.py. "
        "Сами границы верны: 12.7 - полный заряд, 12.4 - около половины, "
        "ниже 12.0 - глубокий разряд.",

    "MP_TEMPERATURE_HUILE_MOTEUR_CALCULEE":
        "Пороговые линии: 110 °C - высокая нагрузка (трасса, жара, прицеп), "
        "125 °C - долго держать не стоит, 140 °C - масло деградирует быстро, "
        "искать причину. Норма 90-110 °C. На холостых без обдува температура "
        "ползёт вверх - это ожидаемо.",
    "TEMPERATURE_HUILE_MESUREE":
        "То же, что и у расчётной температуры: 110 / 125 / 140 °C.",
    "MP_TENSION_ALIMENTION_BSI":
        "Ниже 11.5 В - глубокий разряд, 12.4 В - нижняя граница нормы покоя, "
        "выше 15 В - перезаряд, подозрение на регулятор генератора.",
    "MP_NIVEAU_HUILE_MOTEUR_MOYENNE":
        "Процент между метками min и max на щупе, а не от объёма картера. "
        "Ниже 20% пора долить, ниже нуля - уровень под минимумом. "
        "Точная привязка процентов к меткам не проверена на этой машине.",
    "MP_NOMBRE_KILOMETRE_AVANT_MAINTENANCE":
        "Интервал у этой машины около 10 400 км (посчитано: 9784 км пройдено "
        "с последнего сброса плюс 630 остатка). Ниже 1500 км - пора планировать, "
        "ниже 500 - записываться.",
    "MP_CONSOMMATION_CARBURANT_MOYENNE_TRAJET1":
        "Средний расход по счётчику поездки 1. У этой машины он 8 л/100 км, что "
        "для бензинового двигателя нормально. Выше 11 - обычно город и пробки, "
        "выше 14 стоит искать причину: свечи, датчик кислорода, забитый фильтр. "
        "Внимание: счётчик поездки 1 упёрся в 9999 км, поэтому средний расход "
        "считается за очень длинный период и на изменения реагирует медленно - "
        "сбрось счётчик поездки, чтобы он ожил.",
    "MP_NIVEAU_CARBURANT_MESURE":
        "Бак примерно 61 л (посчитано: 49.5 л при указателе 81%). Ниже 10 л - "
        "резерв. Ниже 5 л реальный риск: погружной насос охлаждается и "
        "смазывается самим бензином, и при малом остатке греется сильнее, а на "
        "торможении и в повороте может хватить воздуха. Плюс со дна тянется "
        "осадок. А вот распространённое \"не ниже половины бака\" - миф, до "
        "резерва ездить нормально.",
}


# Подписи к границам. Тултип умеет показывать только СЕРИИ, а пороговая линия
# Grafana серией не является - подписать её никак. Поэтому границы добавляются
# в запрос как константные серии с говорящими именами: тогда они видны и в
# легенде, и в тултипе при наведении.
THRESHOLD_LINES = {
    "MP_TEMPERATURE_HUILE_MOTEUR_CALCULEE": [
        (110, "граница: высокая нагрузка", "#EAB839"),
        (125, "граница: долго держать нельзя", "orange"),
        (140, "граница: масло деградирует", "red"),
    ],
    "TEMPERATURE_HUILE_MESUREE": [
        (110, "граница: высокая нагрузка", "#EAB839"),
        (125, "граница: долго держать нельзя", "orange"),
        (140, "граница: масло деградирует", "red"),
    ],
    "MP_TENSION_ALIMENTION_BSI": [
        (11.5, "граница: глубокий разряд", "red"),
        (12.4, "граница: низ нормы покоя", "#EAB839"),
        (15.0, "граница: перезаряд", "orange"),
    ],
    "MP_NIVEAU_CARBURANT_MESURE": [
        (10, "граница: резерв, пора на АЗС", "#EAB839"),
        (5, "граница: насос хуже охлаждается", "orange"),
    ],
    "MP_TENSION_BATTERIE_AU_REPOS": [
        (12.7, "граница: полный заряд", "green"),
        (12.4, "граница: около 50% заряда", "#EAB839"),
        (12.0, "граница: глубокий разряд", "red"),
    ],
    "MP_NIVEAU_HUILE_MOTEUR_MOYENNE": [
        (40, "граница: рабочий уровень", "green"),
        (20, "граница: пора долить", "orange"),
    ],
    "MP_NOMBRE_KILOMETRE_AVANT_MAINTENANCE": [
        (1500, "граница: планировать ТО", "#EAB839"),
        (500, "граница: ТО вплотную", "orange"),
    ],
    "MP_CONSOMMATION_CARBURANT_MOYENNE_TRAJET1": [
        (9, "граница: обычный расход", "#EAB839"),
        (11, "граница: много, город и пробки", "orange"),
        (14, "граница: искать причину", "red"),
    ],
}



# Границы берутся в два слоя: сперва выведенные вручную выше, потом norms.py.
# Порядок именно такой - в ручных учтены расчёты по этой машине (объём бака,
# интервал ТО, неверный масштаб напряжения покоя), и автоматика их не должна
# затирать. norms.py добирает остальные физические величины.
import norms


def title_for(name, fallback):
    return norms.title(name) or fallback


def steps_for(name):
    return THRESHOLDS.get(name) or norms.steps(name)


def lines_for(name):
    return THRESHOLD_LINES.get(name) or norms.lines(name)


def axis_for(name):
    return AXIS_RANGE.get(name) or norms.axis(name)


def help_for(name):
    return THRESHOLD_HELP.get(name) or norms.help_text(name)


def target_with_lines(name, lines):
    """Запрос параметра плюс горизонтальные линии-границы как отдельные серии.

    Каждая граница - две точки на краях выбранного интервала, поэтому рисуется
    ровной линией через весь график и подписывается в тултипе.
    """
    # ORDER BY убираем: внутри UNION ALL он недопустим, сортировка идёт в конце
    base = SERIES_SQL.format(names=sql_in([name]),
                             metric=metric_sql([name])).replace("\nORDER BY 1", "")
    parts = [base]
    for value, title, _ in lines:
        label = title.replace("'", "''")
        parts.append(
            f"SELECT $__timeFrom()::timestamptz AS \"time\", '{label}' AS metric, {value}\n"
            f"UNION ALL SELECT $__timeTo()::timestamptz, '{label}', {value}"
        )
    sql = "\nUNION ALL\n".join(parts) + "\nORDER BY 1"
    return {"refId": "A", "datasource": DS, "format": "time_series",
            "rawQuery": True, "rawSql": sql}


def line_overrides(lines):
    """Границы рисуем пунктиром, без заливки, чтобы не мешали основному ряду."""
    out = []
    for _, title, color in lines:
        out.append({
            "matcher": {"id": "byName", "options": title},
            "properties": [
                {"id": "color", "value": {"mode": "fixed", "fixedColor": color}},
                {"id": "custom.lineStyle",
                 "value": {"fill": "dash", "dash": [10, 10]}},
                {"id": "custom.lineWidth", "value": 1},
                {"id": "custom.fillOpacity", "value": 0},
                {"id": "custom.hideFrom",
                 "value": {"legend": False, "tooltip": False, "viz": False}},
            ],
        })
    return out


def with_thresholds(p, name):
    """Дорисовать пороговые линии, если для параметра они заданы."""
    steps = steps_for(name)
    if not steps:
        return p
    p["fieldConfig"]["defaults"]["thresholds"] = {
        "mode": "absolute",
        "steps": [{"value": v, "color": c} for v, c in steps],
    }
    # dashed+area: пунктирная линия плюс подкраска зоны за порогом
    # только заливка зон: пунктирные линии теперь рисуются подписанными сериями,
    # иначе на тех же значениях получилось бы по две линии
    style = "area" if lines_for(name) else "dashed+area"
    p["fieldConfig"]["defaults"].setdefault("custom", {})["thresholdsStyle"] = {
        "mode": style
    }
    rng = axis_for(name)
    if rng:
        p["fieldConfig"]["defaults"]["min"] = rng[0]
        p["fieldConfig"]["defaults"]["max"] = rng[1]
    help_text = help_for(name)
    if help_text:
        p["description"] = help_text
    return p


def mini(pid, title, name, gx, gy, unit="", gw=12, gh=7):
    """Компактный график для обзора.

    Раньше здесь была панель stat с большой цифрой, но у неё в Grafana НЕТ
    тултипа - спарклайн декоративный и на наведение не реагирует. Поэтому обзор
    собран из обычных timeseries: текущее значение видно в легенде (Last),
    а по наведению доступно значение в любой момент времени.
    """
    lines = lines_for(name)
    tgt = target_with_lines(name, lines) if lines else target([name], "A")
    p = panel(pid, title, gx, gy, gw, gh, [tgt], unit)
    # multi: чтобы при наведении были видны и значение, и все границы сразу
    p["options"] = {
        "legend": {"showLegend": True, "displayMode": "list",
                   "placement": "bottom", "calcs": ["lastNotNull"]},
        "tooltip": {"mode": "multi" if lines else "single", "sort": "none"},
    }
    if lines:
        p["fieldConfig"]["overrides"] = line_overrides(lines)
    p["fieldConfig"]["defaults"]["custom"] = {
        "lineWidth": 2, "fillOpacity": 15, "showPoints": "never",
        "spanNulls": True,
    }
    return with_thresholds(p, name)


LATEST_SQL = (
    'SELECT {metric} AS "Параметр", p.unit AS "Ед.",\n'
    '       l.value AS "Значение", l.ts AS "Обновлено"\n'
    "FROM param p\n"
    "JOIN LATERAL (SELECT value, ts FROM reading WHERE param_id = p.id\n"
    "              ORDER BY ts DESC LIMIT 1) l ON true\n"
    "WHERE p.name IN ({names})\n"
    "ORDER BY 1"
)


def latest_table(pid, title, names, gh=16, gy=0):
    """Таблица последних значений: для дискретных состояний это полезнее графика."""
    return {
        "id": pid,
        "type": "table",
        "title": title,
        "datasource": DS,
        "gridPos": {"h": gh, "w": 24, "x": 0, "y": gy},
        "targets": [{"refId": "A", "datasource": DS, "format": "table",
                     "rawQuery": True,
                     "rawSql": LATEST_SQL.format(names=sql_in(names),
                                                metric=metric_sql(names))}],
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": []},
        "options": {"showHeader": True, "footer": {"show": False},
                    "sortBy": [{"displayName": "Параметр", "desc": False}]},
    }


def row(pid, title, gy, collapsed=False, panels=None):
    """Разделитель-заголовок, при collapsed=True - с вложенными панелями.

    История туда-обратно. Сначала группы были свёрнуты, и до любого графика надо
    было доклацываться - развернули всё в один уровень. Потом (11.09) владелец
    попросил все параметры графиками, панелей стало 350, и загрузка дашборда
    превратилась бы в 350 запросов каждые 10 с. Компромисс в collapsed_row():
    ряды до 12 панелей открыты, большие свёрнуты и грузятся по клику.
    """
    return {"id": pid, "type": "row", "title": title, "collapsed": collapsed,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": gy}, "panels": panels or []}


# единицы каталога -> единицы Grafana
UNIT_MAP = {"°C": "celsius", "V": "volt", "%": "percent", "km": "suffix: км",
            "km/h": "velocitykmh", "Rpm": "rotrpm", "ms": "ms", "L": "litre",
            "month(s)": "", "A": "amp", "Nm": "", "s": "s"}


HERE = os.path.dirname(os.path.abspath(__file__))
CLUSTER_STATS = os.path.join(HERE, "cluster_stats.psv")


def cluster_stats(path=CLUSTER_STATS):
    """Статистика параметров из АРХИВА в кластере: имя -> (n, различных, макс).

    Источник данных для Grafana - кластер, а не локальный буфер, поэтому решать
    "рисовать ли панель" надо по кластеру. Локальный car.db периодически чистится
    (cleanup.py выносит мусорные значения), и из-за расхождения 17 параметров с
    данными в архиве вообще не попадали на дашборд. Снимается ./fetch-stats.sh.
    """
    if not os.path.exists(path):
        return {}
    out = {}
    for line in open(path):
        f = line.rstrip("\n").split("|")
        if len(f) < 6:
            continue
        try:
            out[f[0]] = (int(f[2]), int(f[3]), abs(float(f[5])))
        except ValueError:
            continue
    return out


def local_stats(db_path=os.path.join(HERE, "car.db")):
    """То же из локального буфера - запас на случай, когда кластер недоступен."""
    if not os.path.exists(db_path):
        return {}
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT p.name, count(v.value), count(DISTINCT v.value), "
            "       max(abs(v.value)) FROM param p "
            "LEFT JOIN reading v ON v.param_id = p.id GROUP BY p.id").fetchall()
        db.close()
        return {n: (c, d, m or 0.0) for n, c, d, m in rows}
    except sqlite3.Error:
        return {}


def merge_stats():
    """Объединение архива кластера и локального буфера.

    Брать что-то одно нельзя. Кластер - основной источник для Grafana, но вне
    домашней сети ./fetch-stats.sh не отрабатывает и файл остаётся вчерашним; тогда
    свежие параметры чужих блоков в него не попадают и панели для них не рисуются.
    Локальный буфер, наоборот, периодически чистится и теряет историю. Поэтому берём
    объединение, а по каждому параметру - большее число замеров.
    """
    a, b = cluster_stats(), local_stats()
    out = dict(a)
    for name, v in b.items():
        cur = out.get(name)
        if cur is None or v[0] > cur[0]:
            out[name] = v
    return out, a, b


STATS, _CL, _LO = merge_stats()
SOURCE = (f"кластер {len(_CL)} + локально {len(_LO)}, объединение {len(STATS)}")
COUNTS = {n: v[0] for n, v in STATS.items()}
DISTINCT = {n: v[1] for n, v in STATS.items()}
MAXES = {n: v[2] for n, v in STATS.items() if v[2]}
# Фильтр включаем только когда данных набралось достаточно, иначе на пустой базе
# он выкинул бы вообще всё.
FILTER_EMPTY = sum(1 for v in COUNTS.values() if v) > 50


# Приставки имён, которые не бывают графиком: номера железа и ПО, даты
# изготовления, индексы телекодирования. У них по одному замеру, поэтому общее
# правило is_constant (нужно 20 замеров) их не ловило, и номер железа рисовался
# графиком со значением 652906553984 на всю панель.
ID_PREFIX = ("ID_", "CFG", "CONFIG_", "TYPE_")


def is_identifier(name):
    m = name.split(":", 1)[-1]
    return m.startswith(ID_PREFIX) or m == "APC"


def is_constant(name):
    """Значение не менялось ни разу за всю историю.

    Такому параметру график не нужен: это прямая линия, занимающая пол-панели.
    Место ему в таблице текущих значений. Порог по числу замеров нужен, чтобы
    не записать в константы параметр, который просто измерен два раза.
    """
    if is_identifier(name):
        return True
    n, d, _ = STATS.get(name, (0, 0, 0.0))
    return n >= 20 and d <= 1


def has_data(name):
    return (not FILTER_EMPTY) or COUNTS.get(name, 0) > 0


def magnitude(name):
    """Порядок величины параметра. Незнакомые считаем средними."""
    m = MAXES.get(name)
    return int(math.floor(math.log10(m))) if m and m > 0 else 0


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
        if not has_data(name):
            continue          # панель показала бы только "No data"
        item = (name, unit, did)
        if name.startswith("CFG_"):
            groups["Конфигурация"].append(item)
        elif is_constant(name):
            # За всю историю значение не менялось - график был бы прямой линией.
            # Таких на этой машине большинство: флаги, пороги обслуживания,
            # настройки меню. В таблице текущих значений они читаются, а панелей
            # не занимают.
            groups["Состояния и флаги"].append(item)
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


# Обзор - набор, отобранный руками. Панели здесь стоят по решению, а не по
# автоматике: часть параметров пока постоянна (уровень масла, счётчик поездки,
# средний расход), но они меняются со временем, поэтому проверка дашборда не
# должна считать их вырожденными графиками. audit_dashboard.py читает этот
# список именно для того, чтобы отличать намеренное от случайного.
# Напряжение АКБ в покое (DA4D) убрано: показывает неизменные 16.1 В, что для
# покоящейся батареи невозможно. Сырое значение выходит 0x01FE, а 0xFE - это
# штатная заглушка "нет данных" у PSA, то есть в базе DiagBox у этого поля,
# похоже, завышена длина, и параметр на этой машине просто недоступен - как и
# DA46, который честно отдаёт FFFE. Панель с порогами при таком значении не
# информирует, а вводит в заблуждение. Вернуть, когда замер мультиметром и
# probe_raw.py DA4D покажут настоящую раскладку байт.
OVERVIEW = [
    ("Обороты", "MP_REGIME_MOTEUR_AFFICHE", "rotrpm"),
    ("Скорость", "MP_VITESSE_VEHICULE_a", "velocitykmh"),
    ("Температура масла", "MP_TEMPERATURE_HUILE_MOTEUR_CALCULEE", "celsius"),
    ("Питание BSI", "MP_TENSION_ALIMENTION_BSI", "volt"),
    ("Пробег общий", "MP_KILOMETRAGE_TOTAL", "suffix: км"),
    ("Топливо в баке", "MP_NIVEAU_CARBURANT_MESURE", "litre"),
    ("Запас хода", "MP_AUTONOMIE_CARBURANT_CALCULE", "suffix: км"),
    ("Температура за бортом", "MP_TEMPERATURE_EXTERIEURE", "celsius"),
    ("Уровень масла", "MP_NIVEAU_HUILE_MOTEUR_MOYENNE", "percent"),
    ("Пробег поездки 1", "MP_KILOMETRAGE_TRAJET1", "suffix: км"),
    ("Расход средний", "MP_CONSOMMATION_CARBURANT_MOYENNE_TRAJET1", "suffix: л/100км"),
    ("Км до ТО", "MP_NOMBRE_KILOMETRE_AVANT_MAINTENANCE", "suffix: км"),
]


def ecu_sections():
    """Параметры чужих блоков, разложенные по блокам.

    Имена у них с приставкой рода блока (V46_32:MP_...), потому что DID у разных
    блоков совпадают и означают разное. Приставка же даёт и заголовок раздела.
    Без этого раздела 91 собранный параметр чужих блоков не показывался на
    дашборде вообще: categorise() перебирает только живые DID BSI.
    """
    from ecu_catalog import ECUS
    fam_ru, unit_of = {}, {}
    for info in ECUS.values():
        fam_ru[info["fam"]] = info["ru"]
        for p in info["params"]:
            unit_of[f"{info['fam']}:{p['name']}"] = p["unit"]
    groups = {}
    for name, (cnt, _, _) in STATS.items():
        if ":" not in name or not cnt:
            continue
        groups.setdefault(name.split(":", 1)[0], []).append((name, unit_of.get(name, "")))
    return groups, fam_ru


def join_titles(titles) -> str:
    """Заголовок панели из нескольких параметров.

    Обрезка по 26 знаков съедала как раз то, чем параметры различались: четыре
    панели времени впрыска назывались "Temps injection cylindre 0" - номер
    цилиндра оказывался за границей. Поэтому предел выше, а точные повторы
    убираются, чтобы не получить одно имя четыре раза через точку.
    """
    out = []
    for t in titles:
        t = t[:38]
        if t not in out:
            out.append(t)
    return " · ".join(out)


def short_label(name: str) -> str:
    """Заголовок панели: по-русски из ecu_labels, автоперевод - только запас."""
    import ecu_labels
    from ru_labels import humanise
    return ecu_labels.label(name) or humanise(name.split(":", 1)[-1])


def build():
    pid = 1
    panels = []
    y = 0

    # --- обслуживание: что и когда менять ---
    # Таблица maintenance (maintenance.py) против текущего пробега BSI. Ресурс в
    # процентах - большее из "по км" и "по времени"; позиция без даты последней
    # замены считается израсходованной на 100 %: неизвестно = пора. Зоны: до 80 %
    # зелёная, 80-100 жёлтая, выше - красная. Те же пороги стоят в правилах алертов
    # (homelab-infra, monitoring/grafana/alerting.yaml), чтобы графики и телеграм
    # говорили одно и то же.
    panels.append(row(pid, "Обслуживание", y, collapsed=False)); pid += 1; y += 1
    maint_cte = (
        "WITH odo AS (\n"
        "  SELECT r.value AS km FROM reading r JOIN param p ON p.id = r.param_id\n"
        "  WHERE p.name = 'MP_KILOMETRAGE_TOTAL' ORDER BY r.ts DESC LIMIT 1),\n"
        "m AS (\n"
        "  SELECT m.*, odo.km AS now_km,\n"
        "    CASE WHEN m.interval_km IS NOT NULL AND m.last_km IS NOT NULL\n"
        "         THEN (odo.km - m.last_km) / m.interval_km * 100 END AS pct_km,\n"
        "    CASE WHEN m.interval_months IS NOT NULL AND m.last_ts IS NOT NULL\n"
        "         THEN EXTRACT(EPOCH FROM (now() - m.last_ts))\n"
        "              / (m.interval_months * 30.4375 * 86400) * 100 END AS pct_t\n"
        "  FROM maintenance m CROSS JOIN odo),\n"
        "r AS (\n"
        "  SELECT *,\n"
        "    CASE WHEN interval_km IS NULL AND interval_months IS NULL THEN NULL\n"
        "         WHEN last_ts IS NULL AND last_km IS NULL THEN 100\n"
        "         ELSE GREATEST(COALESCE(pct_km, 0), COALESCE(pct_t, 0)) END AS pct\n"
        "  FROM m)\n"
    )
    gauge = panel(pid, "Ресурс до следующей замены, %", 0, y, 9, 11, [{
        "refId": "A", "datasource": DS, "format": "table", "rawQuery": True,
        "rawSql": maint_cte + 'SELECT title AS "Позиция", pct AS "Ресурс"\n'
                  "FROM r WHERE pct IS NOT NULL ORDER BY pct DESC",
    }], "percent", kind="bargauge")
    gauge["fieldConfig"]["defaults"].update({
        "min": 0, "max": 120, "decimals": 0,
        "thresholds": {"mode": "absolute", "steps": [
            {"color": "green", "value": None}, {"color": "#EAB839", "value": 80},
            {"color": "red", "value": 100}]},
    })
    gauge["options"] = {
        "orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
        "reduceOptions": {"values": True, "calcs": ["lastNotNull"], "fields": "/Ресурс/"},
    }
    gauge["description"] = (
        "Сколько ресурса выработано с последней замены: большее из доли по пробегу и "
        "доли по времени. 100 % - пора. Позиция без даты последней замены показывается "
        "как 100 %: неизвестно, значит считаем, что пора. Пробег - текущий по BSI."
    )
    panels.append(gauge); pid += 1
    table = panel(pid, "Что, когда меняли и когда менять", 9, y, 15, 11, [{
        "refId": "A", "datasource": DS, "format": "table", "rawQuery": True,
        "rawSql": maint_cte +
                  'SELECT title AS "Позиция", pct AS "Ресурс, %",\n'
                  "  to_char(last_ts, 'YYYY-MM-DD') AS \"Сделано\", last_km AS \"На пробеге\",\n"
                  "  CASE WHEN interval_km IS NOT NULL AND last_km IS NOT NULL\n"
                  "       THEN last_km + interval_km END AS \"Следующее, км\",\n"
                  "  CASE WHEN interval_km IS NOT NULL AND last_km IS NOT NULL\n"
                  "       THEN last_km + interval_km - now_km END AS \"Осталось, км\",\n"
                  "  CASE WHEN interval_months IS NOT NULL AND last_ts IS NOT NULL\n"
                  "       THEN to_char(last_ts + (interval_months || ' months')::interval,"
                  " 'YYYY-MM-DD') END AS \"Следующее, дата\",\n"
                  '  notes AS "Примечание"\n'
                  "FROM r ORDER BY pct DESC NULLS LAST",
    }], "", kind="table")
    table["fieldConfig"]["defaults"].update({"custom": {"align": "auto", "cellOptions": {"type": "auto"}}})
    table["fieldConfig"]["overrides"] = [{
        "matcher": {"id": "byName", "options": "Ресурс, %"},
        "properties": [
            {"id": "unit", "value": "percent"}, {"id": "decimals", "value": 0},
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
            {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                {"color": "green", "value": None}, {"color": "#EAB839", "value": 80},
                {"color": "red", "value": 100}]}},
        ]}, {
        "matcher": {"id": "byName", "options": "Осталось, км"},
        "properties": [{"id": "unit", "value": "suffix: км"}, {"id": "decimals", "value": 0}],
    }]
    table["options"] = {"showHeader": True, "cellHeight": "sm"}
    table["description"] = (
        "Источник - таблица maintenance в базе машины (maintenance.py). Отметить замену: "
        "maintenance.py --done <позиция> --at <когда> --km <пробег>. Пустое поле 'Сделано' "
        "означает, что дата последней замены неизвестна."
    )
    panels.append(table); pid += 1
    y += 11

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
    known = {e["name"] for e in CATALOG}
    col = 0
    for title, name, unit in OVERVIEW:
        if name not in known:
            continue          # параметра нет на этой машине - панель не рисуем
        panels.append(mini(pid, title, name, (col % 2) * 12, y + (col // 2) * 7, unit))
        pid += 1
        col += 1
    y += 7 * ((col + 1) // 2)
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

    # --- ДТОЖ и вентилятор: панель под охоту за отваливающимся датчиком ------
    # Строится ВСЕГДА, даже когда данных ещё нет: пока watch_coolant.py ни разу
    # не запускался, панель показывает "No data", и это правильнее, чем спрятать
    # её - иначе непонятно, куда смотреть после первой охоты.
    #
    # Две оси обязательны: температура около 95, реле - ноль или единица, и на
    # общей шкале реле легло бы в линию по нулю.
    panels.append(row(pid, "Датчик температуры ОЖ и вентилятор", y, collapsed=False))
    pid += 1; y += 1

    COOL = "V46_32:MP_TEMPERATURE_D_EAU_MOTEUR_d"
    FANR = "V46_32:MP_ETAT_RELAIS_GMV"
    FANS = "V46_32:MP_CONSIGNE_VITESSE_GMV_C5"
    # Реле умножается на 100: само оно ноль или единица, а правая ось общая с
    # заданием скорости в процентах, и единица прижалась бы к оси до невидимости.
    hunt_sql = (
        'SELECT r.ts AS "time", \'Температура охлаждающей жидкости\' AS metric, r.value\n'
        "FROM reading r JOIN param p ON p.id = r.param_id\n"
        f"WHERE p.name = '{COOL}' AND $__timeFilter(r.ts)\n"
        "UNION ALL\n"
        'SELECT r.ts, \'Реле вентилятора (100 = включено)\', r.value * 100\n'
        "FROM reading r JOIN param p ON p.id = r.param_id\n"
        f"WHERE p.name = '{FANR}' AND $__timeFilter(r.ts)\n"
        "UNION ALL\n"
        'SELECT r.ts, \'Задание скорости вентилятора\', r.value\n'
        "FROM reading r JOIN param p ON p.id = r.param_id\n"
        f"WHERE p.name = '{FANS}' AND $__timeFilter(r.ts)\n"
        "ORDER BY 1"
    )
    hunt = panel(pid, "Температура ОЖ и вентилятор вместе", 0, y, 24, 9,
                 [{"refId": "A", "datasource": DS, "format": "time_series",
                   "rawQuery": True, "rawSql": hunt_sql}], "celsius")
    hunt["fieldConfig"]["defaults"]["custom"] = {
        "lineWidth": 2, "fillOpacity": 8, "showPoints": "never", "spanNulls": False,
        "axisLabel": "°C",
    }
    # spanNulls False намеренно: если датчик пропал и значение не пришло, в графике
    # должен быть РАЗРЫВ, а не прямая через пропуск. Разрыв тут и есть событие.
    hunt["fieldConfig"]["defaults"]["thresholds"] = {
        "mode": "absolute",
        "steps": [{"value": v, "color": c} for v, c in (norms.steps(COOL) or [])],
    }
    hunt["fieldConfig"]["defaults"].setdefault("custom", {})["thresholdsStyle"] = {"mode": "area"}
    rng = norms.axis(COOL)
    if rng:
        hunt["fieldConfig"]["defaults"]["min"] = rng[0]
        hunt["fieldConfig"]["defaults"]["max"] = rng[1]
    hunt["fieldConfig"]["overrides"] = [
        # Реле намеренно приглушено: измерено 31.08, что в нормальной работе оно
        # почти всё время в единице, и при масштабе x100 оно превращается в
        # ровную линию по верху, перекрывающую пики задания обдува. Главный
        # сигнал - именно задание, оно плавно идёт за температурой.
        {"matcher": {"id": "byName", "options": "Реле вентилятора (100 = включено)"},
         "properties": [
             {"id": "custom.axisPlacement", "value": "right"},
             {"id": "custom.axisLabel", "value": "обдув, %"},
             {"id": "min", "value": 0}, {"id": "max", "value": 100},
             {"id": "unit", "value": "short"},
             {"id": "custom.lineInterpolation", "value": "stepAfter"},
             {"id": "custom.lineWidth", "value": 1},
             {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [4, 6]}},
             {"id": "custom.fillOpacity", "value": 0},
             {"id": "color", "value": {"mode": "fixed", "fixedColor": "purple"}},
         ]},
        {"matcher": {"id": "byName", "options": "Задание скорости вентилятора"},
         "properties": [
             {"id": "custom.axisPlacement", "value": "right"},
             {"id": "min", "value": 0}, {"id": "max", "value": 100},
             {"id": "unit", "value": "percent"},
             {"id": "custom.lineInterpolation", "value": "stepAfter"},
             {"id": "custom.lineWidth", "value": 2},
             {"id": "custom.fillOpacity", "value": 18},
             {"id": "color", "value": {"mode": "fixed", "fixedColor": "yellow"}},
         ]},
    ]
    hunt["options"] = {
        "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom",
                   "calcs": ["lastNotNull", "min", "max"]},
        "tooltip": {"mode": "multi", "sort": "none"},
    }
    hunt["description"] = (
        "Панель под охоту за отваливающимся датчиком температуры ОЖ. Заполняется "
        "скриптом watch_coolant.py, который сидит в блоке двигателя и опрашивает "
        "два запроса (21C08001 и 21CB8001) с частотой 2 Гц - обычная телеметрия "
        "сюда не пишет, она сидит на BSI и двигатель навещает набегами.\n\n"
        "ЧТО ИСКАТЬ, с образцом. Событие поймано 31.08.2026 в 21:21:31 и выглядит "
        "так: 11 секунд ровных 93 °C при обдуве 29-30%, затем ОДИН замер 108 °C, "
        "обдув мгновенно 29% -> 100%, и за три секунды температура возвращается "
        "к 93. Скачок невозможен физически в обе стороны - масса антифриза не "
        "набирает 15 °C за полсекунды и не теряет их за три. Значит это датчик, "
        "а блок ему верит и гонит вентилятор на полную. Вот и причина того, что "
        "вентилятор иногда ненадолго ревёт.\n\n"
        "Ищи вертикальную иглу на температуре и ступеньку обдува сразу за ней. "
        "Разрыв в линии - тоже событие: значение не пришло вовсе, и разрывы "
        "намеренно не сшиваются.\n\n"
        "ВАЖНО про сигналы: главный - ЗАДАНИЕ ОБДУВА (жёлтое), оно плавно идёт за "
        "температурой: 17% при 53 °C, 22% при 70, 27% при 85, 30% при 93, и до "
        "100% по требованию. Реле (пунктир) в нормальной работе почти всё время "
        "включено и само по себе мало о чём говорит - его оставили для полноты."
    )
    panels.append(hunt)
    pid += 1; y += 9

    dtc = latest_table(pid, "Коды неисправностей двигателя, статус",
                       [], gh=6, gy=y)
    dtc["targets"][0]["rawSql"] = (
        'SELECT coalesce(p.label, p.name) AS "Код и описание", l.value AS "Статус",\n'
        '       l.ts AS "Обновлено"\n'
        "FROM param p\n"
        "JOIN LATERAL (SELECT value, ts FROM reading WHERE param_id = p.id\n"
        "              ORDER BY ts DESC LIMIT 1) l ON true\n"
        "WHERE p.name LIKE 'DTC:%'\n"
        "ORDER BY 1"
    )
    dtc["description"] = (
        "Байт статуса кода неисправности. Бит 0 - неисправность АКТИВНА сейчас, "
        "бит 3 - подтверждена и сохранена. Момент, когда P0116 из сохранённого "
        "становится активным, и есть искомое событие. Пишется watch_coolant.py "
        "раз в полминуты и dtc_read.py вручную."
    )
    panels.append(dtc)
    pid += 1; y += 6

    # Отношение обороты/скорость. Передачу BSI не отдаёт: на механике датчика
    # нет, и "положение селектора" стоит нулём во всех замерах (расшифровки в
    # базе - ASCII P/R/N/D, то есть параметр для автомата). Зато отношение
    # оборотов к скорости на каждой передаче своё, и по нему передача считается.
    # Пока данных мало и все с маневрирования во дворе, где буксует сцепление,
    # поэтому группы не разделяются - нужна поездка с ровными скоростями.
    ratio = panel(pid, "Обороты / скорость (для расчёта передачи)", 0, y, 24, 8, [{
        "refId": "A", "datasource": DS, "format": "time_series", "rawQuery": True,
        "rawSql": 'SELECT r1.ts AS "time", \'обороты / скорость\' AS metric,\n'
                  "       r1.value / r2.value AS value\n"
                  "FROM reading r1\n"
                  "JOIN param p1 ON p1.id = r1.param_id\n"
                  "     AND p1.name = 'MP_REGIME_MOTEUR_AFFICHE'\n"
                  "JOIN reading r2 ON r2.ts = r1.ts\n"
                  "JOIN param p2 ON p2.id = r2.param_id\n"
                  "     AND p2.name = 'MP_VITESSE_VEHICULE_a'\n"
                  "-- ниже 15 км/ч сцепление буксует и отношение бессмысленно\n"
                  "WHERE r2.value > 15 AND $__timeFilter(r1.ts)\n"
                  "ORDER BY 1",
    }])
    ratio["description"] = (
        "Каждой передаче соответствует своё отношение оборотов к скорости, "
        "поэтому по нему передача определяется однозначно. Набери данных в "
        "поездке с ровной скоростью на каждой передаче - на графике проявятся "
        "горизонтальные полосы, и я привяжу их к номерам передач."
    )
    panels.append(ratio)
    pid += 1
    y += 8

    # --- другие блоки: главное -------------------------------------------------
    # Собранные вручную панели по блокам, которые служба читает с 11.09: блок реле,
    # ABS, насос ГУР, подушки. Полные автоматические ряды по каждому блоку идут
    # ниже, а здесь - то, ради чего эти блоки вообще читаются. Русские имена и
    # зоны - в norms.py, оттуда их берёт metric_sql.
    panels.append(row(pid, "Другие блоки: главное", y, collapsed=False)); pid += 1; y += 1

    LIGHTS = ["BSM_2010:MP_COMMANDE_FEU_ROUTE_G", "BSM_2010:MP_COMMANDE_FEU_ROUTE_D",
              "BSM_2010:MP_COMMANDE_FEU_CROISEMENT_G", "BSM_2010:MP_COMMANDE_FEU_CROISEMENT_D"]
    lights = panel(pid, "Свет: команды блока реле (1 = включено)", 0, y, 12, 8,
                   [target(LIGHTS)], "")
    lights["description"] = ("Что блок реле моторного отсека велит фарам. Это независимый "
                             "контроль для перехватчика дальнего света на шине подрулевого: "
                             "если перехват сработал, здесь дальний станет 1.")
    lights["fieldConfig"]["defaults"]["custom"].update({"lineInterpolation": "stepAfter"})
    panels.append(lights); pid += 1
    drl = panel(pid, "Дневные ходовые огни, ШИМ", 12, y, 12, 8,
                [target(["BSM_2010:MP_COMMANDE_FEU_DIURNE_DEDIE_G",
                         "BSM_2010:MP_COMMANDE_FEU_DIURNE_DEDIE_D"])], "percent")
    panels.append(drl); pid += 1; y += 8

    volts = panel(pid, "Напряжения по блокам", 0, y, 12, 8,
                  [target(["MP_TENSION_ALIMENTION_BSI",
                           "V46_32:MP_TENSION_ALIMENTATION_CALCULATEUR_CONTROLE_MOTEUR",
                           "GEP:MP_TENSION_ALIMENTATION",
                           "BSM_2010:MP_TENSION_EXCITATION_ALTERNATEUR"])], "volt")
    volts["description"] = ("Бортовое напряжение глазами четырёх блоков и линия возбуждения "
                            "генератора. Расхождение между блоками - плохая масса или контакт; "
                            "возбуждение к нулю при работающем двигателе - генератор не заряжает.")
    panels.append(volts); pid += 1
    panels.append(mini(pid, "Ток электронасоса ГУР", "GEP:INTENSITE_MESUREE", 12, y, "amp",
                       gw=12, gh=8)); pid += 1; y += 8

    panels.append(mini(pid, "Температура электронасоса ГУР", "GEP:MP_TEMPERATURE_GEP", 0, y,
                       "celsius", gw=12, gh=8)); pid += 1
    wheels = panel(pid, "Скорости колёс (ABS)", 12, y, 12, 8,
                   [target(["ESP81:MP_VITESSE_ROUE_AVANT_GAUCHE", "ESP81:MP_VITESSE_ROUE_AVANT_DROIT",
                            "ESP81:MP_VITESSE_ROUE_ARRIERE_GAUCHE", "ESP81:MP_VITESSE_ROUE_ARRIERE_DROITE"])],
                   "velocitykmh")
    wheels["description"] = ("Снимок раз в три минуты, динамику торможения так не увидеть. Зато "
                             "видно колесо, которое стоит отдельно от остальных - датчик ABS.")
    panels.append(wheels); pid += 1; y += 8

    FLAGS = ["ESP81:MP_NIVEAU_LIQUIDE_DE_FREIN", "BSM_2010:MP_ALERTE_PRESSION_HUILE_MOTEUR_a",
             "BSM_2010:MP_ALERTE_NIVEAU_EAU_MOTEUR", "BSM_2010:MP_NIVEAU_LIQUIDE_LAVE_GLACE",
             "BSM_2010:MP_TENSION_CAPTEUR_NIVEAU_HUILE_MOTEUR",
             "RBG_UDS:MP_COMPTEUR_DE_CHOCS", "RBG_UDS:MP_ETAT_COMMUTATEUR_NEUTRALISATION_COUSSIN_PASSAGER",
             "RBG_UDS:MP_RESISTANCE_LIGNE_COUSSIN_CONDUCTEUR_NIVEAU_1",
             "RBG_UDS:MP_RESISTANCE_LIGNE_COUSSIN_PASSAGER_NIVEAU_1",
             "RBG_UDS:MP_RESISTANCE_LIGNE_COUSSIN_LATERAL_AVANT_GAUCHE",
             "RBG_UDS:MP_RESISTANCE_LIGNE_COUSSIN_LATERAL_AVANT_DROIT",
             "RBG_UDS:MP_RESISTANCE_LIGNE_PRETENSIONNEUR_AVANT_GAUCHE",
             "RBG_UDS:MP_RESISTANCE_LIGNE_PRETENSIONNEUR_AVANT_DROIT"]
    flags = latest_table(pid, "Лампы, уровни и цепи подушек: текущие значения", FLAGS,
                         gh=3 + len(FLAGS), gy=y)
    flags["description"] = ("Уровни и лампы - 0 в норме, 1 сработало. Цепи подушек и "
                            "преднатяжителей - сопротивление пиропатрона с проводкой, норма "
                            "1.5-4.5 Ом. Счётчик срабатываний подушек должен быть 0.")
    panels.append(flags); pid += 1; y += 3 + len(FLAGS)

    # --- коды неисправностей ---
    # Стоят выше обозревателя намеренно: если в машине что-то не так, это первое,
    # что надо увидеть. Пишутся сюда dtc_read.py --sqlite как параметры с именем
    # DTC:<блок>:<код>, значение - байт статуса, ярлык - описание из базы DiagBox.
    dtc_names = sorted(n for n in STATS if n.startswith("DTC:"))
    if dtc_names:
        panels.append(row(pid, f"Коды неисправностей ({len(dtc_names)})", y))
        pid += 1
        y += 1
        gh = max(5, min(14, 3 + len(dtc_names)))
        panels.append(latest_table(pid, "Найденные коды: блок, описание, статус",
                                   dtc_names, gh=gh, gy=y))
        pid += 1
        y += gh

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

    # --- тематические ряды и чужие блоки: ВСЁ графиками, ряды свёрнуты ---------
    # По просьбе владельца (11.09) таблицы текущих значений заменены графиками:
    # даже флаг 0/1 и "константа" информативнее линией - видно, когда именно
    # переключилось, а не только что сейчас. Цена - панелей стало ~260 вместо 156,
    # поэтому эти ряды СВЁРНУТЫ: Grafana не шлёт запросы панелей внутри
    # свёрнутого ряда, пока его не раскроют, и загрузка дашборда не превращается в
    # сотни запросов к Postgres каждые 10 с. Верхние ряды с главным - открыты.
    def graph_chunks(items, label_of, stepped):
        """items: [(имя, единица)] -> панели-графики, до четырёх рядов в каждой,
        сгруппированные по единице и порядку величины; у параметра с зонами -
        своя панель. gridPos.y здесь относительный, ряд сдвинет."""
        nonlocal pid
        zoned = [it for it in items if steps_for(it[0])]
        rest = [it for it in items if not steps_for(it[0])]
        by_unit = {}
        for it in rest:
            by_unit.setdefault((str(it[1]), magnitude(it[0])), []).append(it)
        chunks = [[z] for z in zoned]
        for key in sorted(by_unit):
            grp = by_unit[key]
            chunks += [grp[j:j + 4] for j in range(0, len(grp), 4)]
        out = []
        for i, chunk in enumerate(chunks):
            unit = UNIT_MAP.get(chunk[0][1], "")
            gx, gy = (i % 2) * 12, (i // 2) * 8
            if len(chunk) == 1 and steps_for(chunk[0][0]):
                pan = mini(pid, title_for(chunk[0][0], label_of(chunk[0][0])[:40]),
                           chunk[0][0], gx, gy, unit, gw=12, gh=8)
            else:
                pan = panel(pid, join_titles(label_of(n) for n, _ in chunk),
                            gx, gy, 12, 8, [target([n for n, _ in chunk])], unit)
            if stepped:
                # флаги и конфигурация: ступеньки, а не наклонные линии между 0 и 1
                pan["fieldConfig"]["defaults"]["custom"]["lineInterpolation"] = "stepAfter"
            out.append(pan)
            pid += 1
        return out

    def collapsed_row(title, sub):
        """Ряд с панелями. Маленькие ряды (до 12 панелей) открыты, большие свёрнуты:
        сворачивать пять графиков температур незачем, а 92 панели флагов - надо."""
        nonlocal pid, y
        fold = len(sub) > 12
        r = row(pid, title, y, collapsed=fold)
        pid += 1
        y += 1
        for p in sub:
            p["gridPos"]["y"] += y
        if fold:
            r["panels"] = sub
            panels.append(r)
        else:
            panels.append(r)
            panels.extend(sub)
            y += 8 * ((len(sub) + 1) // 2)

    STEPPED = {"Состояния и флаги", "Конфигурация"}
    for title, items in categorise().items():
        if not items:
            continue
        sub = graph_chunks([(n, u) for n, u, _ in items], human_name, title in STEPPED)
        collapsed_row(f"{title} ({len(items)})", sub)

    groups, fam_ru = ecu_sections()
    for fam in sorted(groups, key=lambda f: -len(groups[f])):
        items = sorted(groups[fam])
        varying = [(n, u) for n, u in items if not is_constant(n)]
        consts = [(n, u) for n, u in items if is_constant(n)]
        sub = graph_chunks(varying, short_label, False)
        below = graph_chunks(consts, short_label, True)
        off = 8 * ((len(sub) + 1) // 2)
        for p in below:
            p["gridPos"]["y"] += off
        collapsed_row(f"{fam_ru.get(fam, fam)} ({len(items)})", sub + below)

    return {
        "uid": "citroen-c4",
        "title": "Citroen C4",
        "tags": ["citroen", "car", "telemetry"],
        # События с машиной (event.py) - вертикальные метки на всех панелях, чтобы
        # видеть, как меняется поведение после ТО. Grafana ждёт колонки time,
        # text, tags; tags - через запятую, здесь это вид события.
        "annotations": {"list": [{
            "name": "События с машиной",
            "datasource": DS,
            "enable": True,
            "hide": False,
            "iconColor": "orange",
            "target": {
                "format": "table",
                "rawQuery": True,
                "rawSql": ("SELECT ts AS time, title AS text, kind AS tags\n"
                           "FROM event\n"
                           "WHERE $__timeFilter(ts)\n"
                           "ORDER BY ts"),
            },
        }]},
        "timezone": "browser",
        "schemaVersion": 39,
        "refresh": "10s",
        # 1 - общий курсор на всех панелях, чтобы читать значения в один момент времени
        "graphTooltip": 1,
        "time": {"from": "now-24h", "to": "now"},
        "templating": {"list": [{
            "name": "param",
            "label": "Параметр",
            "type": "query",
            "datasource": DS,
            # __text - что видно в списке, __value - что уходит в запрос
            # Тот же приём, что в metric_sql: имя подставляется в запрос, иначе в
            # списке выбора остаются старые автопереводы из поля label.
            # Тот же приём, но только для параметров чужих блоков: у них имя в
            # поле label - старый автоперевод. У BSI оно верное, взято из ручной
            # таблицы по DID, и подставлять все 400 имён в запрос незачем -
            # получалось 34 КБ SQL на одну загрузку дашборда.
            # FROM param p - псевдоним обязателен: metric_sql пишет p.name и в ELSE
            # p.label. Без него запрос переменной падал "missing FROM-clause entry
            # for table p", обозреватель не заполнял список, и КАЖДАЯ загрузка
            # дашборда писала в журнал Grafana status=400 - нашлось только прогоном
            # всех 161 запросов через /api/ds/query изнутри пода.
            "query": ("SELECT "
                      + metric_sql([n for n in sorted(STATS) if ":" in n])
                      + " AS \"__text\", p.name AS \"__value\" FROM param p ORDER BY 1"),
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
    # ServerSideApply обязателен, и без него ArgoCD роняет синк.
    #
    # Обычный kubectl apply складывает копию ВСЕГО объекта в аннотацию
    # kubectl.kubernetes.io/last-applied-configuration, а аннотации в Kubernetes
    # ограничены 262144 байтами. Дашборд дорос до ~300 КБ, и синк начал падать:
    #
    #     ConfigMap "grafana-dashboard-citroen" is invalid:
    #     metadata.annotations: Too long: may not be more than 262144 bytes
    #
    # Приложение monitoring встало в OutOfSync, в Grafana осталась версия от
    # 16 августа, и выглядело это как "переводы не применились". Server-Side
    # Apply ведёт учёт полей на стороне API-сервера и эту аннотацию не пишет,
    # поэтому лимит перестаёт упираться при любом размере дашборда.
    print("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: grafana-dashboard-citroen\n"
          "  namespace: monitoring\n  annotations:\n"
          "    argocd.argoproj.io/sync-options: ServerSideApply=true\n"
          "  labels:\n    grafana_dashboard: \"1\"\n"
          "data:\n  citroen.json: |\n" + body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
