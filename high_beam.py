"""
УСТАРЕЛО И НЕ РАБОТАЕТ. Оставлено как история попытки.

Два независимых приговора, оба измерены на машине 2026-08-16:
  1. DID D82B на этой BSI НЕ СУЩЕСТВУЕТ - 22 D8 2B отвечает 7F 22 31
     requestOutOfRange, как и D829/D82A/D82C. Вариант BSI базовый, без AFS,
     головным светом она не управляет вообще.
  2. Протокол здесь неполный: нет рукопожатия с устройством, поэтому связь
     с машиной не поднимется в принципе. Рабочий протокол - lexia_proto.py,
     рабочий CLI - lights.py.

Дальний свет (feux de route) для Citroen C4 через Lexia 3.

ID актуатора найден в дампе диагностической базы DiagBox (jyseojys/diag-server,
ecu_groups_jsons/BSI2010_*.json): MP_COMMANDE_FEUX_DE_ROUTE = DID 0xD82B.

Почему этой базе можно верить: в ней все четыре уже подтверждённых на машине
актуатора совпадают байт в байт (D870 габариты, D871/D872 поворотники,
D875 стоп), а старое поколение BSI (AEE2004) использует совсем другую схему -
KWP2000, сервис 0x21, все огни в одной группе 0x21C5, без DID вида D8xx.
Наш дамп lexia_usb.log содержит 22D871/22D872 и 2FD875, то есть UDS с D8xx,
значит это BSI2010 - и карта D82B применима.

Важно: команды огней лежат в ДВУХ блоках, а не в одном диапазоне 0x70-0x7F:
  D82x - передние фары (ближний L/R, дальний, ПТФ)
  D87x - габариты, поворотники, задний туман, задний ход, стоп

Зажигание ON, мотор ЗАГЛУШЕН - при заведённом BSI отказывает (проверено).
"""

import logging
import time

import usb.core
import usb.util

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

VENDOR_ID = 0x103A
PRODUCT_ID = 0xF008
EP_OUT = 0x06
EP_IN = 0x85
TIMEOUT = 3000

# BSI держит актуатор включённым около 3 секунд, команды "выключить" не найдено.
LATCH_SEC = 3.0

# Актуаторы BSI2010. confirmed - проверено на машине, diagbox - из базы DiagBox.
ACTUATORS = {
    "low_beam_left":   (0x29, "ближний левый",   "diagbox"),
    "low_beam_right":  (0x2A, "ближний правый",  "diagbox"),
    "high_beam":       (0x2B, "ДАЛЬНИЙ СВЕТ",    "diagbox"),
    "front_fog":       (0x2C, "ПТФ передние",    "diagbox"),
    "side_lights":     (0x70, "габариты",        "confirmed"),
    "right_indicator": (0x71, "поворотник прав", "confirmed"),
    "left_indicator":  (0x72, "поворотник лев",  "confirmed"),
    "rear_fog":        (0x73, "задний туман",    "diagbox"),
    "reverse":         (0x74, "задний ход",      "diagbox"),
    "brake_light":     (0x75, "стоп-сигнал",     "confirmed"),
    "drl":             (0x7C, "дневные ходовые", "diagbox"),
}

INIT_FRAME = "400915c000fe0000aa00000000000000000000000000000039"
ACTUATE_PREFIX = "40091bc0ff06060001000000000000000000000000000000"


def build_actuate(actuator_id: int) -> bytes:
    """Кадр UDS 2F D8 <id> 03 0A 01 в обёртке Lexia.

    Контрольный байт добивает 8-битную сумму всего 31-байтного кадра до 0xFF,
    поэтому формула работает для любого id (проверено на 0x75 из дампа).
    """
    checksum = (0xBA - actuator_id) & 0xFF
    return bytes.fromhex(ACTUATE_PREFIX) + bytes(
        [0x2F, 0xD8, actuator_id, 0x03, 0x0A, 0x01, checksum]
    )


