"""
Общий слой протокола Lexia 3 с РАЗБОРОМ ответов.

До сих пор все скрипты в проекте ответы выбрасывали, и всё "подтверждалось на глаз".
Здесь ответ реально парсится, поэтому видно 62/6F (успех) и 7F + NRC (отказ).

Разбор выведен из lexia_usb.log:
  запрос  400918c0ff0603000100000000000000000000000000000022d8716a
  ответ   44091ec0ff060300010000000000ff06090001aa00000000011204000562d871004b
                                                        ^^ ^^ ^^^^^ ^^^^^^^^^^ ^^
                                                        12 len 0005  payload   chk
  запрос  40091bc0ff060600010000000000000000000000000000002fd875030a0145
  ответ   44091dc0ff060600010000000000ff06080001aa0000000001120300057f2f2226
                                                                    ^^^^^^ = 7F 2F 22

Контрольный байт: последний байт подобран так, чтобы 8-битная сумма всего кадра
была 0xFF. Правило (0xBA - id) - частный случай, верный только пока всё остальное
в кадре константно, поэтому здесь считается общая сумма.
"""

import logging
import time

import usb.core
import usb.util

log = logging.getLogger(__name__)

VENDOR_ID = 0x103A
PRODUCT_ID = 0xF008
EP_OUT = 0x06
EP_IN = 0x85
TIMEOUT = 3000

INIT_FRAME = "400915c000fe0000aa00000000000000000000000000000039"
READ_PREFIX = "400918c0ff06030001" + "00" * 15      # для 3-байтной нагрузки (22 D8 xx)
ACT_PREFIX = "40091bc0ff06060001" + "00" * 15       # для 6-байтной (2F D8 xx 03 0A 01)

POLL = "410901c0f4"
FETCH = "430901c0f2"
ACK = "064409"
POLL_BUSY = "42410901"

# Паузы после записи в устройство. Подобраны замером на машине: при 5/2 мс
100% успешных транзакций и ~111 мс на запрос; при 3/1 мс ломается полностью
(0 из 20). То есть у устройства есть порог около 5 мс, ниже которого оно не
успевает. Основное время транзакции - ответ самой Lexia, не наш код.
SETTLE = 0.005
POLL_SETTLE = 0.002
POLL_DONE = "42410900"

# ISO 14229 negative response codes
NRC = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange (DID не существует)",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x72: "generalProgrammingFailure",
    0x78: "responsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
    0x88: "vehicleSpeedTooHigh",
}


def checksum(body: bytes) -> int:
    """Байт, добивающий 8-битную сумму кадра до 0xFF."""
    return (0xFF - sum(body)) & 0xFF


def frame(prefix_hex: str, payload: bytes) -> bytes:
    body = bytes.fromhex(prefix_hex) + payload
    return body + bytes([checksum(body)])


def read_did(did: int) -> bytes:
    """UDS 22 D8 xx - ReadDataByIdentifier."""
    return frame(READ_PREFIX, bytes([0x22, 0xD8, did]))


# Максимум DID в одном запросе. Измерено на машине: 10 проходит, 12 отклоняется.
# Ровно это же число DiagBox показывает как "select up to 10 parameters" - значит
# ограничение протокольное, а не интерфейса.
MAX_DIDS_PER_REQUEST = 10


def read_frame(payload: bytes) -> bytes:
    """Кадр чтения произвольной длины: поля длины считаются из нагрузки."""
    total = 9 + 15 + len(payload) + 1
    hdr = bytes([0x40, 0x09, total - 4, 0xC0, 0xFF, 0x06, len(payload), 0x00, 0x01])
    body = hdr + b"\x00" * 15 + payload
    return body + bytes([checksum(body)])


def read_multi_frame(dids) -> bytes:
    """Один запрос на несколько 16-битных DID: 22 <DID><DID>..."""
    pl = bytes([0x22]) + b"".join(bytes([(d >> 8) & 0xFF, d & 0xFF]) for d in dids)
    return read_frame(pl)


def parse_multi(payload: bytes, lengths: dict) -> dict:
    """Разобрать ответ 62 <DID><данные><DID><данные>...

    lengths - сколько байт данных у каждого DID; без этого границы не определить.
    """
    out = {}
    if not payload or payload[0] != 0x62:
        return out
    b = payload[1:]
    i = 0
    while i + 2 <= len(b):
        did = (b[i] << 8) | b[i + 1]
        i += 2
        ln = lengths.get(did, 1)
        out[did] = b[i:i + ln]
        i += ln
    return out


