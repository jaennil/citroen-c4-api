"""
Мониторинг CAN — читаем байтами, не строками.
Пробуем ATMA и STM (monitor specific).
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
    time.sleep(0.3)
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
cmd("ATH1")
cmd("ATL1")
cmd("ATS1")
cmd("ATSP6")

# Проверяем что CAN работает
log.info("Проверка CAN...")
resp = cmd("010C", 5)
log.info(f"Обороты: {resp}")

# Попытка 1: ATMA
log.info("\n=== Попытка 1: ATMA ===")
ser.flushInput()
ser.write(b"ATMA\r")
time.sleep(0.5)

buf = b""
start = time.time()
count = 0
while time.time() - start < 10:
    if ser.in_waiting:
        chunk = ser.read(ser.in_waiting)
        buf += chunk
        count += len(chunk)
    time.sleep(0.05)

ser.write(b"\r")
time.sleep(0.5)
ser.flushInput()

log.info(f"ATMA: получено {count} байт")
if buf:
    text = buf.decode("ascii", errors="ignore")
    log.info(f"Первые 500 символов:\n{text[:500]}")
else:
    log.info("Пусто")

# Попытка 2: ATBD (CAN buffer dump)
log.info("\n=== Попытка 2: Читаем параметры циклом ===")
for i in range(10):
    resp = cmd("010C", 2)  # RPM
    rpm_raw = resp.strip().split()
    log.info(f"  [{i}] RPM raw: {resp.strip()}")
    time.sleep(0.5)

# Попытка 3: STM (monitor specific CAN ID)
log.info("\n=== Попытка 3: STMA (если поддерживается) ===")
resp = cmd("STMA", 3)
log.info(f"STMA: {resp[:200] if resp else 'нет ответа'}")

# Попытка 4: CAN filter + monitor
log.info("\n=== Попытка 4: CRA + MA (фильтр + монитор) ===")
cmd("ATCRA")  # clear filters
ser.flushInput()
ser.write(b"ATMA\r")
time.sleep(0.5)

buf2 = b""
start = time.time()
while time.time() - start < 5:
    if ser.in_waiting:
        buf2 += ser.read(ser.in_waiting)
    time.sleep(0.05)

ser.write(b"\r")
time.sleep(0.3)

log.info(f"CRA+MA: получено {len(buf2)} байт")
if buf2:
    log.info(f"Данные:\n{buf2.decode('ascii', errors='ignore')[:500]}")

ser.close()
log.info("\nГотово!")
