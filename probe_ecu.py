"""
Поиск способа переключиться на другой ЭБУ.

Что установлено разбором полного дампа DiagBox (lexia_full.log):
  * конфигурационные блоки (команды 05, 16, ff) перед чтениями РАЗНЫХ блоков
    одинаковы - значит выбор блока не в них;
  * зато у кадра смены сессии (команда 01, полезная нагрузка 10 03) отличается
    БАЙТ 8, и он коррелирует с тем, чьи DID читаются дальше:
        байт8=0x00 -> 22D4xx, 22D5xx (подрулевой COM2008P)
        байт8=0x90 -> 22D8xx, 22DAxx (BSI)
        байт8=0x98 -> 2187, 17FF     (похоже рулевое, DAE)
  * НО наш клиент шлёт 10 03 с байтом 8 = 0x00 и всё равно читает BSI. Значит
    байт 8 - не постоянный номер блока, а индекс в таблице каналов, которую
    выставляют предыдущие кадры. Поэтому единственный надёжный способ - перебор.

Скрипт перебирает значения байта 8 в кадре смены сессии и после каждого пробует
прочитать по одному DID, характерному для разных блоков. Что ответило - то и
доступно на этом канале.

    ./.venv/bin/python probe_ecu.py
    ./.venv/bin/python probe_ecu.py --bytes 00,90,98,aa --did D401
"""

import argparse
import logging
import sys

from lexia_proto import (Lexia, checksum, describe, read_multi_frame,
                         parse_multi)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# DID-маркеры: если блок отвечает на свой маркер, значит канал ведёт к нему.
MARKERS = {
    "BSI": 0xD870,           # габаритные огни - точно есть, проверено
    "подрулевой COM2008P": 0xD401,   # свет и поворотники на подрулевом
    "рулевое DAE": 0xD100,   # наугад из диапазона рулевого
    "двигатель CMM": 0xF190,  # VIN отдаёт почти любой блок
}


def session_frame(byte8: int, payload=(0x10, 0x03)) -> bytes:
    """Кадр смены сессии с заданным байтом 8 (предполагаемый выбор канала)."""
    hdr = bytes([0x40, 0x09, 0x17, 0xC0, 0xFF, 0x01, len(payload), 0x00, byte8])
    body = hdr + b"\x00" * 15 + bytes(payload)
    return body + bytes([checksum(body)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bytes", default="00,90,98,aa,01,02,10,20,30,40,50,60,70,80",
                    help="значения байта 8 через запятую, hex")
    ap.add_argument("--did", help="проверять только один DID, hex (например D401)")
    args = ap.parse_args()

    b8s = [int(x, 16) for x in args.bytes.split(",") if x.strip()]
    markers = ({args.did: int(args.did, 16)} if args.did else MARKERS)

    lex = Lexia()
    if not lex.connect():
        return 1
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            log.error("Нет связи с машиной: Lexia в OBD? зажигание включено?")
            return 3
        lex.boot(verbose=False)
        log.info(f"Связь есть. Перебираю {len(b8s)} значений байта 8.")
        print()
        print(f"{'байт8':<7} " + " ".join(f"{n[:16]:<17}" for n in markers))
        print("-" * (8 + 18 * len(markers)))

        for b8 in b8s:
            # заново выставляем канал
            lex.transact(session_frame(b8))
            cells = []
            for name, did in markers.items():
                payload, _ = lex.transact(read_multi_frame([did]))
                got = parse_multi(payload, {did: 4})
                if got:
                    cells.append(f"{'ОТВЕТ ' + got[did].hex():<17}")
                elif payload and payload[0] == 0x7F:
                    code = payload[2] if len(payload) > 2 else 0
                    cells.append(f"{'7F ' + f'{code:02X}':<17}")
                else:
                    cells.append(f"{'-':<17}")
            print(f"0x{b8:02X}    " + " ".join(cells))
            # возвращаемся в известное рабочее состояние
            lex.boot(verbose=False)
    finally:
        lex.disconnect()
    print()
    print("Читается тот блок, чей маркер ответил. 7F 31 - канал ведёт к блоку,")
    print("но такого DID у него нет; прочерк - канал молчит совсем.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
