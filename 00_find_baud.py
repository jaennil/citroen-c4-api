"""
Определяем правильную скорость для ELM327.
Пробуем все стандартные скорости и шлём ATZ.
"""

import serial
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/rfcomm0"
BAUDS = [9600, 38400, 115200, 57600, 230400, 19200, 4800]


def try_baud(port: str, baud: int) -> str | None:
    try:
        ser = serial.Serial(port, baud, timeout=3)
        time.sleep(0.5)

        # Очищаем буфер
        ser.flushInput()
        ser.flushOutput()

        # Шлём ATZ с разными окончаниями строк
        for ending in [b"\r\n", b"\r", b"\n"]:
            ser.write(b"ATZ" + ending)
            time.sleep(1.5)

            response = b""
            while ser.in_waiting:
                response += ser.read(ser.in_waiting)
                time.sleep(0.1)

            decoded = response.decode("ascii", errors="ignore").strip()
            if decoded:
                ser.close()
                return f"baud={baud}, ending={ending!r}, response: {decoded}"

        ser.close()
        return None
    except Exception as e:
        return None


def main():
    log.info(f"Ищем правильную скорость для {PORT}...")
    log.info(f"Пробуем: {BAUDS}\n")

    for baud in BAUDS:
        log.info(f"Пробуем {baud}...")
        result = try_baud(PORT, baud)
        if result:
            log.info(f"  ОТВЕТ: {result}")
            log.info(f"\n  >>> Правильная скорость: {baud} <<<")
            return
        else:
            log.info(f"  тишина")

    log.info("\nНи одна скорость не подошла.")
    log.info("Возможно адаптер BLE-only и rfcomm не работает.")
    log.info("Попробуем BLE-подход (bleak).")


if __name__ == "__main__":
    main()
