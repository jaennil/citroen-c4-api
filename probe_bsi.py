"""
Пробник BSI через Lexia 3: существует ли DID дальнего света на ЭТОЙ машине.

Отвечает на главный открытый вопрос. В дампе базы DiagBox MP_COMMANDE_FEUX_DE_ROUTE
(22D82B) встречается только в группах MESUREPARAMETRE2E_VARB* - вариант с AFS и
раздельными билинзами. В базовой MESUREPARAMETRE2E дальнего нет вообще. Если у нас
базовый вариант, чтение вернёт 7F 22 31 requestOutOfRange, и весь маршрут через 2F
закрыт независимо от скорости.

Порядок намеренный: сначала контрольное чтение заведомо рабочего DID, чтобы отличить
"DID не существует" от "не работает вся цепочка".

Запуск:  ./.venv/bin/python probe_bsi.py
         ./.venv/bin/python probe_bsi.py --actuate   (ещё и подать 2F, свет ~3с)
(root не нужен, права выдаёт 70-psa-diag.rules)

Зажигание ON. Мотор можно и завести - "мотор должен быть заглушен" оказалось
соглашением проекта, ничем не подтверждённым.
"""

import argparse
import logging
import sys
import time

from lexia_proto import Lexia, actuate, describe, link_status, read_did

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Что читаем. confirmed - щёлкали физически, diagbox - только из базы.
PROBE = [
    (0x71, "поворотник правый", "confirmed"),
    (0x70, "габариты", "confirmed"),
    (0x2B, "ДАЛЬНИЙ СВЕТ", "diagbox"),
    (0x29, "ближний левый", "diagbox"),
    (0x2A, "ближний правый", "diagbox"),
    (0x26, "доступность автопереключения", "diagbox"),
    (0x2C, "ПТФ передние", "diagbox"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actuate", action="store_true",
                    help="после чтения подать 2F на дальний (свет загорится)")
    args = ap.parse_args()

    lex = Lexia()
    if not lex.connect():
        return 1

    try:
        lex.drain()
        log.info("Рукопожатие с Lexia (чтение версий прошивки)...")
        if not lex.device_boot():
            log.error("Lexia отвечает по USB, но машины не видит. Дальше идти бессмысленно:")
            log.error("  - воткнут ли разъём Lexia в OBD (не только USB в ноутбук)?")
            log.error("  - включено ли зажигание?")
            return 3

        log.info("Стартовая процедура DiagBox (10 01 / 10 03 + конфигурация)...")
        lex.boot()

        print()
        print(f"{'DID':<8} {'что':<30} {'источник':<10} результат")
        print("-" * 88)

        results = {}
        for did, name, src in PROBE:
            payload, raw = lex.transact(read_did(did))
            verdict = describe(payload)
            results[did] = payload
            print(f"D8{did:02X}     {name:<30} {src:<10} {verdict}")
            time.sleep(0.15)

        print()
        hb = results.get(0x2B)
        if hb and hb[0] == 0x62:
            log.info("D82B СУЩЕСТВУЕТ на этой BSI - маршрут через 2F жив, можно пробовать --actuate.")
        elif hb and hb[0] == 0x7F and len(hb) > 2 and hb[2] == 0x31:
            log.warning("D82B НЕ существует (requestOutOfRange) - базовый вариант BSI.")
            log.warning("Маршрут дальнего через 2F закрыт. Остаются MITM на LS.CAR или реле.")
        else:
            log.warning("Однозначного ответа по D82B нет - смотри строку выше.")

        ctrl = results.get(0x71)
        if not (ctrl and ctrl[0] == 0x62):
            log.error("Контрольный DID D871 тоже не прочитался - проблема в цепочке/сессии,")
            log.error("а не в наличии дальнего. Результату по D82B верить нельзя.")

        if args.actuate:
            if not (hb and hb[0] == 0x62):
                log.error("Не подаю 2F: D82B не читается. Сначала разберись с чтением.")
                return 2
            print()
            log.info("Подаю 2F D8 2B - дальний должен загореться примерно на 3 секунды...")
            payload, raw = lex.transact(actuate(0x2B))
            log.info(f"  ответ: {describe(payload)}")
            if payload and payload[0] == 0x7F:
                code = payload[2] if len(payload) > 2 else 0
                if code == 0x22:
                    log.warning("conditionsNotCorrect. Ровно это же в дампе получил и штатный")
                    log.warning("DiagBox на стоп-сигнал - там этому предшествовали 28 с тишины")
                    log.warning("по keep-alive. Попробуй сразу после init, без пауз.")
                elif code == 0x88:
                    log.warning("vehicleSpeedTooHigh - вот он, скоростной гейт.")
    finally:
        lex.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
