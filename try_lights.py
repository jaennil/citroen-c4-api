"""
Попытка управлять фарами через OBD2 порт.
Пробуем отправить CAN-сообщения на ID 0x128 (CDE_COMBINE_SIGNALISATION).
Может не сработать если OBD2 не на той шине.
"""

import serial
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PORT = "/dev/ttyACM0"
ser = serial.Serial(PORT, 38400, timeout=3)


def cmd(c, t=3):
    ser.flushInput()
    time.sleep(0.05)
    ser.flushInput()
    ser.write(f"{c}\r".encode())
    resp = b""
    deadline = time.time() + t
    while time.time() < deadline:
        if ser.in_waiting:
            resp += ser.read(ser.in_waiting)
            time.sleep(0.02)
        else:
            if b">" in resp:
                break
            time.sleep(0.05)
    decoded = resp.decode("ascii", errors="ignore").strip()
    log.info(f"  {c} -> {decoded[:80]}")
    return decoded


def main():
    log.info("=== Тест управления фарами ===\n")

    # Инициализация
    cmd("ATZ", 3)
    cmd("ATE0")
    cmd("ATH1")
    cmd("ATSP6")
    cmd("ATCAF0")  # сырой CAN формат

    # Проверяем что CAN работает
    log.info("\nПроверка CAN (читаем обороты)...")
    cmd("ATSH7DF")  # стандартный OBD broadcast ID
    resp = cmd("010C", 3)
    log.info(f"Обороты: {resp}\n")

    log.info("=" * 50)
    log.info("Пробуем отправить на CAN ID 0x128")
    log.info("(CDE_COMBINE_SIGNALISATION — индикаторы фар)")
    log.info("Смотри на приборку — может мигнут иконки фар!")
    log.info("=" * 50)

    # Устанавливаем CAN ID = 0x128 (CDE_COMBINE_SIGNALISATION)
    cmd("ATSH128")

    # Бит 38 = ближний (байт 4, бит 6 = 0x40)
    # Бит 37 = дальний (байт 4, бит 5 = 0x20)
    # Бит 34 = правый поворотник (байт 4, бит 2 = 0x04)
    # Бит 33 = левый поворотник (байт 4, бит 1 = 0x02)

    log.info("\n>>> Попытка 1: Включить ближний (бит 38)")
    resp = cmd("00 00 00 00 40 00 00 00", 2)

    time.sleep(2)

    log.info("\n>>> Попытка 2: Выключить")
    resp = cmd("00 00 00 00 00 00 00 00", 2)

    time.sleep(1)

    log.info("\n>>> Попытка 3: Оба поворотника (аварийка)")
    resp = cmd("00 00 00 00 06 00 00 00", 2)

    time.sleep(2)

    log.info("\n>>> Попытка 4: Выключить")
    resp = cmd("00 00 00 00 00 00 00 00", 2)

    time.sleep(1)

    # Теперь пробуем через BSI диагностику (UDS)
    log.info("\n" + "=" * 50)
    log.info("Пробуем через диагностику BSI (UDS)")
    log.info("=" * 50)

    # BSI diagnostic CAN ID (типичные для PSA)
    for bsi_id in ["6B1", "7A1", "762"]:
        log.info(f"\n>>> BSI ID: 0x{bsi_id}")
        cmd(f"ATSH{bsi_id}")

        # Открыть диагностическую сессию
        resp = cmd("10 03", 3)  # DiagnosticSessionControl, extendedSession
        log.info(f"  Сессия: {resp}")

        if "ERROR" not in resp.upper() and "NO DATA" not in resp.upper():
            log.info(f"  BSI ответил на 0x{bsi_id}!")

            # Попробуем IOControl (управление выходами)
            # 0x30 = InputOutputControlByIdentifier
            resp = cmd("30 F0 01 03", 3)  # попытка управления актуатором
            log.info(f"  IOControl: {resp}")
            break

    ser.close()
    log.info("\nГотово! Что-нибудь произошло с фарами/приборкой?")


if __name__ == "__main__":
    main()
