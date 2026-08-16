"""
Полный обход всех DID из каталога: что реально существует на ЭТОЙ машине.

Каталог собран из базы DiagBox для платформы, а не для конкретного автомобиля.
Урок дальнего света: D82B в базе есть, а на машине 7F 22 31. Поэтому единственный
способ узнать правду - опросить всё и записать, что ответило.

Читает пачками по 10 DID; если пачка отклонена (в ней есть несуществующий DID),
откатывается на поштучное чтение, чтобы найти живые. Чтение пассивно и безопасно,
двигатель может работать.

Запуск:  ./.venv/bin/python sweep.py [--out live_dids.py]
"""

import argparse
import logging
import sys
import time

from did_catalog import BY_DID, CATALOG
from lexia_proto import (MAX_DIDS_PER_REQUEST, Lexia, parse_multi,
                         read_did, read_multi_frame)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def did_length(did: int) -> int:
    e = BY_DID.get(did)
    return max((x["sb"] - 4) + x["ln"] for x in e) if e else 1


def name_of(did: int) -> str:
    e = BY_DID.get(did)
    return e[0]["name"] if e else f"DID_{did:04X}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="live_dids.py", help="куда записать список живых")
    args = ap.parse_args()

    dids = sorted(BY_DID)
    lengths = {d: did_length(d) for d in dids}
    log.info(f"В каталоге {len(CATALOG)} параметров на {len(dids)} уникальных DID.")

    lex = Lexia()
    if not lex.connect():
        return 1
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            log.error("Рукопожатие не прошло: Lexia в OBD? зажигание?")
            return 3
        lex.boot(verbose=False)

        live, dead = {}, []
        batches = single = 0
        t0 = time.time()
        for i in range(0, len(dids), MAX_DIDS_PER_REQUEST):
            chunk = dids[i:i + MAX_DIDS_PER_REQUEST]
            payload, _ = lex.transact(read_multi_frame(chunk))
            batches += 1
            got = parse_multi(payload, lengths)
            if got and len(got) == len(chunk):
                live.update(got)
            else:
                # в пачке есть отсутствующий DID - разбираем поштучно
                for d in chunk:
                    p, _ = lex.transact(read_did(d & 0xFF) if (d >> 8) == 0xD8
                                        else read_multi_frame([d]))
                    single += 1
                    g = parse_multi(p, lengths)
                    if g:
                        live.update(g)
                    else:
                        dead.append(d)
            if batches % 10 == 0:
                log.info(f"  ... {i + len(chunk)}/{len(dids)}, живых {len(live)}")
        dt = time.time() - t0

        print()
        print(f"обход занял {dt:.1f} с ({batches} пачек + {single} одиночных)")
        print(f"ЖИВЫХ DID:   {len(live)} из {len(dids)}")
        print(f"отсутствуют: {len(dead)}")

        with open(args.out, "w") as f:
            f.write('"""Список DID, реально ответивших на этой машине.\n\n'
                    f'Обход {time.strftime("%Y-%m-%d")}: живых {len(live)} из {len(dids)} '
                    f'по каталогу платформы.\n"""\n\nLIVE = [\n')
            for d in sorted(live):
                f.write(f"    (0x{d:04X}, {name_of(d)!r}),\n")
            f.write("]\n")
        log.info(f"Список живых записан в {args.out}")

        print("\nпримеры живых параметров:")
        for d in sorted(live)[:25]:
            print(f"  {d:04X}  {name_of(d)[:58]:<58} {live[d].hex()}")
    finally:
        lex.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
