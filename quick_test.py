"""
Быстрый тест: OBD запрос + мониторинг CAN 15 сек.
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
    ser.write(f"{c}\r".encode())
    time.sleep(0.5)
    resp = b""
    deadline = time.time() + t
    while time.time() < deadline:
        if ser.in_waiting:
            resp += ser.read(ser.in_waiting)
            time.sleep(0.05)
        else:
            if b">" in resp:
                break
            time.sleep(0.1)
    return resp.decode("ascii", errors="ignore").strip()

# Инициализация
log.info("Init...")
cmd("ATZ", 3)
cmd("ATE0")
cmd("ATL1")
cmd("ATH1")

# Авто-поиск протокола и OBD запрос
log.info("Авто-поиск протокола...")
cmd("ATSP0")
resp = cmd("0100", 10)
log.info(f"OBD 0100: {resp}")

resp = cmd("ATDPN")
log.info(f"Протокол: {resp}")

resp = cmd("ATRV")
log.info(f"Напряжение: {resp}")

# Читаем обороты
resp = cmd("010C", 5)
log.info(f"Обороты (010C): {resp}")

# Читаем скорость
resp = cmd("010D", 5)
log.info(f"Скорость (010D): {resp}")

# Читаем температуру
resp = cmd("0105", 5)
log.info(f"Температура (0105): {resp}")

# Мониторинг
log.info("\n=== Мониторинг CAN 15 сек ===")
log.info("ВКЛЮЧАЙ/ВЫКЛЮЧАЙ ФАРЫ!")
cmd("ATS1")
cmd("ATCAF0")
ser.write(b"ATMA\r")
ser.timeout = 0.3

lines = []
start = time.time()
while time.time() - start < 15:
    data = ser.readline()
    if data:
        line = data.decode("ascii", errors="ignore").strip()
        if line and line != ">" and len(line) > 3:
            lines.append(line)
            if len(lines) <= 30:
                log.info(f"  {line}")

ser.write(b"\r")
time.sleep(0.3)
ser.close()

log.info(f"\nИтого строк: {len(lines)}")
if lines:
    ids = set()
    for l in lines:
        ids.add(l[:3])
    log.info(f"Уникальных ID: {sorted(ids)}")
