"""
Тест моргания — пробуем разные способы:
1. Быстрое переключение (правый перебивает левый)
2. Команда с байтом 00 вместо 01 (возможно = выключить)
3. Init-сессия как сброс между включениями
"""

import usb.core
import usb.util
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

VENDOR_ID = 0x103a
PRODUCT_ID = 0xf008
EP_OUT = 0x06
EP_IN = 0x85
TIMEOUT = 3000


class Lexia:
    def __init__(self):
        self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if self.dev.is_kernel_driver_active(0):
            self.dev.detach_kernel_driver(0)
        try:
            self.dev.set_configuration()
        except:
            pass
        try:
            usb.util.claim_interface(self.dev, 0)
        except:
            pass
        log.info("Lexia подключена.")

    def raw_send(self, data: bytes):
        """Отправить и прочитать ответ, минимум задержек."""
        self.dev.write(EP_OUT, data, timeout=TIMEOUT)
        try:
            return bytes(self.dev.read(EP_IN, 64, timeout=500))
        except:
            return b""

    def fast_actuator(self, actuator_id: int):
        """Быстрая активация без ожидания завершения."""
        checksum = (0xBA - actuator_id) & 0xFF
        cmd = bytes.fromhex("40091bc0ff06060001000000000000000000000000000000") + \
              bytes([0x2f, 0xd8, actuator_id, 0x03, 0x0a, 0x01, checksum])
        self.raw_send(cmd)

    def fast_actuator_off(self, actuator_id: int):
        """Попытка выключить — байт 00 вместо 01."""
        checksum_base = (0xBA - actuator_id) & 0xFF
        # Пробуем 00 вместо 01 в последнем значимом байте
        cmd = bytes.fromhex("40091bc0ff06060001000000000000000000000000000000") + \
              bytes([0x2f, 0xd8, actuator_id, 0x03, 0x0a, 0x00, (checksum_base + 1) & 0xFF])
        self.raw_send(cmd)

    def init_session(self):
        self.raw_send(bytes.fromhex("400915c000fe0000aa00000000000000000000000000000039"))


def main():
    lex = Lexia()

    RIGHT = 0x71
    LEFT = 0x72

    print("\n1 — Быстрое переключение лево-право (без пауз)")
    print("2 — Включить правый → через 0.3с попытка выключить")
    print("3 — Включить правый → init сброс → включить левый (быстро)")
    print("4 — Супер-быстрый police (минимум задержек)")
    print("q — Выход\n")

    while True:
        choice = input("Режим: ").strip()

        if choice == "q":
            break

        elif choice == "1":
            log.info("Быстрое переключение 10 раз...")
            for i in range(10):
                lex.init_session()
                lex.fast_actuator(RIGHT)
                time.sleep(0.3)
                lex.init_session()
                lex.fast_actuator(LEFT)
                time.sleep(0.3)
            log.info("Готово.")

        elif choice == "2":
            log.info("Включаю правый, потом пробую выключить...")
            lex.init_session()
            lex.fast_actuator(RIGHT)
            time.sleep(0.3)
            log.info("Пробую выключить (байт 00)...")
            lex.init_session()
            lex.fast_actuator_off(RIGHT)
            log.info("Готово. Погас?")

        elif choice == "3":
            log.info("Правый → сброс → левый, 10 раз...")
            for i in range(10):
                lex.init_session()
                lex.fast_actuator(RIGHT)
                time.sleep(0.2)
                lex.init_session()  # сброс
                time.sleep(0.05)
                lex.init_session()
                lex.fast_actuator(LEFT)
                time.sleep(0.2)
                lex.init_session()  # сброс
                time.sleep(0.05)
            log.info("Готово.")

        elif choice == "4":
            log.info("Супер-быстрый police! Ctrl+C стоп")
            try:
                while True:
                    lex.init_session()
                    lex.fast_actuator(RIGHT)
                    time.sleep(0.15)
                    lex.init_session()
                    lex.fast_actuator(LEFT)
                    time.sleep(0.15)
            except KeyboardInterrupt:
                log.info("Стоп.")

    usb.util.release_interface(lex.dev, 0)


if __name__ == "__main__":
    main()