def actuate(did: int, on: bool = True) -> bytes:
    """UDS 2F D8 xx 03 0A 01 - InputOutputControlByIdentifier."""
    return frame(ACT_PREFIX, bytes([0x2F, 0xD8, did, 0x03, 0x0A, 0x01 if on else 0x00]))


def link_status(resp: bytes):
    """Статус связи Lexia с машиной - байт 18 ответа.

    Во всём рабочем дампе (366 ответов) здесь всегда 0x01. Значение 0x0C в дампе
    не встречается ни разу и наблюдалось только когда машина недоступна: либо
    Lexia не воткнута в OBD, либо выключено зажигание и BSI спит.
    """
    if not resp or len(resp) < 19:
        return None, "ответ слишком короткий"
    st = resp[18]
    return st, {0x01: "OK", 0x03: "?", 0x0C: "нет связи с машиной (OBD/зажигание)"}.get(st, f"неизвестно 0x{st:02X}")


def extract_payload(resp: bytes):
    """Вытащить полезную нагрузку UDS из ответа Lexia.

    Ищем маркер 12 <len> 00 05, дальше <len> байт - это ответ ECU.
    """
    if not resp:
        return None
    for i in range(len(resp) - 4):
        if resp[i] == 0x12 and resp[i + 2] == 0x00 and resp[i + 3] == 0x05:
            n = resp[i + 1]
            payload = resp[i + 4:i + 4 + n]
            if len(payload) == n and n > 0:
                return payload
    return None


def describe(payload: bytes) -> str:
    """Человекочитаемая расшифровка ответа UDS."""
    if not payload:
        return "нет ответа / не распознано"
    if payload[0] == 0x7F:
        svc = payload[1] if len(payload) > 1 else 0
        code = payload[2] if len(payload) > 2 else 0
        return f"ОТКАЗ 7F на сервис {svc:02X}: NRC {code:02X} = {NRC.get(code, 'неизвестный')}"
    if payload[0] == 0x62:
        did = payload[1:3].hex().upper() if len(payload) >= 3 else "??"
        val = payload[3:].hex() if len(payload) > 3 else ""
        return f"OK чтение (62) DID {did} = {val or '(пусто)'}"
    if payload[0] == 0x6F:
        did = payload[1:3].hex().upper() if len(payload) >= 3 else "??"
        return f"OK актуатор (6F) DID {did}"
    return f"нераспознанный ответ: {payload.hex()}"


