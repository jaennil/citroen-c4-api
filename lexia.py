"""
Lexia 3 Linux драйвер через libusb.
Порт CLexiaExchanger из kolyandex/Lexia_J2534.

USB: Vendor=0x103a Product=0xf008
EP OUT: 0x06 (bulk, отправка)
EP IN:  0x85 (bulk, приём)
"""

import usb.core
import usb.util
import struct
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

VENDOR_ID = 0x103a
PRODUCT_ID = 0xf008
EP_OUT = 0x06
EP_IN = 0x85
TIMEOUT = 3000  # мс


class LexiaDevice:
    def __init__(self):
        self.dev = None

    def connect(self):
        self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if self.dev is None:
            log.error("Lexia 3 не найдена! Воткни в USB.")
            return False

        log.info(f"Lexia 3 найдена: {self.dev.manufacturer or 'PSA'} (Bus {self.dev.bus}, Device {self.dev.address})")

        # Отсоединяем от ядра если занята
        if self.dev.is_kernel_driver_active(0):
            log.info("Отсоединяем kernel driver...")
            self.dev.detach_kernel_driver(0)

        # Устанавливаем конфигурацию
        try:
            self.dev.set_configuration()
        except usb.core.USBError as e:
            log.warning(f"set_configuration: {e}")

        # Claim interface
        try:
            usb.util.claim_interface(self.dev, 0)
        except usb.core.USBError as e:
            log.warning(f"claim_interface: {e}")

        log.info("Подключено!")
        return True

    def disconnect(self):
        if self.dev:
            usb.util.release_interface(self.dev, 0)
            usb.util.dispose_resources(self.dev)
            self.dev = None
            log.info("Отключено.")

    def send_raw(self, data: bytes) -> bytes:
        """Отправить сырые данные и получить ответ."""
        log.debug(f"  TX: {data.hex()}")

        try:
            self.dev.write(EP_OUT, data, timeout=TIMEOUT)
        except usb.core.USBError as e:
            log.error(f"Ошибка отправки: {e}")
            return b""

        time.sleep(0.05)

        try:
            resp = self.dev.read(EP_IN, 64, timeout=TIMEOUT)
            result = bytes(resp)
            log.debug(f"  RX: {result.hex()}")
            return result
        except usb.core.USBTimeoutError:
            log.debug("  RX: timeout")
            return b""
        except usb.core.USBError as e:
            log.error(f"Ошибка чтения: {e}")
            return b""

    def send_receive(self, data: bytes) -> bytes:
        """Отправить команду и прочитать полный ответ (может быть несколько пакетов)."""
        self.dev.write(EP_OUT, data, timeout=TIMEOUT)
        time.sleep(0.05)

        result = bytearray()
        while True:
            try:
                resp = self.dev.read(EP_IN, 64, timeout=1000)
                result.extend(bytes(resp))
                if len(resp) < 64:
                    break
            except usb.core.USBTimeoutError:
                break
            except usb.core.USBError:
                break

        return bytes(result)


def main():
    lexia = LexiaDevice()

    if not lexia.connect():
        return

    log.info("\n=== Тест связи с Lexia 3 ===\n")

    # Попробуем несколько типов запросов
    # Формат из Windows драйвера: IOCTL_SEND_COMMAND шлёт данные напрямую

    # Тест 1: пустой пинг
    log.info("Тест 1: пинг (пустые данные)...")
    resp = lexia.send_raw(b"\x00" * 8)
    log.info(f"  Ответ: {resp.hex() if resp else 'нет'}")

    # Тест 2: стандартные диагностические заголовки
    log.info("\nТест 2: инициализация...")
    resp = lexia.send_raw(b"\x01\x00\x00\x00")
    log.info(f"  Ответ: {resp.hex() if resp else 'нет'}")

    # Тест 3: запрос версии
    log.info("\nТест 3: попытка разных команд...")
    for i in range(0, 16):
        cmd = bytes([i, 0, 0, 0, 0, 0, 0, 0])
        resp = lexia.send_raw(cmd)
        if resp:
            log.info(f"  CMD {i:02X}: {resp.hex()}")

    lexia.disconnect()
    log.info("\nГотово!")


if __name__ == "__main__":
    main()
