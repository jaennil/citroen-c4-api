"""
Опрос ВСЕХ параметров ВСЕХ блоков машины.

Собирает вместе три вещи, добытые по отдельности:
  * ecu_entry.py  - дословные последовательности переключения на блок;
  * ecu_catalog.py - что у какого блока можно прочитать (1393 параметра);
  * lexia_proto.py - сам протокол интерфейса.

Две модели доступа, и опрос устроен под каждую по-своему:

  UDS-блоки (BSI, BSM, подрулевой, щиток...). Запрос 22 <DID>, свой DID на
  каждый параметр. Слать по одному расточительно, поэтому DID собираются в
  пачки. Пределов два, оба измерены на машине: не более 10 DID в запросе и не
  более 30 байт в ответе - пачка сверх этого отклоняется ЦЕЛИКОМ, а не
  усекается, поэтому жадничать нельзя. Данные параметра лежат со смещения
  sb-4 внутри данных своего DID.

  KWP-блоки (двигатель, ABS, насос). Запрос 21 <LID> [аргументы], и один
  запрос возвращает целый блок, из которого параметры берутся по смещению
  sb-1 в ответе. Батчить нечего и не нужно: у двигателя 189 параметров всего
  за 14 запросов.

Только чтение: актуаторные группы в каталог не попали, служебные запросы вроде
2187xx здесь не посылаются.

    ./.venv/bin/python poll_all.py                 # обойти все блоки
    ./.venv/bin/python poll_all.py --tx 6A8        # только двигатель
    ./.venv/bin/python poll_all.py --tx 6A8 --sqlite car.db
"""

import argparse
import collections
import logging
import sys
import time

import usb.core

from ecu import enter
from ecu_catalog import ECUS
from lexia_proto import Lexia, parse_multi, plan_batches, read_frame, read_multi_frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("poll_all")

# Заглушки "данных нет". Беззнаковые - все единицы и на единицу меньше;
# знаковые - границы диапазона. Без этой проверки в графики попадали 65.5 В
# (0xFFFE) и 3276.7 град/с (0x7FFF как знаковое).
SENTINEL = {1: (0xFF, 0xFE), 2: (0xFFFF, 0xFFFE, 0x7FFF, 0x8000),
            4: (0xFFFFFFFF, 0xFFFFFFFE)}


def decode(p, data: bytes):
    """Значение параметра из его куска данных. None - нет данных или заглушка."""
    # У UDS данные параметра лежат внутри куска своего DID, где ответ уже
    # разобран и префикс 62 <DID> снят - отсюда sb-4. У KWP разбирается ответ
    # целиком, вместе с префиксом 61 <LID> - отсюда sb-1.
    off = p["sb"] - 4 if p["req"].startswith("22") else p["sb"] - 1
    if off < 0 or off + p["ln"] > len(data):
        return None
    raw = int.from_bytes(data[off:off + p["ln"]], "big")   # endian из базы врёт
    if raw in SENTINEL.get(p["ln"], ()):
        return None
    if p["mask"]:
        raw = (raw >> (p["shift"] or 0)) & p["mask"]
    return raw * p["factor"] + p["offset"]


def uds_payload(dids):
    """Нагрузка запроса на несколько DID: 22 <DID><DID>..."""
    return bytes([0x22]) + b"".join(bytes([(d >> 8) & 0xFF, d & 0xFF]) for d in dids)


def uds_plan(params):
    """DID -> сколько байт данных нужно, чтобы накрыть все его параметры."""
    need = collections.defaultdict(int)
    by_did = collections.defaultdict(list)
    for p in params:
        if not (p["req"].startswith("22") and len(p["req"]) == 6):
            continue
        did = int(p["req"][2:], 16)
        by_did[did].append(p)
        need[did] = max(need[did], (p["sb"] - 4) + p["ln"])
    return by_did, dict(need)


