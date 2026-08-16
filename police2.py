"""
Police mode v2 — спамим команды чтобы свет не успевал гаснуть.
BSI зажигает на ~3 сек, мы шлём каждые 0.5 сек = не гаснет.
Переключаем актуатор каждые N повторов.
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

RIGHT = 0x71
LEFT = 0x72
SIDE = 0x70
BRAKE = 0x75


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

    def send(self, data: bytes):
        self.dev.write(EP_OUT, data, timeout=TIMEOUT)
        try:
            return bytes(self.dev.read(EP_IN, 64, timeout=500))
        except:
            return b""

    def init(self):
        self.send(bytes.fromhex("400915c000fe0000aa00000000000000000000000000000039"))
        # poll
        poll = bytes.fromhex("410901c0f4")
        for _ in range(20):
            resp = self.send(poll)
            if resp and resp[-1] == 0:
                break
        self.send(bytes.fromhex("430901c0f2"))
        try:
            self.dev.read(EP_IN, 64, timeout=300)
        except:
            pass
        self.send(bytes.fromhex("064409"))

    def actuate(self, act_id: int):
        chk = (0xBA - act_id) & 0xFF
        cmd = bytes.fromhex("40091bc0ff06060001000000000000000000000000000000") + \
              bytes([0x2f, 0xd8, act_id, 0x03, 0x0a, 0x01, chk])
        self.send(cmd)
        # быстрый poll
        poll = bytes.fromhex("410901c0f4")
        for _ in range(30):
            resp = self.send(poll)
            if resp and resp[-1] == 0:
                break
        self.send(bytes.fromhex("430901c0f2"))
        try:
            self.dev.read(EP_IN, 64, timeout=300)
        except:
            pass
        self.send(bytes.fromhex("064409"))


def main():
    lex = Lexia()
    log.info("Lexia подключена.")

    print("\n1 — Police: правый 1сек → левый 1сек (спам)")
    print("2 — Стробоскоп: габариты спам каждые 0.5с")
    print("3 — Правый непрерывно (спам каждые 0.5с)")
    print("4 — Только один раз правый (для сравнения)")
    print("q — Выход\n")

    while True:
        choice = input("Режим: ").strip()
        if choice == "q":
            break

        elif choice == "1":
            log.info("Police! Ctrl+C стоп")
            try:
                while True:
                    # Правый — держим 1 сек (2 повтора по 0.5с)
                    for _ in range(2):
                        lex.init()
                        lex.actuate(RIGHT)
                        time.sleep(0.3)
                    # Левый — держим 1 сек
                    for _ in range(2):
                        lex.init()
                        lex.actuate(LEFT)
                        time.sleep(0.3)
            except KeyboardInterrupt:
                log.info("Стоп.")

        elif choice == "2":
            log.info("Стробоскоп! Ctrl+C стоп")
            try:
                while True:
                    lex.init()
                    lex.actuate(SIDE)
                    time.sleep(0.5)
            except KeyboardInterrupt:
                log.info("Стоп.")

        elif choice == "3":
            log.info("Правый непрерывно! Ctrl+C стоп")
            try:
                while True:
                    lex.init()
                    lex.actuate(RIGHT)
                    time.sleep(0.5)
            except KeyboardInterrupt:
                log.info("Стоп.")

        elif choice == "4":
            log.info("Один раз правый...")
            lex.init()
            lex.actuate(RIGHT)
            log.info("Готово.")

    usb.util.release_interface(lex.dev, 0)


if __name__ == "__main__":
    main()
