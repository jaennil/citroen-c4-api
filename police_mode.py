"""
POLICE MODE для Citroen C4 через Lexia 3 напрямую из Linux.
Протокол реверс-инженерен из USB-трафика DiagBox.

Зажигание ON, мотор заглушен!
"""

import usb.core
import usb.util
import time
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

VENDOR_ID = 0x103a
PRODUCT_ID = 0xf008
EP_OUT = 0x06
EP_IN = 0x85
TIMEOUT = 3000

# Актуаторы (реверс-инженерены из DiagBox USB трафика)
ACTUATORS = {
    "side_lights":     0x70,
    "right_indicator": 0x71,
    "left_indicator":  0x72,
    "brake_light":     0x75,
}


class Lexia:
    def __init__(self):
        self.dev = None

    def connect(self):
        self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if self.dev is None:
            log.error("Lexia 3 не найдена!")
            return False

        if self.dev.is_kernel_driver_active(0):
            self.dev.detach_kernel_driver(0)

        try:
            self.dev.set_configuration()
        except usb.core.USBError:
            pass

        try:
            usb.util.claim_interface(self.dev, 0)
        except usb.core.USBError:
            pass

        log.info("Lexia 3 подключена.")
        return True

    def send(self, data: bytes) -> bytes:
        self.dev.write(EP_OUT, data, timeout=TIMEOUT)
        time.sleep(0.02)
        try:
            resp = self.dev.read(EP_IN, 64, timeout=TIMEOUT)
            return bytes(resp)
        except usb.core.USBTimeoutError:
            return b""

    def send_and_poll(self, data: bytes) -> bytes:
        """Отправить команду и ждать завершения (как DiagBox)."""
        self.dev.write(EP_OUT, data, timeout=TIMEOUT)
        time.sleep(0.02)

        # Читаем первый ответ
        try:
            self.dev.read(EP_IN, 64, timeout=TIMEOUT)
        except usb.core.USBTimeoutError:
            pass

        # Поллим статус (410901c0f4) пока не получим 42410900
        poll_cmd = bytes.fromhex("410901c0f4")
        for _ in range(100):
            self.dev.write(EP_OUT, poll_cmd, timeout=TIMEOUT)
            time.sleep(0.005)
            try:
                resp = bytes(self.dev.read(EP_IN, 64, timeout=500))
                if resp == bytes.fromhex("42410900"):
                    break
            except usb.core.USBTimeoutError:
                continue

        # Читаем результат
        read_cmd = bytes.fromhex("430901c0f2")
        self.dev.write(EP_OUT, read_cmd, timeout=TIMEOUT)
        time.sleep(0.02)
        try:
            result = bytes(self.dev.read(EP_IN, 64, timeout=TIMEOUT))
        except usb.core.USBTimeoutError:
            result = b""

        # Ack
        ack = bytes.fromhex("064409")
        self.dev.write(EP_OUT, ack, timeout=TIMEOUT)
        time.sleep(0.01)

        return result

    def init_session(self):
        """Инициализация сессии с BSI (как DiagBox перед актуатором)."""
        log.info("Инициализация сессии BSI...")
        init_cmd = bytes.fromhex("400915c000fe0000aa00000000000000000000000000000039")
        result = self.send_and_poll(init_cmd)
        log.info(f"  Сессия: {result.hex() if result else 'нет ответа'}")
        return len(result) > 0

    def activate_actuator(self, actuator_id: int):
        """Активировать актуатор по ID."""
        checksum = (0xBA - actuator_id) & 0xFF
        cmd = bytes.fromhex("40091bc0ff06060001000000000000000000000000000000") + \
              bytes([0x2f, 0xd8, actuator_id, 0x03, 0x0a, 0x01, checksum])
        self.send_and_poll(cmd)

    def disconnect(self):
        if self.dev:
            usb.util.release_interface(self.dev, 0)
            usb.util.dispose_resources(self.dev)
            self.dev = None


def police_mode(lexia: Lexia, speed: float = 0.3):
    """Попеременно лево-право."""
    log.info(f"POLICE MODE! (speed={speed}s) Ctrl+C для остановки")
    try:
        while True:
            lexia.init_session()
            lexia.activate_actuator(ACTUATORS["right_indicator"])
            time.sleep(speed)
            lexia.init_session()
            lexia.activate_actuator(ACTUATORS["left_indicator"])
            time.sleep(speed)
    except KeyboardInterrupt:
        log.info("Остановлено.")


def strobe_mode(lexia: Lexia, speed: float = 0.2):
    """Стробоскоп габаритов."""
    log.info(f"STROBE MODE! (speed={speed}s) Ctrl+C для остановки")
    try:
        while True:
            lexia.init_session()
            lexia.activate_actuator(ACTUATORS["side_lights"])
            time.sleep(speed)
    except KeyboardInterrupt:
        log.info("Остановлено.")


def disco_mode(lexia: Lexia, speed: float = 0.15):
    """Всё мигает по очереди."""
    log.info(f"DISCO MODE! (speed={speed}s) Ctrl+C для остановки")
    order = ["right_indicator", "left_indicator", "side_lights", "brake_light"]
    try:
        i = 0
        while True:
            lexia.init_session()
            lexia.activate_actuator(ACTUATORS[order[i % len(order)]])
            time.sleep(speed)
            i += 1
    except KeyboardInterrupt:
        log.info("Остановлено.")


def main():
    lexia = Lexia()
    if not lexia.connect():
        return

    print()
    print("╔══════════════════════════════════╗")
    print("║   CITROEN C4 LIGHT CONTROL       ║")
    print("║   Зажигание ON, мотор OFF!       ║")
    print("╠══════════════════════════════════╣")
    print("║  1 — Police mode (лево-право)    ║")
    print("║  2 — Strobe (габариты)           ║")
    print("║  3 — Disco (всё по очереди)      ║")
    print("║  4 — Один раз: правый            ║")
    print("║  5 — Один раз: левый             ║")
    print("║  6 — Один раз: стоп-сигнал       ║")
    print("║  7 — Один раз: габариты          ║")
    print("║  q — Выход                       ║")
    print("╚══════════════════════════════════╝")

    while True:
        choice = input("\nРежим: ").strip()

        if choice == "q":
            break
        elif choice == "1":
            police_mode(lexia)
        elif choice == "2":
            strobe_mode(lexia)
        elif choice == "3":
            disco_mode(lexia)
        elif choice in ("4", "5", "6", "7"):
            actuator_map = {"4": "right_indicator", "5": "left_indicator",
                            "6": "brake_light", "7": "side_lights"}
            name = actuator_map[choice]
            log.info(f"Активирую {name}...")
            lexia.init_session()
            lexia.activate_actuator(ACTUATORS[name])
            log.info("Готово!")

    lexia.disconnect()


if __name__ == "__main__":
    main()
