"""
Состояния параметров из базы DiagBox - запросом, а не сканированием байт.

Зачем. В enums.py расшифровки собраны по замерам этой машины, и их немного: что
удалось привязать к оборотам, скорости и прочему наблюдаемому. В базе тексты есть
для всех параметров сразу - но их надо брать ровно для НАШЕЙ версии блока.

Требует поднятого контейнера: см. diagbox-sql.sh.

    ./.venv/bin/python gen_states_sql.py > states_fr.py   # собрать
    ./.venv/bin/python gen_states_sql.py --check          # сверить с enums.py

Охват берётся из ecu_catalog: у каждого блока там записан файл DiagBox, из которого
собран наш каталог параметров (например BSI2010_B78_V1), и это же значение лежит в
ECUVER.ECUVEMNEMONAME - то есть ровно одна строка версии на блок. Так область
выборки совпадает с тем, что мы реально читаем с машины, и не расходится с ней при
пересборке каталога.

Три ловушки, каждая стоила неверного результата с первого захода:

* **Форма запроса решает всё.** Цепочка из пяти соединений с DISTINCT раздувает
  промежуточный результат, вариант через EXISTS выполняет подзапрос на каждую строку
  PARAM - оба идут по 6 минут НА БЛОК. Два независимых среза (список PARID версии и
  вся таблица состояний одной выгрузкой), соединённые в питоне, дают те же данные за
  11 секунд на все блоки.
* **Подчёркивание в LIKE это шаблон "любой символ".** 'V46_32_B7%' подбирает заодно
  V46_32_B75C_*, то есть другую платформу. Поэтому здесь сравнение на равенство, а
  не LIKE: единственный способ не думать об экранировании вообще.
* **STAVALUE это не всегда шестнадцатеричное число.** У параметров с кодировкой BMP
  (bit map) значение записано ДВОИЧНОЙ строкой по ширине поля: "00", "01", "10", "11"
  это 0, 1, 2, 3, а вовсе не 0, 1, 16, 17. Разбор как hex сдвигает смысл на соседнее
  состояние - именно из-за него "2 = ограничитель скорости" выглядело противоречащим
  базе, хотя база его подтверждает.

  Решать надо на ВЕСЬ параметр сразу и по полю PARENCODING, а не по виду отдельного
  значения. Правило "строка из нулей и единиц - значит двоичная" ломает перечисления:
  у MP_ETAT_VERROUILLAGE_VEHICULE значения 00, 01, 02, 04, 08, 10, 20 шестнадцатеричные,
  и "10" при таком чтении превращается в 2, затирая настоящее "заперто" подписью
  "передняя кабина отперта". Исключение - ENUM, где значения всё-таки двоичные (их
  видно по тому, что hex не влезает в маску каталога, а двоичное влезает).
* **Одна мнемоника это не один параметр.** MP_ETAT_GMP существует под 42 разными
  PARID, MP_ETAT_RELAIS_GMV под 15, и наборы состояний у них РАЗНЫЕ (у одного из
  пятнадцати реле вентилятора "разомкнуто" на единице, у остальных "включено").
  Слияние по имени даёт кашу. Поэтому набор хранится отдельно на каждый PARID, а в
  STATES попадают только мнемоники с единственным набором внутри версии; спорные
  лежат в STATES_AMBIG и решаются замером.
"""

import json
import os
import sys
from collections import defaultdict
from subprocess import run

HERE = __file__.rsplit("/", 1)[0]
CACHE = f"{HERE}/states_raw.json"

# Какие параметры принадлежат этой версии блока. Только идентификаторы, без текстов.
SQL_PARIDS = """
SET LIST OFF;
SELECT DISTINCT e.ECUVEMNEMONAME || '|' || isp.PARID AS R
FROM ECUVER e
JOIN I_ECUSER i ON i.ECUVEID = e.ECUVEID
JOIN SERVUNIT su ON su.SERID = i.SERID
JOIN SERVUNITFRAME f ON f.SERUNID = su.SERUNID
JOIN I_SERPAR isp ON isp.SERUNFRID = f.SERUNFRID
WHERE e.ECUVEMNEMONAME IN ({names});
"""

# Все состояния всех параметров разом, одной строкой на запись.
SQL_STATES = """
SET LIST OFF;
SELECT p.PARID || '|' || p.PARSNAME || '|' || COALESCE(p.PARENCODING,'-')
    || '|' || st.STAVALUE || '|' || st.STALNAME AS R
FROM STATES st JOIN PARAM p ON p.PARID = st.PARID
WHERE st.STAVALUE IS NOT NULL AND st.STALNAME IS NOT NULL AND st.STALNAME <> '';
"""


def blocks():
    """Файл каталога DiagBox -> наше имя семейства, по тем блокам, что реально читаем."""
    sys.path.insert(0, HERE)
    import ecu_catalog
    import poll_all
    return {e["file"]: e["fam"] for (tx, _), e in sorted(ecu_catalog.ECUS.items())
            if tx not in poll_all.SKIP_BLOCKS}


def masks():
    """fam:мнемоника -> маска поля из нашего каталога, где она есть."""
    sys.path.insert(0, HERE)
    import ecu_catalog
    import poll_all
    res = {}
    for (tx, _), e in sorted(ecu_catalog.ECUS.items()):
        if tx in poll_all.SKIP_BLOCKS:
            continue
        for p in e["params"]:
            if p.get("mask"):
                res[f"{e['fam']}:{p['name']}"] = p["mask"]
    return res


