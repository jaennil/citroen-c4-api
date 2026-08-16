"""
Шаг 1: Проверка связи с ELM327
Отправляем AT-команды и смотрим что он отвечает.
Если отвечает — адаптер рабочий.
"""

import serial
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# /dev/rfcomm0 для Bluetooth, /dev/ttyUSB0 для USB
PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/rfcomm0"
BAUD = 38400  # стандартная скорость для ELM327 Bluetooth


def send_cmd(ser: serial.Serial, cmd: str, timeout: float = 2.0) -> str:
    """Отправить AT-команду и получить ответ."""
    ser.timeout = timeout
    ser.write(f"{cmd}\r".encode())
    response = b""
    while True:
        chunk = ser.read(1)
        if not chunk:
            break
        response += chunk
        if b">" in response:
            break
    decoded = response.decode("ascii", errors="ignore").strip().replace(">", "").strip()
    return decoded


def main():
    log.info(f"Подключаемся к {PORT} на скорости {BAUD}...")

    try:
        ser = serial.Serial(PORT, BAUD)
    except serial.SerialException as e:
        log.error(f"Не удалось открыть {PORT}: {e}")
        log.info("Убедись что ELM327 спарен и rfcomm привязан:")
        log.info("  sudo rfcomm bind 0 XX:XX:XX:XX:XX:XX")
        sys.exit(1)

    log.info("Порт открыт. Шлём AT-команды...\n")

    # Сброс адаптера
    log.info(">>> ATZ (сброс)")
    resp = send_cmd(ser, "ATZ", timeout=3)
    log.info(f"<<< {resp}\n")

    # Версия прошивки
    log.info(">>> ATI (версия)")
    resp = send_cmd(ser, "ATI")
    log.info(f"<<< {resp}\n")

    # Проверяем реальную версию чипа
    log.info(">>> AT@1 (описание устройства)")
    resp = send_cmd(ser, "AT@1")
    log.info(f"<<< {resp}\n")

    # Напряжение бортовой сети (работает только при подключении к OBD)
    log.info(">>> ATRV (напряжение)")
    resp = send_cmd(ser, "ATRV")
    log.info(f"<<< {resp}\n")

    # Поддерживаемые протоколы
    log.info(">>> ATDP (текущий протокол)")
    resp = send_cmd(ser, "ATDP")
    log.info(f"<<< {resp}\n")

    # Пробуем установить CAN 500kbps (ISO 15765-4, 11bit, 500kbps)
    log.info(">>> ATSP6 (установить протокол CAN 500kbps)")
    resp = send_cmd(ser, "ATSP6")
    log.info(f"<<< {resp}\n")

    log.info(">>> ATDP (проверяем что протокол установился)")
    resp = send_cmd(ser, "ATDP")
    log.info(f"<<< {resp}\n")

    ser.close()
    log.info("Готово! Если видишь 'ELM327 v1.5' или похожее — адаптер рабочий.")
    log.info("Если видишь 'ELM327 v2.1' — клон, но может работать.")


if __name__ == "__main__":
    main()
