"""
Опознание блоков этой машины по отпечатку диагностических запросов.

Идея. В дампе переключения (switch.log) видно, какие именно запросы DiagBox
посылал на каждый адрес CAN. В базе DiagBox (клон jyseojys/diag-server) у каждого
блока перечислены его запросы в поле req_frame_hex. Значит адрес можно опознать,
сопоставив наблюдённый набор запросов с каталогами всех 4305 блоков.

Частые запросы вроде 2180 или 21FE есть почти у всех и ничего не различают,
поэтому вес запроса обратно пропорционален числу блоков, где он встречается -
редкий запрос опознаёт, частый почти нет.

Так уже опознан 0x747: запросы 22D7F0 и 22D800..22D860 встречаются только у
BSM_2010 - блока предохранителей и реле моторного отсека.

    ./.venv/bin/python id_ecus.py              # опознать всё, что нашлось в дампе
    ./.venv/bin/python id_ecus.py --rebuild    # заново собрать индекс запросов
"""

import argparse
import collections
import json
import os
import re
import subprocess
import sys

DB = "/home/jaennil/life/citroen/diag-server/ecu_groups_jsons"
INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ecu_req_index.json")


def build_index():
    """Множество req_frame_hex на каждый файл блока. Через grep - на порядок
    быстрее, чем разбирать 800 МБ JSON целиком."""
    out = subprocess.run(
        ["grep", "-ro", '"req_frame_hex": "[^"]*"', DB],
        capture_output=True, text=True, check=False).stdout
    idx = collections.defaultdict(set)
    pat = re.compile(r'^(.*?):"req_frame_hex": "([^"]*)"$')
    for line in out.splitlines():
        m = pat.match(line)
        if not m:
            continue
        f, req = m.group(1), m.group(2).upper()
        if req:
            idx[os.path.basename(f)[:-5]].add(req)
    data = {k: sorted(v) for k, v in idx.items()}
    with open(INDEX, "w") as fh:
        json.dump(data, fh)
    return data


def load_index(rebuild=False):
    if not rebuild and os.path.exists(INDEX):
        return json.load(open(INDEX))
    return build_index()


def observed(path="switch.log", gap=3.0):
    """Наблюдённые запросы по адресам CAN из дампа переключения."""
    recs = []
    for line in open(path):
        if line.startswith("#"):
            continue
        p = line.rstrip("\n").split(",")
        if len(p) < 12 or not p[11] or p[2] != "BULK":
            continue
        if p[3] == "OUT" and p[1] != "S":
            continue
        if p[3] == "IN" and p[1] != "C":
            continue
        recs.append((float(p[0]), p[3], p[6], p[11].lower()))
    dev = collections.Counter(d for _, _, d, _ in recs).most_common(1)[0][0]

    def head(h):
        if not h.startswith("4009") or len(h) < 50:
            return None
        b = bytes.fromhex(h)
        if not (b[3] & 0x80):
            return None
        return b[4], b[5], b[24:-1]

    def addr(pl):
        r, i = {}, 0
        while i + 3 < len(pl):
            if pl[i] == 0x50:
                r[pl[i + 1]] = pl[i + 2] | (pl[i + 3] << 8)
                i += 4
            else:
                i += 1
        return r.get(1)

    # Всплеск - связная серия чтений одного адреса без большой паузы. Резать
    # именно так важно: обзорный проход DiagBox (222100..22212D) уходит многим
    # блокам, и если сваливать все запросы адреса в один набор, обзорные
    # запросы перевешивают те немногие, что реально принадлежат блоку.
    cur, bursts, cb, last = None, [], None, None
    for ts, d, dv, h in recs:
        if d != "OUT" or dv != dev:
            continue
        f = head(h)
        if not f:
            continue
        b4, b5, pl = f
        # Кадр 00/fe тут НЕ граница разговора: DiagBox шлёт его рутинно перед
        # каждым чтением. Адрес держится до следующей таблицы 00/16.
        if b4 == 0x00 and b5 == 0x16:
            a = addr(pl)
            if a:
                if cb:
                    bursts.append(cb)
                cur, cb, last = a, None, None
        elif b4 == 0xFF and b5 == 0x06 and pl and cur:
            if cb is None or (last is not None and ts - last > gap):
                if cb:
                    bursts.append(cb)
                cb = {"tx": cur, "t": ts, "reqs": set()}
            cb["reqs"].add(pl.hex().upper())
            last = ts
    if cb:
        bursts.append(cb)
    return bursts


