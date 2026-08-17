"""
Чтение отдельных DID с показом СЫРЫХ байт и всех вариантов расшифровки.

Нужен, когда значение параметра выглядит неправдоподобно и надо понять, в чём
дело: в множителе, в смещении, в длине поля или в порядке байт. Каждый DID
читается ОТДЕЛЬНЫМ запросом - в пачке ошибка в длине одного параметра сдвигает
разбор всех следующих, и тогда мусор не отличить от настоящей ошибки кодировки.

Что уже выяснено этим способом:
  * поле endian из базы DiagBox доверять нельзя: little-endian даёт запас хода
    27650 км и топливо 210 л при 61-литровом баке, а big-endian - правдоподобные
    620 км и 49 л. На проводе big-endian;
  * у пробега при сбросе ТО множитель в базе завышен вдесятеро;
  * у напряжения покоя (DA4D) при factor 0.01 и offset 11.0 однобайтовое поле
    даёт диапазон 11.00-13.55 В, ровно осмысленный для покоя, а двухбайтовое -
    11-666 В. Похоже, byte_length в базе неверен, но подтвердить можно только
    сырыми байтами.

Запуск:
    ./.venv/bin/python probe_raw.py DA4D DA45 D8C3
    ./.venv/bin/python probe_raw.py --suspect      # заранее подозрительные
"""

import argparse
import logging
import sys

from did_catalog import BY_DID
from lexia_proto import Lexia, parse_multi, read_multi_frame
from ru_labels import label

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Параметры, чьи значения физически невозможны - разбираться с ними в первую очередь.
SUSPECT = [0xDA4D, 0xDA45, 0xDBEC, 0xD8C5, 0xDA00, 0xDA46, 0xDA21]


def hypotheses(raw: bytes, e: dict):
    """Все разумные варианты расшифровки одних и тех же байт."""
    out = []
    f = e["factor"] or 1.0
    off = e.get("offset", 0.0)
    for ln in sorted({1, 2, e["ln"] or 1} & {1, 2, 3, 4}):
        if ln > len(raw):
            continue
        for order in ("big", "little"):
            v = int.from_bytes(raw[:ln], order)
            out.append((f"{ln}б {order:<6}", v, v * f + off))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dids", nargs="*", help="DID в hex, например DA4D")
    ap.add_argument("--suspect", action="store_true", help="взять заранее подозрительные")
    args = ap.parse_args()

    dids = SUSPECT if args.suspect else [int(x, 16) for x in args.dids]
    if not dids:
        print("Укажи DID (например DA4D) или --suspect")
        return 2

    lex = Lexia()
    if not lex.connect():
        return 1
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            log.error("Нет связи с машиной: воткнута ли Lexia в OBD, включено ли зажигание?")
            return 3
        lex.boot(verbose=False)

        for did in dids:
            e = BY_DID.get(did)
            if not e:
                print(f"\n{did:04X}: нет в каталоге")
                continue
            x = e[0]
            # длину запрашиваем с запасом, чтобы увидеть и лишние байты
            payload, _ = lex.transact(read_multi_frame([did]))
            got = parse_multi(payload, {did: 8})
            raw = got.get(did, b"")
            print(f"\n{did:04X}  {label(did, x['name'])}")
            print(f"   каталог: ln={x['ln']} factor={x['factor']:.6g} "
                  f"offset={x.get('offset', 0)} endian={x.get('endian', '?')} unit={x['unit']!r}")
            if not raw:
                print("   ответа нет")
                continue
            print(f"   СЫРЫЕ БАЙТЫ: {raw.hex(' ')}")
            for how, num, val in hypotheses(raw, x):
                print(f"      {how} сырое={num:<8} -> {val:,.3f} {x['unit']}")
    finally:
        lex.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