def isql(sql):
    """Сырые строки ответа. isql отдаёт WIN1252, а не UTF-8."""
    out = run([f"{HERE}/diagbox-sql.sh", "DSD", sql], capture_output=True, timeout=1800)
    return out.stdout.decode("cp1252", "replace").splitlines()


def rows(sql, n):
    for line in isql(sql):
        parts = line.strip().split("|", n - 1)
        if len(parts) == n:
            yield parts


def parids(names):
    """Имя версии блока -> множество PARID."""
    res = defaultdict(set)
    quoted = ", ".join(f"'{n}'" for n in names)
    for name, pid in rows(SQL_PARIDS.format(names=quoted), 2):
        if pid.isdigit():
            res[name.strip()].add(int(pid))
    return res


def decode(vals, enc, mask):
    """Значения состояний параметра: сырые строки -> числа.

    Основание - PARENCODING: BMP это bit map, то есть двоичная строка. Остальное
    шестнадцатеричное. Маска из нашего каталога служит арбитром для ENUM, где
    встречается и то и другое: если hex не влезает в поле, а двоичное влезает,
    значит двоичное.
    """
    allbin = all(set(v) <= {"0", "1"} for v in vals)

    def conv(base):
        try:
            return {v: int(v, base) for v in vals}
        except ValueError:
            return None

    if enc == "BMP" and allbin:
        return conv(2)
    hexa = conv(16)
    if allbin and mask and (hexa is None or max(hexa.values()) > mask):
        binary = conv(2)
        if binary and max(binary.values()) <= mask:
            return binary
    return hexa or conv(2)


def all_states():
    """PARID -> (мнемоника, кодировка, {сырое значение: текст})."""
    res = {}
    for pid, name, enc, val, text in rows(SQL_STATES, 5):
        if not pid.isdigit():
            continue
        text = text.strip()
        if text:
            res.setdefault(int(pid), (name, enc, {}))[2][val.strip()] = text
    return res


def collect(force=False):
    if os.path.exists(CACHE) and not force:
        return json.load(open(CACHE))
    blk = blocks()
    msk = masks()
    states = all_states()
    print(f"  состояний в базе: {len(states)} параметров", file=sys.stderr)
    ids = parids(blk)
    res = {}
    for file, fam in blk.items():
        got = defaultdict(lambda: defaultdict(list))
        for pid in sorted(ids.get(file, ())):
            if pid not in states:
                continue
            name, enc, raw = states[pid]
            num = decode(list(raw), enc, msk.get(f"{fam}:{name}"))
            if not num:
                continue
            vals = {num[v]: t for v, t in raw.items()}
            # наборы НЕ объединяются: у одной мнемоники их бывает несколько
            got[name][tuple(sorted(vals.items()))].append(pid)
        amb = 0
        for name, sets in got.items():
            res[f"{fam}:{name}"] = [
                {"parids": p, "states": {str(k): v for k, v in sig}}
                for sig, p in sorted(sets.items(), key=lambda x: -len(x[1]))]
            amb += len(sets) > 1
        print(f"  {file:24} параметров {len(ids.get(file, ())):5}, "
              f"с состояниями {len(got):4}, из них спорных {amb}", file=sys.stderr)
    json.dump(res, open(CACHE, "w"), ensure_ascii=False)
    return res


def check(res):
    """Сверка с enums.py - тем, что собрано по замерам этой машины."""
    sys.path.insert(0, HERE)
    import enums
    same = amb = missing = 0
    for full, ours in enums.ENUMS.items():
        if full == "DTC":
            continue
        mn = full.split(":")[-1]
        key = full if full in res else next(
            (k for k in res if k.endswith(":" + mn)), None)
        if not key:
            missing += 1
            print(f"\n[нет в базе] {full}\n   наши: {ours}")
            continue
        variants = res[key]
        if len(variants) > 1:
            amb += 1
            print(f"\n[спорно: {len(variants)} набора] {full}   (база: {key})"
                  f"\n   наши: {ours}")
            for v in variants:
                print(f"   вариант ({len(v['parids'])} PARID): {v['states']}")
            continue
        same += 1
        theirs = {int(k): v for k, v in variants[0]["states"].items()}
        print(f"\n{full}   (база: {key})")
        for v in sorted(set(ours) | set(theirs)):
            print(f"   {v:>4}  наши: {ours.get(v) or '-':<38} база: {theirs.get(v) or '-'}")
    print(f"\nитого: однозначных {same}, спорных {amb}, нет в базе {missing}",
          file=sys.stderr)


def main():
    res = collect(force="--force" in sys.argv)
    if "--check" in sys.argv:
        check(res)
        return 0
    print('"""Состояния параметров из базы DiagBox, по версиям блоков этой машины.\n\n'
          "СГЕНЕРИРОВАН gen_states_sql.py. Это каталог, то есть ГИПОТЕЗА: проверенные\n"
          'замером расшифровки лежат в enums.py и имеют приоритет.\n"""')
    print("\n# Мнемоники с единственным набором состояний внутри версии блока.\nSTATES = {")
    ambig = {}
    for k in sorted(res):
        if len(res[k]) > 1:
            ambig[k] = res[k]
            continue
        vals = {int(a): b for a, b in res[k][0]["states"].items()}
        print(f"    {k!r}: {vals!r},")
    print("}")
    print("\n# Несколько разных наборов под одной мнемоникой в одной версии блока -\n"
          "# выбрать между ними можно только замером, поэтому сюда, а не в STATES.\n"
          "STATES_AMBIG = {")
    for k in sorted(ambig):
        print(f"    {k!r}: [")
        for v in ambig[k]:
            print(f"        {({int(a): b for a, b in v['states'].items()})!r},")
        print("    ],")
    print("}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
