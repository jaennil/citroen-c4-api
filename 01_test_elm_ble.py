"""
Тест ELM327 через BLE (Bluetooth Low Energy).
Для дешёвых v2.1 клонов которые не работают через rfcomm/SPP.

BLE сервис: 0000fff0
Характеристики: fff1 (notify/read), fff2 (write)
"""

import asyncio
import sys
import logging
from bleak import BleakClient, BleakScanner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# MAC адрес ELM327
DEVICE_MAC = sys.argv[1] if len(sys.argv) > 1 else "00:10:CC:4F:36:03"

# Стандартные UUID для BLE ELM327 клонов
SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
# Возможные характеристики — попробуем найти автоматически
NOTIFY_UUIDS = [
    "0000fff1-0000-1000-8000-00805f9b34fb",
    "0000fff2-0000-1000-8000-00805f9b34fb",
]
WRITE_UUIDS = [
    "0000fff2-0000-1000-8000-00805f9b34fb",
    "0000fff1-0000-1000-8000-00805f9b34fb",
]


class ELMResponse:
    def __init__(self):
        self.data = bytearray()
        self.event = asyncio.Event()

    def reset(self):
        self.data = bytearray()
        self.event.clear()

    def callback(self, sender, data: bytearray):
        log.debug(f"  BLE notify: {data}")
        self.data.extend(data)
        if b">" in self.data:
            self.event.set()


async def send_cmd(client: BleakClient, write_char: str, response: ELMResponse, cmd: str, timeout: float = 3.0) -> str:
    response.reset()
    await client.write_gatt_char(write_char, f"{cmd}\r".encode(), response=False)
    try:
        await asyncio.wait_for(response.event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    decoded = response.data.decode("ascii", errors="ignore").strip().replace(">", "").strip()
    return decoded


async def main():
    log.info(f"Ищем ELM327 BLE: {DEVICE_MAC}")

    # Сканируем
    log.info("Сканируем BLE устройства...")
    device = await BleakScanner.find_device_by_address(DEVICE_MAC, timeout=10)

    if not device:
        log.error(f"Устройство {DEVICE_MAC} не найдено! Убедись что ELM327 воткнут и мигает.")
        sys.exit(1)

    log.info(f"Найден: {device.name} ({device.address})")
    log.info(f"Подключаемся...")

    async with BleakClient(device) as client:
        log.info(f"Подключено! MTU: {client.mtu_size}")

        # Показываем все сервисы и характеристики
        log.info("\nСервисы и характеристики:")
        write_char = None
        notify_char = None

        for service in client.services:
            log.info(f"  Сервис: {service.uuid} ({service.description})")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                log.info(f"    Характеристика: {char.uuid} [{props}]")

                # Ищем write характеристику
                if "write" in char.properties or "write-without-response" in char.properties:
                    if "fff" in char.uuid:
                        write_char = char.uuid
                        log.info(f"      >>> WRITE: {char.uuid}")

                # Ищем notify характеристику
                if "notify" in char.properties:
                    if "fff" in char.uuid:
                        notify_char = char.uuid
                        log.info(f"      >>> NOTIFY: {char.uuid}")

        if not write_char or not notify_char:
            log.error("Не нашли write/notify характеристики! Адаптер нестандартный.")
            # Пробуем стандартные
            write_char = WRITE_UUIDS[0]
            notify_char = NOTIFY_UUIDS[0]
            log.info(f"Пробуем стандартные: write={write_char}, notify={notify_char}")

        # Подписываемся на уведомления
        response = ELMResponse()
        await client.start_notify(notify_char, response.callback)
        log.info(f"\nНастроено: write={write_char}, notify={notify_char}")

        # Тестируем AT-команды
        log.info("\n=== Тест AT-команд ===\n")

        commands = [
            ("ATZ", "Сброс", 4),
            ("ATI", "Версия", 3),
            ("ATRV", "Напряжение бортсети", 3),
            ("ATSP6", "Протокол CAN 500kbps", 3),
            ("ATDP", "Текущий протокол", 3),
            ("0100", "OBD: поддерживаемые PIDs", 5),
        ]

        for cmd, desc, timeout in commands:
            log.info(f">>> {cmd} ({desc})")
            resp = await send_cmd(client, write_char, response, cmd, timeout)
            if resp:
                log.info(f"<<< {resp}\n")
            else:
                log.info(f"<<< (нет ответа)\n")

        await client.stop_notify(notify_char)

    log.info("Готово!")


if __name__ == "__main__":
    asyncio.run(main())