def expand(req):
    """Пачку 22 DID DID ... развернуть в отдельные 22DID - в базе они так и лежат."""
    b = bytes.fromhex(req)
    if b[0] == 0x22 and len(b) >= 3 and (len(b) - 1) % 2 == 0:
        return {f"22{b[i]:02X}{b[i + 1]:02X}" for i in range(1, len(b), 2)}
    return {req}


def is_generic(req: str) -> bool:
    """Служебный запрос, который DiagBox посылает любому блоку.

    Такие не опознают блок, даже когда в базе редки: 2221xx - стандартный
    идентификационный блок, 22F0xx/22F1xx - версии ПО и VIN, 19xxxx - чтение
    кодов неисправностей. Именно из-за них адрес 0x747 опознавался как IVI:
    IVI - единственный каталог, где 2221xx выписаны явно, тогда как реальный
    отпечаток блока лежал в 22D7F0 и 22D800..22D860.
    """
    return (req.startswith(("22F0", "22F1", "2221", "19"))
            or req in ("220100", "21FE", "2180", "2182"))


def family(name):
    """BSM_2010_B7_V1 -> BSM. Различать варианты платформы смысла нет: опознать
    надо род блока, а не какой именно файл DiagBox под него подставит."""
    return name.split("_")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?", default="switch.log")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--min-requests", type=int, default=4,
                    help="меньше запросов во всплеске - судить не о чем")
    args = ap.parse_args()

    idx = load_index(args.rebuild)
    freq = collections.Counter()
    for v in idx.values():
        for r in v:
            freq[r] += 1
    print(f"в индексе блоков: {len(idx)}\n")
    print("Опознаём по ИСКЛЮЧИТЕЛЬНОСТИ набора: ищем блоки, у которых есть ВСЕ")
    print("запросы всплеска. Если такие нашлись и все одного рода - опознано.")
    print("Рейтинги по редкости и по доле покрытия пробованы и негодны: на")
    print("заведомо известном 0x752 они не давали BSI.\n")

    verdict = {}
    for b in observed(args.log):
        reqs = set()
        for r in b["reqs"]:
            reqs |= expand(r)
        reqs = {r for r in reqs if not is_generic(r)}
        if len(reqs) < args.min_requests:
            continue
        # Только полное совпадение набора. Ослабление до «редкой части» пробовано
        # и давало ложное срабатывание: у BSI2010 в базе нет телекодировочных
        # D4xx/D5xx, и всплеск с ними опознавался как OBC.
        full = [n for n, v in idx.items() if reqs <= set(v)]
        loose = False
        if not full:
            continue
        fams = collections.Counter(family(n) for n in full)
        tx = b["tx"]
        entry = verdict.setdefault(tx, collections.Counter())
        # один род на всплеск - иначе всплеск не различающий
        if len(fams) == 1:
            entry[(next(iter(fams)), loose)] += 1

    for tx in sorted(verdict):
        items = verdict[tx].most_common()
        if not items:
            continue
        print(f"0x{tx:03X}: " + "  ".join(
            f"{fam}{'~' if loose else ''} x{n}" for (fam, loose), n in items))
    print("\nx N - во столько всплесков род определился однозначно. Несколько родов")
    print("на одном адресе означают, что часть всплесков неоднозначна: набор")
    print("D400..D41B, например, есть у многих блоков и BSI по нему не выделяется.")
    un = [b["tx"] for b in observed(args.log) if b["tx"] not in verdict]
    if un:
        print("не опознаны: " + " ".join(f"0x{t:03X}" for t in sorted(set(un))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
