"""
Переключение на произвольный ЭБУ и опрос, кто там живой.

Как это устроено, установлено разбором дампа переключения блоков в DiagBox
(switch.log, analyze_switch.py). Выбор блока НЕ передаётся отдельной командой -
он лежит в таблице настройки протокола, кадр b5=16, в записях вида

    50 <индекс> <lo> <hi>      # 16-битное значение, младший байт первым
    50 01 52 07  ->  P01 = 0x0752   адрес запроса на CAN
    50 02 52 06  ->  P02 = 0x0652   адрес ответа

0x752/0x652 - диагностическая пара BSI из PSA DiagOnCan, отсюда и уверенность,
что прочитано верно. Таблица не влезает в один USB-кадр и шлётся двумя.

Полная последовательность входа - пять кадров: сброс сессии, дескриптор,
таблица (два кадра), затем StartCommunication. Кадры берутся ДОСЛОВНО из
ecu_entry.py, а не собираются заново: попытки собирать их самому дважды давали
ложный вывод об успехе, потому что BSI продолжала отвечать на запросы, которые
я считал адресованными другому блоку.

Здесь только чтение. Актуаторы не трогаются.

    ./.venv/bin/python ecu.py --all           # обойти все известные блоки
    ./.venv/bin/python ecu.py --tx 6A8        # войти в один и опросить
    ./.venv/bin/python ecu.py --tx 6A8 --lid 80,C0,01
"""

import argparse
import logging
import sys

from ecu_entry import ENTRY, NAMES
from lexia_proto import Lexia, describe, read_frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Чем опрашивать незнакомый блок. Порядок важен: сперва UDS, потом KWP - у
# KWP-блоков сервис 22 отвечает отказом, а не молчанием, и это само по себе
# полезный признак.
IDENT_UDS = [0xF080, 0xF190, 0xF18C]   # версия ПО, VIN, серийный номер
IDENT_KWP = [0x80, 0xFE, 0x01, 0xC0]   # идентификация, состояние, блоки данных


def groups(frames):
    """Разбить кадры на команды: команда кончается кадром с битом 0x40 в байте 3."""
    out, cur = [], []
    for h in frames:
        b = bytes.fromhex(h)
        cur.append(b)
        if b[3] & 0x40:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def enter(lex, tx, rx, verbose=False):
    """Переключить интерфейс на блок tx/rx. Возвращает ответ на последнюю команду."""
    key = (tx, rx)
    if key not in ENTRY:
        raise KeyError(f"нет записанной последовательности для 0x{tx:03X}")
    last = None
    for i, g in enumerate(groups(ENTRY[key]), 1):
        payload, raw = lex.transact_frames(g)
        last = payload
        if verbose:
            what = f"{len(g)} кадр(ов), {sum(len(x) for x in g)} байт"
            log.info(f"   вход {i}: {what} -> "
                     f"{describe(payload) if payload else (raw.hex()[:32] or 'нет ответа')}")
    return last


def ask(lex, payload: bytes):
    """Один произвольный запрос к текущему блоку."""
    pl, raw = lex.transact(read_frame(payload))
    return pl


def ascii_of(b: bytes) -> str:
    s = "".join(chr(x) if 32 <= x < 127 else "." for x in b)
    return s if any(c.isalnum() for c in s) else ""


def probe_one(lex, tx, rx, uds=None, kwp=None, verbose=False):
    """Войти в блок и попробовать его опознать. Возвращает список строк-находок."""
    found = []
    try:
        enter(lex, tx, rx, verbose=verbose)
    except Exception as e:
        return [f"вход не удался: {type(e).__name__}: {e}"]

    for did in (uds if uds is not None else IDENT_UDS):
        pl = ask(lex, bytes([0x22, (did >> 8) & 0xFF, did & 0xFF]))
        if pl and pl[0] == 0x62:
            data = pl[3:]
            found.append(f"22 {did:04X} -> {data.hex()} {ascii_of(data)}".rstrip())
        elif pl and pl[0] == 0x7F:
            found.append(f"22 {did:04X} -> отказ NRC {pl[2]:02X}")

    for lid in (kwp if kwp is not None else IDENT_KWP):
        pl = ask(lex, bytes([0x21, lid]))
        if pl and pl[0] == 0x61:
            data = pl[2:]
            found.append(f"21 {lid:02X} -> {data.hex()[:60]} {ascii_of(data)}".rstrip())
        elif pl and pl[0] == 0x7F:
            found.append(f"21 {lid:02X} -> отказ NRC {pl[2]:02X}")
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="обойти все известные блоки")
    ap.add_argument("--tx", help="адрес одного блока, hex (например 6A8)")
    ap.add_argument("--did", help="какие DID читать через 22, hex через запятую")
    ap.add_argument("--lid", help="какие ID читать через 21, hex через запятую")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not args.all and not args.tx:
        ap.error("укажи --all или --tx")

    targets = sorted(ENTRY)
    if args.tx:
        want = int(args.tx, 16)
        targets = [k for k in targets if k[0] == want]
        if not targets:
            print(f"нет последовательности для 0x{want:03X}. Известны: "
                  + " ".join(f"{t:03X}" for t, _ in sorted(ENTRY)))
            return 2

    uds = [int(x, 16) for x in args.did.split(",")] if args.did else None
    kwp = [int(x, 16) for x in args.lid.split(",")] if args.lid else None

    lex = Lexia()
    if not lex.connect():
        return 1
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            log.error("нет связи с машиной: Lexia в OBD? зажигание включено?")
            return 3
        log.info(f"связь есть, обхожу {len(targets)} блок(ов)")
        alive = 0
        for tx, rx in targets:
            name = NAMES.get(tx, "")
            print(f"\n=== 0x{tx:03X}/0x{rx:03X} {name}")
            try:
                res = probe_one(lex, tx, rx, uds, kwp, verbose=args.verbose)
            except Exception as e:
                print(f"    сорвалось: {type(e).__name__}: {e}")
                continue
            if not res:
                print("    молчит")
                continue
            alive += 1
            for line in res:
                print(f"    {line}")
        print(f"\nответили: {alive} из {len(targets)}")
    finally:
        lex.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
