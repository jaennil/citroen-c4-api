"""
Управление светом Citroen C4 через Lexia 3 из Linux.

Работает поверх lexia_proto, то есть с полным рукопожатием устройства - без него
BSI не отвечает (см. CLAUDE.md). Старые скрипты police_mode.py / police2.py /
blink_test.py / high_beam.py написаны до того, как рукопожатие было найдено,
и работать не будут.

Список актуаторов - только те, что реально существуют на этой машине; проверено
чтением 22 D8 xx. Головного света в BSI нет: D829/D82A/D82B/D82C отвечают
7F 22 31 requestOutOfRange, потому что вариант BSI базовый, без AFS.

BSI держит выход около 3 секунд и команды выключения не найдено, поэтому
удержание сделано пересылкой.

Запуск:
    ./.venv/bin/python lights.py                 список актуаторов
    ./.venv/bin/python lights.py side            разово
    ./.venv/bin/python lights.py side --hold 10  держать 10 секунд
    ./.venv/bin/python lights.py police          лево-право попеременно
"""

import argparse
import logging
import sys
import time

from lexia_proto import Lexia, actuate, describe, read_did

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

LATCH = 3.0  # сколько BSI держит выход

ACTUATORS = {
    "side":    (0x70, "габариты"),
    "right":   (0x71, "поворотник правый"),
    "left":    (0x72, "поворотник левый"),
    "rearfog": (0x73, "задний противотуманный"),
    "reverse": (0x74, "задний ход"),
    "brake":   (0x75, "стоп-сигнал"),
    "hazled":  (0x77, "светодиод в кнопке аварийки"),
}


def connect() -> Lexia:
    lex = Lexia()
    if not lex.connect():
        sys.exit(1)
    lex.drain()
    if not lex.device_boot(verbose=False):
        log.error("Рукопожатие не прошло. Воткнута ли Lexia в OBD и включено ли зажигание?")
        lex.disconnect()
        sys.exit(2)
    lex.boot(verbose=False)
    log.info("Связь с BSI установлена.")
    return lex


def fire(lex: Lexia, did: int, title: str):
    payload, _ = lex.transact(actuate(did))
    ok = bool(payload) and payload[0] == 0x6F
    log.info(f"{title}: {describe(payload)}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="?", help="актуатор или режим police")
    ap.add_argument("--hold", type=float, default=0, help="держать N секунд")
    args = ap.parse_args()

    if not args.what:
        print("Доступные актуаторы (проверены на этой машине):")
        for k, (did, title) in ACTUATORS.items():
            print(f"  {k:<9} D8{did:02X}  {title}")
        print("\nРежимы: police (лево-право)")
        print("Головного света нет: BSI базового варианта им не управляет.")
        return 0

    lex = connect()
    try:
        if args.what == "police":
            log.info("Police mode. Ctrl+C - стоп.")
            try:
                while True:
                    fire(lex, 0x71, "правый")
                    time.sleep(0.4)
                    fire(lex, 0x72, "левый")
                    time.sleep(0.4)
            except KeyboardInterrupt:
                log.info("Стоп, жду снятия защёлки BSI...")
                time.sleep(LATCH)
            return 0

        if args.what not in ACTUATORS:
            log.error(f"Неизвестно: {args.what}. Запусти без аргументов для списка.")
            return 2

        did, title = ACTUATORS[args.what]
        payload, _ = lex.transact(read_did(did))
        if payload and payload[0] == 0x7F:
            log.error(f"{title}: DID отсутствует на этой машине - {describe(payload)}")
            return 3

        if args.hold:
            log.info(f"Держу {title} {args.hold:.0f}с, переотправкой...")
            end = time.time() + args.hold
            try:
                # Пока актуатор активен, BSI отвечает 7F 2F 22 "тест уже идёт" и
                # защёлку это не продлевает. Поэтому шлём чуть позже её истечения.
                while time.time() < end:
                    fire(lex, did, title)
                    time.sleep(LATCH + 0.25)
            except KeyboardInterrupt:
                pass
            log.info("Готово, жду снятия защёлки...")
            time.sleep(LATCH)
        else:
            fire(lex, did, title)
            log.info(f"Должно гореть ~{LATCH:.0f}с.")
    finally:
        lex.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
