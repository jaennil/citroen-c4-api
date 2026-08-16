"""
Шаг 2: Сниффер CAN-шины через ELM327
Слушаем все сообщения на CAN-шине и записываем в лог.
Включай/выключай фары — увидим какие ID и данные меняются.

Использование:
  python 02_sniff_can.py [порт]

  Нажми Ctrl+C чтобы остановить. Лог сохранится в can_dump.log
"""

import serial
import sys
import time
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/rfcomm0"
BAUD = 38400
LOG_FILE = "can_dump.log"


def send_cmd(ser: serial.Serial, cmd: str, timeout: float = 2.0) -> str:
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
    return response.decode("ascii", errors="ignore").strip().replace(">", "").strip()


def init_elm(ser: serial.Serial):
    """Инициализация ELM327 для CAN мониторинга."""
    commands = [
        ("ATZ", "Сброс", 3),
        ("ATE0", "Выключить эхо", 1),
        ("ATL0", "Выключить переводы строк", 1),
        ("ATS0", "Выключить пробелы", 1),
        ("ATH1", "Включить заголовки (CAN ID)", 1),
        ("ATSP6", "Протокол CAN 500kbps", 1),
        ("ATCAF0", "Выключить CAN форматирование (сырые данные)", 1),
        ("ATMA", "Начать мониторинг всех CAN сообщений", 1),
    ]

    for cmd, desc, timeout in commands[:-1]:  # все кроме ATMA
        log.info(f"  {cmd} — {desc}")
        resp = send_cmd(ser, cmd, timeout)
        log.info(f"  -> {resp}")
        if "ERROR" in resp.upper():
            log.error(f"Ошибка на команде {cmd}! Адаптер может не поддерживать CAN.")
            sys.exit(1)

    return commands[-1]  # вернём ATMA для запуска мониторинга


def main():
    log.info(f"CAN Сниффер для Citroen C4")
    log.info(f"Подключаемся к {PORT}...")

    try:
        ser = serial.Serial(PORT, BAUD)
    except serial.SerialException as e:
        log.error(f"Не удалось открыть {PORT}: {e}")
        sys.exit(1)

    log.info("Инициализация ELM327...")
    atma_cmd = init_elm(ser)

    log.info(f"\nЗапускаем мониторинг CAN-шины. Лог пишется в {LOG_FILE}")
    log.info("=" * 60)
    log.info("СЕЙЧАС: включай/выключай фары, поворотники, что угодно!")
    log.info("Нажми Ctrl+C чтобы остановить.")
    log.info("=" * 60)

    # Запускаем ATMA (мониторинг)
    ser.write(f"{atma_cmd[0]}\r".encode())
    ser.timeout = 0.1

    msg_count = 0
    seen_ids = set()

    with open(LOG_FILE, "w") as f:
        f.write(f"# CAN dump started at {datetime.now()}\n")
        f.write(f"# timestamp,raw_line\n")

        try:
            buffer = ""
            while True:
                chunk = ser.read(256)
                if chunk:
                    buffer += chunk.decode("ascii", errors="ignore")

                    while "\r" in buffer:
                        line, buffer = buffer.split("\r", 1)
                        line = line.strip()
                        if not line or line == ">":
                            continue

                        timestamp = time.time()
                        f.write(f"{timestamp},{line}\n")
                        msg_count += 1

                        # Пробуем вычленить CAN ID (первые 3 символа для 11-bit)
                        can_id = line[:3] if len(line) >= 3 else "???"
                        if can_id not in seen_ids:
                            seen_ids.add(can_id)
                            log.info(f"  НОВЫЙ CAN ID: 0x{can_id} | данные: {line}")

                        if msg_count % 500 == 0:
                            log.info(f"  ... принято {msg_count} сообщений, уникальных ID: {len(seen_ids)}")

        except KeyboardInterrupt:
            # Останавливаем мониторинг
            ser.write(b"\r")
            time.sleep(0.5)
            ser.close()

            log.info(f"\nОстановлено.")
            log.info(f"Всего сообщений: {msg_count}")
            log.info(f"Уникальных CAN ID: {len(seen_ids)}")
            log.info(f"ID: {sorted(seen_ids)}")
            log.info(f"Лог сохранён в {LOG_FILE}")


if __name__ == "__main__":
    main()