def poll_ecu(lex, tx, rx, info, verbose=False):
    """Прочитать всё, что можно, у одного блока. Возвращает {имя: (значение, ед.)}."""
    out, refused, silent = {}, 0, 0
    params = info["params"]
    by_did, need = uds_plan(params)

    # UDS - пачками по DID
    if by_did:
        for chunk in plan_batches(sorted(by_did), need):
            try:
                payload, _ = lex.read(uds_payload(chunk))
            except Exception as e:
                log.warning(f"  0x{tx:03X}: запрос сорвался ({type(e).__name__})")
                continue
            if payload and payload[0] == 0x7F:
                refused += len(chunk)
                continue
            got = parse_multi(payload, need) if payload else {}
            if not got:
                silent += len(chunk)
            for did, data in got.items():
                for p in by_did[did]:
                    v = decode(p, data)
                    if v is not None:
                        out[p["name"]] = (v, p["unit"])

    # всё прочее - как есть, по одному запросу (KWP и длинные UDS)
    rest = collections.defaultdict(list)
    for p in params:
        if p["req"].startswith("22") and len(p["req"]) == 6:
            continue
        rest[p["req"]].append(p)
    for req, ps in sorted(rest.items()):
        try:
            payload, _ = lex.read(bytes.fromhex(req))
        except Exception as e:
            log.warning(f"  0x{tx:03X}: {req} сорвался ({type(e).__name__})")
            continue
        if not payload:
            silent += len(ps)
            continue
        if payload[0] == 0x7F:
            refused += len(ps)
            continue
        for p in ps:
            v = decode(p, payload)
            if v is not None:
                out[p["name"]] = (v, p["unit"])
    if verbose:
        log.info(f"  прочитано {len(out)}, отказ {refused}, молчание {silent}")
    return out, refused, silent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx", help="один блок, hex (например 6A8)")
    ap.add_argument("--sqlite", help="писать в базу телеметрии")
    ap.add_argument("--limit", type=int, default=0, help="показать не более N значений на блок")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    targets = sorted(ECUS)
    if args.tx:
        want = int(args.tx, 16)
        targets = [k for k in targets if k[0] == want]
        if not targets:
            print("известные адреса: " + " ".join(f"{t:03X}" for t, _ in sorted(ECUS)))
            return 2

    store = None
    if args.sqlite:
        from storage import Store
        store = Store(args.sqlite)

    lex = Lexia()
    if not lex.connect():
        return 1
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            log.error("нет связи с машиной: Lexia в OBD? зажигание включено?")
            return 3
        grand = 0
        for tx, rx in targets:
            info = ECUS[(tx, rx)]
            print(f"\n=== 0x{tx:03X} {info['ru']} ({info['fam']}, "
                  f"{len(info['params'])} параметров в каталоге)")
            t0 = time.time()
            try:
                enter(lex, tx, rx, verbose=args.verbose)
                vals, refused, silent = poll_ecu(lex, tx, rx, info, args.verbose)
            except usb.core.USBError as e:
                # Устройство залипло: дальше все запросы будут падать, а их
                # тысяча. Прекращаем обход, уже собранное записано по блокам.
                print(f"    интерфейс перестал отвечать ({type(e).__name__}) - "
                      f"обход прерван, собранное сохранено")
                break
            except Exception as e:
                print(f"    войти не удалось: {type(e).__name__}: {e}")
                continue
            dt = time.time() - t0
            grand += len(vals)
            print(f"    прочитано {len(vals)} за {dt:.1f} с "
                  f"(отказ {refused}, молчание {silent})")
            shown = sorted(vals.items())
            if args.limit:
                shown = shown[:args.limit]
            for name, (v, unit) in shown:
                print(f"      {name:<52} {v:>12.3f} {unit}")
            if store and vals:
                store.write([(0, f"{info['fam']}:{n}", u, v)
                             for n, (v, u) in vals.items()], ts=time.time())
        print(f"\nвсего прочитано значений: {grand}")
    finally:
        lex.disconnect()
        if store:
            store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