class Lexia:
    def __init__(self):
        self.dev = None

    def connect(self) -> bool:
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

    def send_and_poll(self, data: bytes) -> bytes:
        """Полный цикл DiagBox: команда, поллинг готовности, чтение, ack."""
        self.dev.write(EP_OUT, data, timeout=TIMEOUT)
        time.sleep(0.02)

        try:
            self.dev.read(EP_IN, 64, timeout=TIMEOUT)
        except usb.core.USBTimeoutError:
            pass

        poll_cmd = bytes.fromhex("410901c0f4")
        for _ in range(100):
            self.dev.write(EP_OUT, poll_cmd, timeout=TIMEOUT)
            time.sleep(0.005)
            try:
                if bytes(self.dev.read(EP_IN, 64, timeout=500)) == bytes.fromhex("42410900"):
                    break
            except usb.core.USBTimeoutError:
                continue

        self.dev.write(EP_OUT, bytes.fromhex("430901c0f2"), timeout=TIMEOUT)
        time.sleep(0.02)
        try:
            result = bytes(self.dev.read(EP_IN, 64, timeout=TIMEOUT))
        except usb.core.USBTimeoutError:
            result = b""

        self.dev.write(EP_OUT, bytes.fromhex("064409"), timeout=TIMEOUT)
        time.sleep(0.01)
        return result

    def init_session(self) -> bool:
        result = self.send_and_poll(bytes.fromhex(INIT_FRAME))
        return len(result) > 0

    def activate(self, actuator_id: int) -> bytes:
        return self.send_and_poll(build_actuate(actuator_id))

    def disconnect(self):
        if self.dev:
            usb.util.release_interface(self.dev, 0)
            usb.util.dispose_resources(self.dev)
            self.dev = None


def fire(lexia: Lexia, name: str):
    """Одиночное включение актуатора."""
    actuator_id, title, source = ACTUATORS[name]
    log.info(f"{title} (id=0x{actuator_id:02X}, {source})...")
    lexia.init_session()
    lexia.activate(actuator_id)
    log.info(f"Отправлено. Гореть должно ~{LATCH_SEC:.0f}с.")


def hold(lexia: Lexia, name: str, period: float = 2.0):
    """Держать актуатор включённым, переотправляя команду до истечения защёлки.

    Дальний свет это ~110-130 Вт, а мотор заглушен - долго не держим.
    """
    actuator_id, title, source = ACTUATORS[name]
    log.info(f"Удержание: {title} (id=0x{actuator_id:02X}). Ctrl+C для остановки.")
    try:
        while True:
            lexia.init_session()
            lexia.activate(actuator_id)
            time.sleep(period)
    except KeyboardInterrupt:
        log.info("Остановлено, ждём снятия защёлки BSI...")
        time.sleep(LATCH_SEC)


def scan(lexia: Lexia, ids):
    """Запасной вариант: перебор id с паузой, оператор глазами смотрит что зажглось."""
    log.info("Скан. После каждого id смотри на машину и отвечай y/n.")
    found = []
    for actuator_id in ids:
        input(f"\nEnter - подать 0x{actuator_id:02X}...")
        lexia.init_session()
        lexia.activate(actuator_id)
        log.info(f"0x{actuator_id:02X} отправлен, смотри.")
        time.sleep(LATCH_SEC + 1.0)
        if input("Дальний загорелся? (y/n): ").strip().lower().startswith("y"):
            found.append(actuator_id)
            log.info(f">>> ДАЛЬНИЙ = 0x{actuator_id:02X}")
            break
    if not found:
        log.info("Ничего не найдено в этом диапазоне.")
    return found


def main():
    lexia = Lexia()
    if not lexia.connect():
        return

    print()
    print("╔══════════════════════════════════════╗")
    print("║   CITROEN C4 - ДАЛЬНИЙ СВЕТ          ║")
    print("║   Зажигание ON, мотор OFF!           ║")
    print("╠══════════════════════════════════════╣")
    print("║  1 — Дальний свет (разово)           ║")
    print("║  2 — Дальний свет (держать)          ║")
    print("║  3 — Ближний левый                   ║")
    print("║  4 — Ближний правый                  ║")
    print("║  5 — ПТФ передние                    ║")
    print("║  6 — Моргание дальним                ║")
    print("║  s — Скан запасных id (если 1 молчит)║")
    print("║  q — Выход                           ║")
    print("╚══════════════════════════════════════╝")

    try:
        while True:
            choice = input("\nРежим: ").strip().lower()

            if choice == "q":
                break
            elif choice == "1":
                fire(lexia, "high_beam")
            elif choice == "2":
                hold(lexia, "high_beam")
            elif choice == "3":
                fire(lexia, "low_beam_left")
            elif choice == "4":
                fire(lexia, "low_beam_right")
            elif choice == "5":
                fire(lexia, "front_fog")
            elif choice == "6":
                log.info("Моргание дальним. Ctrl+C для остановки.")
                try:
                    while True:
                        fire(lexia, "high_beam")
                        time.sleep(LATCH_SEC + 0.5)
                except KeyboardInterrupt:
                    log.info("Остановлено.")
            elif choice == "s":
                # Если 0xD82B не сработал - соседние id того же блока, потом дыры в D87x.
                scan(lexia, [0x28, 0x2D, 0x27, 0x76, 0x77, 0x7D])
    finally:
        lexia.disconnect()


if __name__ == "__main__":
    main()