class Lexia:
    def __init__(self):
        self.dev = None

    def connect(self) -> bool:
        self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if self.dev is None:
            log.error("Lexia 3 не найдена (нет 103a:f008). Воткнута? Нужен root или udev-правило.")
            return False
        try:
            if self.dev.is_kernel_driver_active(0):
                self.dev.detach_kernel_driver(0)
        except usb.core.USBError as e:
            log.warning(f"detach: {e}")
        try:
            self.dev.set_configuration()
        except usb.core.USBError:
            pass
        try:
            usb.util.claim_interface(self.dev, 0)
        except usb.core.USBError as e:
            log.error(f"claim_interface: {e} - скорее всего нужен root.")
            return False
        log.info("Lexia 3 подключена.")
        return True

    def _w(self, data: bytes):
        self.dev.write(EP_OUT, data, timeout=TIMEOUT)

    def _r(self, timeout=TIMEOUT) -> bytes:
        try:
            return bytes(self.dev.read(EP_IN, 64, timeout=timeout))
        except usb.core.USBError:
            return b""

    def transact(self, cmd: bytes, poll_limit: int = 60):
        """Полный цикл: команда, ожидание готовности, забор ответа, ack.

        Возвращает (payload, raw). payload - разобранный ответ UDS.
        """
        self._w(cmd)
        time.sleep(SETTLE)
        self._r(timeout=500)  # 064009

        poll = bytes.fromhex(POLL)
        done = bytes.fromhex(POLL_DONE)
        for _ in range(poll_limit):
            self._w(poll)
            time.sleep(POLL_SETTLE)
            if self._r(timeout=500) == done:
                break

        self._w(bytes.fromhex(FETCH))
        time.sleep(SETTLE)
        raw = self._r()
        self._w(bytes.fromhex(ACK))
        time.sleep(POLL_SETTLE)
        return extract_payload(raw), raw

    def init_session(self):
        return self.transact(bytes.fromhex(INIT_FRAME))

    # Квитанции и статусы поллинга - не данные, их надо пропускать.
    ACKS = {bytes.fromhex(x) for x in ("064009", "064000", "064409", "42410901", "42410900")}

    def drain(self, timeout=120):
        """Выгрести из трубы остатки предыдущей сессии (например, после DiagBox)."""
        n = 0
        while self._r(timeout=timeout):
            n += 1
            if n > 200:
                break
        return n

    def read_all(self, timeout=1200) -> bytes:
        """Прочитать содержательный ответ, пропуская квитанции.

        Устройство сначала отвечает квитанцией 064009, и только затем присылает
        сам ответ, возможно несколькими пакетами (64 байта + хвост).
        """
        out = bytearray()
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            chunk = self._r(timeout=400)
            if not chunk:
                if out:
                    break
                continue
            if not out and chunk in self.ACKS:
                continue  # это квитанция, ждём данные
            out += chunk
            if len(chunk) < 64:
                break
        return bytes(out)

    def device_boot(self, verbose=True) -> bool:
        """Рукопожатие с самой Lexia, до всякой машины.

        Снято из lexia_full.log: DiagBox сначала вычитывает версии загрузчика и
        прошивки (BOOT1_PSA_XS__, APPLI_XS_Fuji_, ACTIA/921815), и только после
        этого кадр fe отвечает успехом. Без этой преамбулы fe возвращает статус
        0x0C, что мы и получали, посылая fe холодным.
        """
        from lexia_boot import DEVICE_BOOT

        for i, hx in enumerate(DEVICE_BOOT, 1):
            _, resp = self.transact(bytes.fromhex(hx))
            if verbose:
                txt = "".join(chr(b) if 32 <= b < 127 else "." for b in resp[15:])
                log.info(f"  dev {i}/{len(DEVICE_BOOT)} cmd={hx[10:12]}: {txt.strip('.') or resp.hex()[:44]}")

        # Теперь fe должен ответить успехом
        _, resp = self.transact(bytes.fromhex(INIT_FRAME))
        st, meaning = link_status(resp)
        if verbose:
            log.info(f"  init fe: статус 0x{st:02X} - {meaning}" if st is not None else "  init fe: нет ответа")
        return st == 0x01

    def boot(self, verbose=True):
        """Полная стартовая процедура DiagBox.

        Одного INIT_FRAME мало: BSI не отвечает на чтения, пока не пройдут
        DiagnosticSessionControl 10 01 / 10 03 и три конфигурационных блока.
        Кадры воспроизводятся дословно из дампа.
        """
        from lexia_boot import BOOT

        self.init_session()
        for i, hx in enumerate(BOOT, 1):
            payload, raw = self.transact(bytes.fromhex(hx))
            if verbose:
                b = bytes.fromhex(hx)
                pl = b[24:-1]
                tag = f"SessionControl {pl.hex()}" if pl[:1] == b"\x10" else f"{len(b)} байт"
                log.info(f"  boot {i}/{len(BOOT)} {tag}: {describe(payload) if payload else (raw.hex()[:40] or 'нет ответа')}")
            self.init_session()
        return True

    def disconnect(self):
        if self.dev:
            usb.util.release_interface(self.dev, 0)
            usb.util.dispose_resources(self.dev)
            self.dev = None


def self_test():
    """Проверка парсера на реальных кадрах из lexia_usb.log (без железа)."""
    ok_read = bytes.fromhex("44091ec0ff060300010000000000ff06090001aa00000000011204000562d871004b")
    ok_neg = bytes.fromhex("44091dc0ff060600010000000000ff06080001aa0000000001120300057f2f2226")
    assert extract_payload(ok_read) == bytes.fromhex("62d87100"), extract_payload(ok_read)
    assert extract_payload(ok_neg) == bytes.fromhex("7f2f22"), extract_payload(ok_neg)
    assert read_did(0x71) == bytes.fromhex(
        "400918c0ff0603000100000000000000000000000000000022d8716a")
    assert actuate(0x75) == bytes.fromhex(
        "40091bc0ff060600010000000000000000000000000000002fd875030a0145")
    print("парсер и конструктор кадров сходятся с дампом:")
    print(" ", describe(extract_payload(ok_read)))
    print(" ", describe(extract_payload(ok_neg)))
    print("  read  22 D8 2B ->", read_did(0x2B).hex())
    print("  actu  2F D8 2B ->", actuate(0x2B).hex())


if __name__ == "__main__":
    self_test()
