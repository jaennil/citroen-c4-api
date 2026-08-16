"""
Пробуем разные CAN протоколы и OBD запросы чтобы найти рабочий.
"""

import serial
import time
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
BAUD = 38400


def send_cmd(ser, cmd, timeout=3):
    ser.timeout = timeout
    ser.flushInput()
    ser.write(f"{cmd}\r".encode())
    time.sleep(0.5)
    resp = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ser.in_waiting:
            resp += ser.read(ser.in_waiting)
            time.sleep(0.05)
        else:
            time.sleep(0.1)
        if b">" in resp:
            break
    return resp.decode("ascii", errors="ignore").strip().replace(">", "").strip()


def main():
    ser = serial.Serial(PORT, BAUD, timeout=3)

    log.info("Сброс...")
    send_cmd(ser, "ATZ", 3)
    send_cmd(ser, "ATE0", 2)

    log.info("\n=== Проверяем зажигание ===")
    resp = send_cmd(ser, "ATRV")
    log.info(f"Напряжение: {resp}")

    # Автоматический поиск протокола
    log.info("\n=== Авто-поиск протокола (ATSP0) ===")
    send_cmd(ser, "ATSP0", 2)
    resp = send_cmd(ser, "0100", 10)  # запрос поддерживаемых PIDs
    log.info(f"0100 ответ: {resp}")
    resp = send_cmd(ser, "ATDP")
    log.info(f"Найденный протокол: {resp}")

    # Пробуем каждый протокол
    protocols = {
        "1": "SAE J1850 PWM",
        "2": "SAE J1850 VPW",
        "3": "ISO 9141-2",
        "4": "ISO 14230-4 KWP (5 baud init)",
        "5": "ISO 14230-4 KWP (fast init)",
        "6": "ISO 15765-4 CAN (11bit, 500kbps)",
        "7": "ISO 15765-4 CAN (29bit, 500kbps)",
        "8": "ISO 15765-4 CAN (11bit, 250kbps)",
        "9": "ISO 15765-4 CAN (29bit, 250kbps)",
    }

    log.info("\n=== Пробуем все протоколы ===")
    for num, name in protocols.items():
        send_cmd(ser, f"ATSP{num}", 2)
        resp = send_cmd(ser, "0100", 8)
        status = "ОТВЕТ" if resp and "ERROR" not in resp.upper() and "UNABLE" not in resp.upper() and "NO DATA" not in resp.upper() else "тишина"
        log.info(f"  Протокол {num} ({name}): {status} | {resp[:60] if resp else '-'}")
        if status == "ОТВЕТ":
            log.info(f"  >>> РАБОТАЕТ!")

    # Пробуем ATMA на рабочем протоколе
    log.info("\n=== Пробуем мониторинг на авто-протоколе ===")
    send_cmd(ser, "ATSP0", 2)
    send_cmd(ser, "ATH1", 1)
    send_cmd(ser, "ATCAF0", 1)
    ser.write(b"ATMA\r")
    ser.timeout = 0.5

    log.info("Слушаем 10 сек...")
    lines = []
    start = time.time()
    while time.time() - start < 10:
        data = ser.readline()
        if data:
            line = data.decode("ascii", errors="ignore").strip()
            if line and line != ">" and len(line) > 2:
                lines.append(line)
                if len(lines) <= 10:
                    log.info(f"  {line}")

    ser.write(b"\r")
    time.sleep(0.5)
    ser.close()

    log.info(f"\nИтого: {len(lines)} CAN-сообщений за 10 сек")


if __name__ == "__main__":
    main()
