"""
Попытка управлять фарами через UDS диагностику BSI.
BSI TX=0x752 RX=0x652
PROJECTEURS TX=0x6B7 RX=0x697

DiagBox делает именно это — шлёт UDS команды на BSI через OBD2 порт.
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
    return decoded


def try_ecu(name, tx_id, rx_id):
    """Попробовать связаться с ЭБУ и открыть диагностическую сессию."""
    log.info(f"\n{'='*50}")
    log.info(f"  {name}: TX=0x{tx_id} RX=0x{rx_id}")
    log.info(f"{'='*50}")

    cmd(f"ATSH{tx_id}")
    cmd(f"ATCRA{rx_id}")  # фильтр на ответы от этого ЭБУ

    # Открыть расширенную диагностическую сессию
    resp = cmd("10 03", 3)
    log.info(f"  Сессия (10 03): {resp}")

    if "7F" in resp:
        log.info(f"  Отказ: {resp}")
        return False
    if "50 03" in resp:
        log.info(f"  >>> СЕССИЯ ОТКРЫТА!")

        # Читаем информацию об ЭБУ
        resp = cmd("22 F1 90", 3)  # VIN
        log.info(f"  VIN (22 F190): {resp}")

        # Попробуем IOControl для фар
        # UDS 0x2F = InputOutputControlByIdentifier
        # DID зависит от конкретного ЭБУ

        # Пробуем разные DID для управления выходами
        for did in ["20 01", "20 02", "20 03", "F0 01", "F0 02", "D0 01", "D0 02"]:
            # 0x2F DID 03=ShortTermAdjustment 01=ON
            resp = cmd(f"2F {did} 03 FF", 2)
            if "6F" in resp:  # положительный ответ на 2F = 6F
                log.info(f"  >>> IOControl {did}: ОТВЕТ! {resp}")
                time.sleep(2)
                # Вернуть контроль ЭБУ
                cmd(f"2F {did} 00", 2)  # returnControlToECU
                return True
            elif "7F" in resp:
                log.info(f"  IOControl {did}: отказ")
            else:
                log.info(f"  IOControl {did}: {resp[:40]}")

        return True
    else:
        log.info(f"  Нет ответа или ошибка")
        return False


def main():
    log.info("=== UDS диагностика: поиск управления фарами ===\n")

    cmd("ATZ", 3)
    cmd("ATE0")
    cmd("ATH1")
    cmd("ATSP6")

    # Проверяем CAN
    cmd("ATSH7DF")
    cmd("ATCRA")
    resp = cmd("010C", 3)
    log.info(f"Обороты (проверка CAN): {resp}")

    # Пробуем BSI
    try_ecu("BSI", "752", "652")

    # Пробуем PROJECTEURS (блок фар)
    try_ecu("PROJECTEURS", "6B7", "697")

    # Пробуем HDC (подрулевой)
    try_ecu("HDC (подрулевой)", "742", "642")

    # Пробуем COMBINE (приборка)
    try_ecu("COMBINE (приборка)", "75F", "65F")

    ser.close()
    log.info("\nГотово!")


if __name__ == "__main__":
    main()
