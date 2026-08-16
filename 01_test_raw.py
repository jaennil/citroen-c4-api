"""
Сырой тест ELM327 — без pyserial, напрямую через os.
Пробуем разные подходы к чтению.
"""

import os
import time
import termios
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PORT = "/dev/rfcomm0"

def main():
    log.info(f"Открываем {PORT} напрямую...")

    fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY)
    log.info(f"fd={fd}, ждём 2 сек...")
    time.sleep(2)

    # Настройки терминала
    attrs = termios.tcgetattr(fd)
    # 38400 baud, 8N1, no flow control
    attrs[4] = termios.B38400  # ispeed
    attrs[5] = termios.B38400  # ospeed
    attrs[2] = attrs[2] | termios.CS8 | termios.CLOCAL | termios.CREAD
    attrs[0] = 0  # iflag
    attrs[1] = 0  # oflag
    attrs[3] = 0  # lflag
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 50  # 5 sec timeout
    termios.tcsetattr(fd, termios.TCSANOW, attrs)

    log.info("Порт настроен. Шлём ATZ...")

    # Очищаем
    termios.tcflush(fd, termios.TCIOFLUSH)
    time.sleep(0.5)

    # Шлём ATZ
    os.write(fd, b"ATZ\r")
    log.info("ATZ отправлен, ждём ответ 5 сек...")
    time.sleep(3)

    # Читаем
    try:
        data = os.read(fd, 1024)
        log.info(f"Ответ ({len(data)} байт): {data!r}")
        if data:
            log.info(f"Текст: {data.decode('ascii', errors='ignore')}")
    except OSError as e:
        log.error(f"Ошибка чтения: {e}")

    # Пробуем ATI
    log.info("\nШлём ATI...")
    os.write(fd, b"ATI\r")
    time.sleep(2)
    try:
        data = os.read(fd, 1024)
        log.info(f"Ответ ({len(data)} байт): {data!r}")
        if data:
            log.info(f"Текст: {data.decode('ascii', errors='ignore')}")
    except OSError as e:
        log.error(f"Ошибка чтения: {e}")

    # Пробуем просто послушать
    log.info("\nСлушаем 5 сек без отправки...")
    time.sleep(5)
    try:
        data = os.read(fd, 1024)
        log.info(f"Данные ({len(data)} байт): {data!r}")
    except OSError as e:
        log.error(f"Ошибка чтения: {e}")

    os.close(fd)
    log.info("Готово.")


if __name__ == "__main__":
    main()
