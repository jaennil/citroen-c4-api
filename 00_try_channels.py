"""
Пробуем разные rfcomm каналы и скорости для ELM327.
Некоторые клоны слушают на нестандартных каналах.
"""

import serial
import subprocess
import sys
import time
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

MAC = "00:10:CC:4F:36:03"
SUDO_PASS = os.environ.get("SUDO_PASS", "")  # пароль в коде не хранится
BAUDS = [38400, 9600, 115200]


def sudo_cmd(cmd: str) -> str:
    result = subprocess.run(
        f"echo {SUDO_PASS} | sudo -S {cmd}",
        shell=True, capture_output=True, text=True, timeout=5
    )
    return result.stdout + result.stderr


def try_channel_baud(channel: int, baud: int) -> bool:
    """Привязать rfcomm к каналу, попробовать AT на заданной скорости."""
    # Отвязать
    sudo_cmd("rfcomm release 0")
    time.sleep(0.3)

    # Привязать к каналу
    sudo_cmd(f"rfcomm bind 0 {MAC} {channel}")
    time.sleep(0.3)
    sudo_cmd("chmod 666 /dev/rfcomm0")
    time.sleep(0.3)

    try:
        ser = serial.Serial("/dev/rfcomm0", baud, timeout=3)
        time.sleep(1)
        ser.flushInput()
        ser.flushOutput()

        # Шлём ATZ
        ser.write(b"ATZ\r")
        time.sleep(2)

        response = b""
        while ser.in_waiting:
            response += ser.read(ser.in_waiting)
            time.sleep(0.1)

        ser.close()

        decoded = response.decode("ascii", errors="ignore").strip()
        if decoded:
            log.info(f"    ОТВЕТ: {decoded}")
            return True
        return False

    except serial.SerialException as e:
        log.debug(f"    SerialException: {e}")
        return False
    except Exception as e:
        log.debug(f"    Error: {e}")
        return False


def main():
    log.info(f"Перебираем каналы (1-30) и скорости для {MAC}...\n")

    for channel in range(1, 31):
        for baud in BAUDS:
            log.info(f"Канал {channel}, скорость {baud}...")
            if try_channel_baud(channel, baud):
                log.info(f"\n>>> НАШЛИ! Канал {channel}, скорость {baud} <<<")
                return

    log.info("\nНи один канал не ответил.")
    log.info("Адаптер v2.1 скорее всего не поддерживает нормальный SPP.")
    log.info("Нужен USB ELM327 v1.5.")


if __name__ == "__main__":
    main()
