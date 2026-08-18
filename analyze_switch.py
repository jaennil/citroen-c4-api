"""
Разбор дампа переключения между блоками (switch.log от capture-switch.sh).

Задача: найти кадр, которым DiagBox сообщает интерфейсу целевой ЭБУ. Метод -
сегментировать дамп по разрывам во времени (между шагами в меню оператор делал
паузы) и сравнить, чем отличаются кадры на границах сегментов.

Строение кадра команды, проверено арифметикой контрольной суммы:

    40 09 <len> c0 <b4> <b5> <plen> 00 <handle>  + 15 нулей + payload + cksum

Байты b4/b5 - самое интересное: у чтения параметров это ff/06, у кадра начала
сессии 00/fe, у служебного 00/09. Если выбор блока передаётся явно, он тут.
Опрос/выборка/подтверждение (410901c0f4, 430901c0f2, 064409) - шум, они одинаковы
всегда и из разбора исключаются.

    ./.venv/bin/python analyze_switch.py switch.log
    ./.venv/bin/python analyze_switch.py switch.log --gap 3
"""

import argparse
import collections
import sys

NOISE = {"410901c0f4", "430901c0f2", "064409", "064009"}


def load(path):
    """Кадры Lexia в порядке появления: (ts, dir, ep, hex)."""
    out = []
    devs = collections.Counter()
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split(",")
            if len(p) < 12 or not p[11]:
                continue
            # ts,type,xfer,dir,ep,bus,dev,status,length,caplen,setup,data
            if p[2] != "BULK":
                continue
            h = p[11].lower()
            # интересуют только реальные передачи данных, submission на OUT и
            # completion на IN - иначе каждый кадр удвоится
            if p[3] == "OUT" and p[1] != "S":
                continue
            if p[3] == "IN" and p[1] != "C":
                continue
            devs[p[6]] += 1
            out.append((float(p[0]), p[3], p[4], p[6], h))
    if not out:
        return [], None
    dev = devs.most_common(1)[0][0]
    return [r for r in out if r[3] == dev], dev


def fields(h):
    """(b4, b5, plen, handle, payload) для кадра команды, иначе None."""
    if not h.startswith("4009") or len(h) < 50:
        return None
    b = bytes.fromhex(h)
    return b[4], b[5], b[6], b[8], b[24:-1].hex()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?", default="switch.log")
    ap.add_argument("--gap", type=float, default=4.0, help="разрыв, режущий на сегменты, с")
    args = ap.parse_args()

    recs, dev = load(args.log)
    if not recs:
        print("в дампе нет bulk-кадров")
        return 1
    t0 = recs[0][0]
    print(f"устройство {dev}, кадров {len(recs)}, "
          f"длительность {recs[-1][0] - t0:.0f} с")

    # сегментация по разрывам между КОМАНДАМИ (не шумом): пауза в меню - это
    # пауза в осмысленном трафике, опрос готовности при этом может продолжаться
    cmds = [(ts, h) for ts, d, ep, _, h in recs
            if d == "OUT" and h not in NOISE and fields(h)]
    segs, cur, prev = [], [], None
    for ts, h in cmds:
        if prev is not None and ts - prev > args.gap:
            segs.append(cur)
            cur = []
        cur.append((ts, h))
        prev = ts
    if cur:
        segs.append(cur)

    print(f"команд (без шума) {len(cmds)}, сегментов {len(segs)} "
          f"при разрыве > {args.gap} с\n")

    for i, seg in enumerate(segs):
        ts0, ts1 = seg[0][0] - t0, seg[-1][0] - t0
        b45 = collections.Counter()
        dids = collections.Counter()
        for _, h in seg:
            b4, b5, plen, handle, pl = fields(h)
            b45[(b4, b5)] += 1
            # запросы чтения: payload 22 <did> <did> ...
            if pl.startswith("22") and len(pl) >= 6:
                for j in range(2, len(pl) - 3, 4):
                    dids[pl[j:j + 4].upper()] += 1
        pairs = " ".join(f"{a:02x}/{b:02x}:{n}" for (a, b), n in b45.most_common(6))
        print(f"### сегмент {i:>2}  t={ts0:6.0f}..{ts1:6.0f} с  кадров {len(seg):>4}")
        print(f"     b4/b5: {pairs}")
        if dids:
            top = " ".join(k for k, _ in dids.most_common(12))
            print(f"     читает DID: {top}")
        else:
            print("     чтений 22 нет")

    # Кадры-кандидаты: встречаются в НАЧАЛЕ сегментов и не являются чтением.
    print("\n=== первые 6 команд каждого сегмента (тут искать переключатель) ===")
    for i, seg in enumerate(segs):
        print(f"\n-- сегмент {i} --")
        for ts, h in seg[:6]:
            b4, b5, plen, handle, pl = fields(h)
            print(f"   t={ts - t0:7.1f}  b4={b4:02x} b5={b5:02x} plen={plen:>2} "
                  f"handle={handle:02x}  payload={pl[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
