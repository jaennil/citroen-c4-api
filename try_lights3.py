"""
Последняя попытка — пробуем разные настройки CAN для BSI.
Может быть другая скорость или нужен CAN formatting.
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
    return resp.decode("ascii", errors="ignore").strip()


def try_bsi(protocol, desc):
    log.info(f"\n--- Протокол {protocol}: {desc} ---")
    cmd(f"ATSP{protocol}")
    cmd("ATCAF1")  # CAN formatting ON (ISO-TP)
    cmd("ATH1")
    cmd("ATSH752")   # BSI TX
    cmd("ATCRA652")  # BSI RX filter

    resp = cmd("10 03", 4)  # Extended diagnostic session
    log.info(f"  BSI сессия: {resp}")

    if "NO DATA" not in resp and resp and "ERROR" not in resp:
        log.info(f"  >>> BSI ОТВЕТИЛ на протоколе {protocol}!")
        return True

    # Без фильтра
    cmd("ATCRA")
    resp = cmd("10 03", 4)
    log.info(f"  BSI без фильтра: {resp}")

    if "NO DATA" not in resp and resp and "ERROR" not in resp:
        log.info(f"  >>> BSI ОТВЕТИЛ без фильтра!")
        return True

    return False


def main():
    log.info("=== Поиск BSI на всех протоколах ===\n")

    cmd("ATZ", 3)
    cmd("ATE0")

    # Проверка
    cmd("ATSP6")
    cmd("ATSH7DF")
    cmd("ATCRA")
    cmd("ATCAF1")
    resp = cmd("010C", 3)
    log.info(f"Обороты (проверка): {resp}")

    # Пробуем BSI на разных протоколах
    protocols = [
        ("6", "CAN 11bit 500kbps"),
        ("7", "CAN 29bit 500kbps"),
        ("8", "CAN 11bit 250kbps"),
        ("9", "CAN 29bit 250kbps"),
    ]

    found = False
    for proto, desc in protocols:
        if try_bsi(proto, desc):
            found = True
            break

    if not found:
        # Пробуем с ATCAF0 (raw CAN, без ISO-TP)
        log.info("\n--- Raw CAN (без ISO-TP) ---")
        for proto in ["6", "8"]:
            cmd(f"ATSP{proto}")
            cmd("ATCAF0")
            cmd("ATH1")
            cmd("ATSH752")
            cmd("ATCRA652")
            # Сырой UDS запрос: 02 = длина, 10 03 = DiagSession Extended
            resp = cmd("02 10 03 00 00 00 00 00", 4)
            log.info(f"  Raw proto {proto}: {resp}")
            if "NO DATA" not in resp and resp:
                log.info(f"  >>> ОТВЕТ!")
                found = True
                break

    if not found:
        # Пробуем K-Line (ISO 9141/14230)
        log.info("\n--- K-Line (ISO 14230 KWP) ---")
        for proto in ["4", "5"]:
            cmd(f"ATSP{proto}")
            resp = cmd("10 83", 8)  # KWP startDiagSession
            log.info(f"  KWP proto {proto}: {resp}")
            if "NO DATA" not in resp and "ERROR" not in resp and resp and "UNABLE" not in resp:
                log.info(f"  >>> K-LINE РАБОТАЕТ!")
                found = True
                break

    if not found:
        log.info("\n>>> BSI недоступен через OBD2 с ELM327.")
        log.info("Lexia 3 использует другой канал/протокол.")

    ser.close()


if __name__ == "__main__":
    main()
