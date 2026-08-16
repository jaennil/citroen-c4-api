"""
Шаг 4: Стробоскоп фар через CAN-шину
Основано на реверс-инжиниринге PSA CAN (github.com/prototux/PSA-CAN-RE-old)

CAN ID 0x128 (CDE_COMBINE_SIGNALISATION) — BSI → приборка, статус огней
  Бит 38: feu_croisement (ближний свет)
  Бит 37: feu_route (дальний свет)
  Бит 34: clignotant_droite (правый поворотник)
  Бит 33: clignotant_gauche (левый поворотник)
  Бит 17: indicateur_warning (аварийка)

ВАЖНО: Это ИНДИКАТОРНЫЕ сообщения (BSI сообщает приборке что включено).
Чтобы реально управлять фарами, нужно найти КОМАНДНЫЕ сообщения
(от подрулевого переключателя к BSI). Сниффинг всё равно понадобится
для обнаружения этих командных ID.

Этот скрипт — отправная точка. Сначала проверим что CAN-связь работает,
потом адаптируем под реальное управление.

Использование:
  python 04_strobe.py [порт]
"""

import serial
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/rfcomm0"
BAUD = 38400

# Известные CAN ID из PSA-CAN-RE (hex)
# 0x128 = 296 dec = CDE_COMBINE_SIGNALISATION (BSI -> COMBINE)
# Шина: CONF (comfort CAN bus)

# Байты для 0x128 (8 байт, биты нумеруются от 0):
# Байт 4 (биты 32-39):
#   бит 33 = clignotant_gauche
#   бит 34 = clignotant_droite
#   бит 35 = feu_brouillard_arriere
#   бит 36 = feu_brouillard_avant
#   бит 37 = feu_route (дальний)
#   бит 38 = feu_croisement (ближний)
#   бит 39 = indicateur_diurne (ДХО)
# Байт 2 (биты 16-23):
#   бит 17 = indicateur_warning


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


def init_elm_for_send(ser: serial.Serial):
    """Инициализация ELM327 для отправки CAN-сообщений."""
    commands = [
        ("ATZ", "Сброс", 3),
        ("ATE0", "Выключить эхо", 1),
        ("ATL0", "Выключить переводы строк", 1),
        ("ATS0", "Выключить пробелы", 1),
        ("ATH1", "Включить заголовки", 1),
        ("ATSP6", "Протокол CAN 500kbps", 1),
        ("ATCAF0", "Выключить CAN форматирование", 1),
        ("ATAL", "Разрешить длинные сообщения", 1),
    ]

    for cmd, desc, timeout in commands:
        log.info(f"  {cmd} — {desc}")
        resp = send_cmd(ser, cmd, timeout)
        log.info(f"  -> {resp}")
        if "ERROR" in resp.upper() and cmd not in ("ATAL",):
            log.error(f"Ошибка: {cmd}")
            sys.exit(1)


def send_can_msg(ser: serial.Serial, can_id: int, data: list[int]):
    """Отправить CAN-сообщение через ELM327.

    Устанавливаем заголовок (CAN ID) через ATSH,
    затем шлём данные.
    """
    # Установить CAN ID
    header_cmd = f"ATSH{can_id:03X}"
    send_cmd(ser, header_cmd, 1)

    # Отправить данные (hex строка без пробелов)
    data_hex = "".join(f"{b:02X}" for b in data)
    resp = send_cmd(ser, data_hex, 1)
    return resp


def main():
    log.info("=== C4 CAN Strobe ===")
    log.info(f"Подключаемся к {PORT}...")

    try:
        ser = serial.Serial(PORT, BAUD)
    except serial.SerialException as e:
        log.error(f"Не удалось открыть {PORT}: {e}")
        sys.exit(1)

    log.info("Инициализация...")
    init_elm_for_send(ser)

    log.info("\n=== РЕЖИМЫ ===")
    log.info("1 — Стробоскоп (ближний свет мигает)")
    log.info("2 — Police mode (поворотники лево-право)")
    log.info("3 — Аварийка быстрая")
    log.info("4 — Всё мигает")
    log.info("0 — Всё выключить")
    log.info("q — Выход")

    while True:
        choice = input("\nРежим: ").strip()

        if choice == "q":
            break

        elif choice == "0":
            log.info("Выключаем всё...")
            # Все биты в 0
            send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
            log.info("Выключено.")

        elif choice == "1":
            log.info("Стробоскоп ближний! Ctrl+C чтобы остановить")
            try:
                while True:
                    # Бит 38 = ближний = байт 4, бит 6 (38-32=6) = 0x40
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x40, 0x00, 0x00, 0x00])
                    time.sleep(0.1)
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                    time.sleep(0.1)
            except KeyboardInterrupt:
                send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                log.info("Остановлено.")

        elif choice == "2":
            log.info("Police mode! Ctrl+C чтобы остановить")
            try:
                while True:
                    # Бит 33 = левый поворотник = байт 4, бит 1 = 0x02
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00])
                    time.sleep(0.15)
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                    time.sleep(0.05)
                    # Бит 34 = правый поворотник = байт 4, бит 2 = 0x04
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00])
                    time.sleep(0.15)
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                    time.sleep(0.05)
            except KeyboardInterrupt:
                send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                log.info("Остановлено.")

        elif choice == "3":
            log.info("Быстрая аварийка! Ctrl+C чтобы остановить")
            try:
                while True:
                    # Оба поворотника = 0x02 | 0x04 = 0x06
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x06, 0x00, 0x00, 0x00])
                    time.sleep(0.1)
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                    time.sleep(0.1)
            except KeyboardInterrupt:
                send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                log.info("Остановлено.")

        elif choice == "4":
            log.info("DISCO MODE! Ctrl+C чтобы остановить")
            try:
                while True:
                    # Ближний + дальний + оба поворотника + ПТФ
                    # 0x40(ближн) | 0x20(дальн) | 0x10(ПТФ перед) | 0x04(право) | 0x02(лево) = 0x76
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x76, 0x00, 0x00, 0x00])
                    time.sleep(0.08)
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                    time.sleep(0.08)
                    # Только дальний + задние ПТФ
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x28, 0x00, 0x00, 0x00])
                    time.sleep(0.08)
                    send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                    time.sleep(0.08)
            except KeyboardInterrupt:
                send_can_msg(ser, 0x128, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                log.info("Остановлено.")

    ser.close()
    log.info("Выход.")


if __name__ == "__main__":
    main()
