"""
Простой сниффер — просто дампит сырые данные с CAN через ELM327.
"""

import serial
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
BAUD = 38400
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 30


def send_cmd(ser, cmd, timeout=2):
    ser.timeout = timeout
    ser.write(f"{cmd}\r".encode())
    time.sleep(0.3)
    resp = b""
    while ser.in_waiting:
        resp += ser.read(ser.in_waiting)
        time.sleep(0.05)
    return resp.decode("ascii", errors="ignore").strip()


def main():
    log.info(f"Подключаемся к {PORT}...")
    ser = serial.Serial(PORT, BAUD, timeout=1)

    # Инициализация
    for cmd in ["ATZ", "ATE0", "ATL1", "ATS1", "ATH1", "ATSP6", "ATCAF0"]:
        resp = send_cmd(ser, cmd, 2)
        log.info(f"  {cmd} -> {resp}")

    log.info(f"\nМониторинг {DURATION} сек. ВКЛЮЧАЙ/ВЫКЛЮЧАЙ ФАРЫ!")
    log.info("=" * 50)

    # Запуск мониторинга
    ser.write(b"ATMA\r")
    ser.timeout = 0.1

    start = time.time()
    lines = []

    while time.time() - start < DURATION:
        data = ser.readline()
        if data:
            line = data.decode("ascii", errors="ignore").strip()
            if line and line != ">" and "SEARCHING" not in line:
                lines.append((time.time(), line))

    # Стоп
    ser.write(b"\r")
    time.sleep(0.5)
    ser.close()

    # Сохраняем
    with open("can_raw.log", "w") as f:
        for ts, line in lines:
            f.write(f"{ts},{line}\n")

    log.info(f"\nПринято {len(lines)} строк")

    # Показываем уникальные ID
    ids = set()
    for _, line in lines:
        if len(line) >= 3:
            ids.add(line[:3])

    log.info(f"Уникальных ID: {len(ids)}")
    log.info(f"ID: {sorted(ids)}")

    # Показываем первые 20 строк
    log.info("\nПервые 20 строк:")
    for _, line in lines[:20]:
        log.info(f"  {line}")

    log.info(f"\nЛог сохранён в can_raw.log")


if __name__ == "__main__":
    main()
