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

POLL = "410901c0f4"
FETCH = "430901c0f2"
ACK = "064409"
POLL_BUSY = "42410901"

POLL_DONE = "42410900"

# Паузы после записи в устройство. Подобраны замером на машине:
#   20/5 мс -> 140 мс на транзакцию, 20/20 успешных
#    5/2 мс -> 111 мс на транзакцию, 20/20 успешных
#    3/1 мс ->  50 мс, но 0/20 успешных - обрыв
# То есть у устройства есть порог около 5 мс, ниже которого оно не успевает.
# Важное следствие: из 111 мс только ~14 мс наши паузы, остальное - ответ самой
# Lexia. Переписывание на другой язык скорость транзакции не изменит.
SETTLE = 0.005
POLL_SETTLE = 0.002

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


def plan_batches(dids, lengths, max_n=MAX_DIDS_PER_REQUEST, max_bytes=30):
    """Разбить DID на пачки, влезающие в один ответ.

    Ограничений два, и второе неочевидное: кроме предела в 10 DID есть предел на
    длину ответа. Замерено на 316 живых параметрах: бюджет 30 байт -> 306 прочитано
    за 3.9 с, бюджет 40 -> только 226, потому что длинные пачки отклоняются целиком.
    """
    cur, used = [], 1  # 1 байт на сам код ответа 0x62
    for d in dids:
        need = 2 + lengths.get(d, 1)
        if cur and (len(cur) >= max_n or used + need > max_bytes):
            yield cur
            cur, used = [], 1
        cur.append(d)
        used += need
    if cur:
        yield cur


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
    """Вытащить полезную нагрузку ответа ЭБУ из ответа Lexia.

    Заголовок ответа фиксированной длины, нагрузка начинается с 29-го байта:

        ... 01 <фмт> <len> 00 05 <len байт ответа>
                     ^25  ^26  ^27 ^28

    Байт 25 - НЕ константа: у BSI там 0x12, у KWP-блока двигателя 0x08. Раньше
    он считался маркером, и из-за этого все ответы блоков, кроме BSI, молча
    отбрасывались как нераспознанные. Опознаём по паре 00 05 и длине.
    """
    if not resp:
        return None
    # штатное место: так разбирается подавляющая часть ответов
    if len(resp) > 29 and resp[27] == 0x00 and resp[28] == 0x05:
        n = resp[26]
        pl = resp[29:29 + n]
        if len(pl) == n and n > 0:
            return pl
    # запасной поиск - ответ мог приехать со сдвигом (многокадровый ISO-TP)
    for i in range(len(resp) - 4):
        if resp[i + 2] == 0x00 and resp[i + 3] == 0x05:
            n = resp[i + 1]
            pl = resp[i + 4:i + 4 + n]
            # первый байт обязан быть кодом ответа: 41-7F положительные и 7F
            # отрицательный по UDS, C1 - положительный StartCommunication KWP
            if len(pl) == n and n > 0 and pl[0] >= 0x40:
                return pl
    return None


def echo_of(cmd: bytes):
    """Какое эхо обязан вернуть ответ на эту команду: пара байт b4/b5.

    В ответе устройства байты 4 и 5 повторяют b4/b5 команды. Это единственный
    способ убедиться, что ответ относится именно к нашему запросу.
    """
    if len(cmd) < 6 or not (cmd[3] & 0x80):
        return None
    return cmd[4], cmd[5]


def echo_ok(expect, resp: bytes) -> bool:
    """Ответ относится к нашей команде? None в expect - проверять нечем."""
    if expect is None or not resp or len(resp) < 6:
        return True
    return (resp[4], resp[5]) == tuple(expect)


def rejected(ack: bytes) -> bool:
    """Отказ устройства вместо квитанции: 15 40 09 xx против 06 40 09."""
    return bool(ack) and ack[0] == 0x15


def empty_result(resp: bytes) -> bool:
    """Пустая квитанция: эхо команды есть, а области результата нет.

    У осмысленного ответа в байтах 14..19 лежит эхо вида <b4> <b5> <len> 00
    <handle>, у пустой квитанции там нули. Так отличается "принял, но ещё не
    сделал" от настоящего результата.
    """
    if not resp or len(resp) < 20:
        return True
    return not any(resp[14:20])


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

    def transact(self, cmd: bytes, deadline: float = 3.0, dwell: float = 0.0,
                 retries: int = 2, expect=None):
        """Полный цикл: команда, ожидание готовности, забор ответа, ack.

        Возвращает (payload, raw). payload - разобранный ответ ЭБУ.

        Два измеренных на машине правила, без которых работала только BSI.

        Готовности ждём ПО ЧАСАМ, а не по числу опросов. Раньше стоял предел в 60
        итераций: устройство отвечает "занят" мгновенно, поэтому 60 опросов
        пролетали за ~200 мс. У BSI тайминг в таблице протокола 250 мс и этого
        хватало, а у KWP-блоков вроде двигателя там 1000 мс - мы сдавались до
        ответа, оставляли команду недоделанной, и следующая запись валилась
        USBError с Errno 5.

        ПОВТОР ПРИ ОТКАЗЕ. Первую команду после таблицы настройки устройство
        отвергает всегда - отвечает не 06 40 09, а 15 40 09 02, - и повтор той же
        команды проходит. Проверено: три отправки подряд дали отказ, потом успех с
        ответом C1 (StartCommunication), потом чтения двигателя пошли. Без повтора
        KWP-связь не поднималась и блок молчал на всё; выглядело это как будто
        переключение блоков не работает, хотя канал переключался исправно.
        """
        if expect is None:
            expect = echo_of(cmd)
        payload, raw = None, b""
        for attempt in range(retries + 1):
            self._w(cmd)
            time.sleep(SETTLE)
            ack = self._r(timeout=500)   # 06 40 09 - принято, 15 40 09 xx - отказ
            if dwell:
                time.sleep(dwell)
            payload, raw = self._collect(deadline)
            if rejected(ack):
                # Отказ. Только что забранный результат - это зависший ответ
                # прошлой команды, а свою надо послать заново.
                continue
            if not echo_ok(expect, raw):
                # Ответ не от нашей команды: переписка разъехалась. Раньше это
                # молча копилось - лишние байты оставались в трубе, сдвиг рос, и
                # через несколько минут работы устройство отвечало USBError [Errno 5]
                # и залипало до переподключения разъёма. Вычищаем трубу и
                # повторяем, вместо того чтобы жить со сдвигом.
                log.debug("ответ не от нашей команды, пересинхронизация")
                self.drain(timeout=60)
                continue
            return payload, raw
        return payload, raw

    def _collect(self, deadline: float = 3.0):
        """Дождаться готовности, забрать результат, подтвердить."""
        poll = bytes.fromhex(POLL)
        done = bytes.fromhex(POLL_DONE)
        until = time.time() + deadline
        while time.time() < until:
            self._w(poll)
            time.sleep(POLL_SETTLE)
            if self._r(timeout=500) == done:
                break

        self._w(bytes.fromhex(FETCH))
        time.sleep(SETTLE)
        raw = self._r()
        # Длинный ответ приходит несколькими пакетами по 64 байта: ровно 64 значит
        # "продолжение следует". Без сборки терялись все блочные чтения KWP - у
        # двигателя один запрос 21 C0 80 01 отдаёт разом 64 параметра и в один
        # пакет не влезает, поэтому из 189 параметров читалось только 21.
        while raw and len(raw) % 64 == 0:
            more = self._r(timeout=400)
            if not more:
                break
            raw += more
        # ОДНО подтверждение на ответ. Правило "по подтверждению на каждый пакет"
        # здесь стояло сутки (коммит 91d4305) и ЛОМАЛО установление связи:
        # строки прошивки в рукопожатии (BOOT1_PSA_XS__ ... @ACTIA) многопакетные,
        # лишние ack на них не давали устройству выйти из фазы boot, и все процессы
        # с этим кодом получали Errno 110 на инициализации - служба после
        # перезапуска 2026-09-04 10:10, все ручные прогоны с 21:17 накануне. Старый
        # процесс службы с прежним кодом в памяти в то же время связь поднимал.
        #
        # При этом наблюдение, ради которого правило вводилось, остаётся верным:
        # после многопакетного ответа двигателя (21 C0 80 01, 252 байта, четыре
        # пакета) DiagBox иногда шлёт несколько 064409 подряд, а у нас после одного
        # ack устройство отвечает отказом 15 40 09 E9 и повторяет устаревший ответ -
        # отсюда 13 с до USBTimeoutError при возврате с KWP на BSI. Но DiagBox шлёт
        # несколько ack лишь в 7 случаях из 25, а в 18 - один. Значит критерий не
        # число пакетов, а что-то ещё, чего по дампу пока не видно. Пока правило не
        # найдено, безопасное поведение - одно подтверждение: связь встаёт, BSI
        # читается, ломается только переключение блоков.
        self._w(bytes.fromhex(ACK))
        time.sleep(POLL_SETTLE)
        return extract_payload(raw), raw

    def transact_frames(self, frames, deadline: float = 5.0, dwell: float = 0.15,
                        refetch: bool = True):
        """Команда из нескольких USB-кадров (фрагментированная).

        Таблица настройки протокола не влезает в один кадр: DiagBox пишет её
        двумя - у первого в байте 3 стоит 0x80 (первый, продолжение следует), у
        последнего 0x40 (последний). Опрос готовности делается ОДИН раз, после
        последнего фрагмента: если опрашивать после каждого, устройство отвечает
        отказом на незавершённую команду.

        На такую команду устройство отвечает ДВАЖДЫ: сперва пустой квитанцией
        (область результата в нулях, статус 0), потом настоящим результатом. Если
        забрать только первую, вся дальнейшая переписка съезжает на шаг - ответ
        таблицы приезжает уже на следующей команде, и блок выглядит молчащим.
        Замерено на машине: именно из-за этого не открывался двигатель.
        """
        for f in frames[:-1]:
            self._w(f)
            time.sleep(POLL_SETTLE)
        # Эхо ждём от ПЕРВОГО кадра: у продолжений заголовок укороченный и b4/b5 в
        # них нет вовсе.
        payload, raw = self.transact(frames[-1], deadline=deadline, dwell=dwell,
                                     expect=echo_of(frames[0]))
        if refetch and empty_result(raw):
            payload, raw = self._collect(deadline)
        return payload, raw

    def read(self, payload: bytes, deadline: float = 3.0, init_first: bool = True):
        """Один запрос к текущему блоку, по дисциплине DiagBox.

        ПЕРЕД КАЖДЫМ ЧТЕНИЕМ идёт кадр init (00/fe). Это измерено на записи
        DiagBox: в сессии двигателя на 235 секунд и 474 чтения узор строго
        I R I R I R - init, чтение, init, чтение. Без init подряд идущие чтения
        рано или поздно оставляют устройство в состоянии, из которого оно
        отвечает USBError [Errno 5] на всё и лечится только переподключением
        разъёма. Именно на этом разваливались длинные обходы блоков.

        Проверенная там же дисциплина опроса: медиана интервала между опросами
        готовности 0.5 мс, до 237 опросов на команду, темп команд ~41 мс. То есть
        частый опрос устройству НЕ вредит - прежняя догадка про это была неверной.
        """
        if init_first:
            self.transact(bytes.fromhex(INIT_FRAME))
        pl, raw = self.transact(read_frame(payload), deadline=deadline)
        # Пустая квитанция вместо результата. То же лечение, что у фрагментированных
        # команд: забрать ещё раз. Без этого первое чтение после входа в блок
        # выглядело как "запрос молчит", и самообучение помечало мёртвым РАБОЧИЙ
        # запрос - проверено на машине, 21C08001 объявлялся молчащим через минуту
        # после успешного чтения им же.
        if pl is None and empty_result(raw):
            pl, raw = self._collect(deadline)
        return pl, raw

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
    print("парсер и конструктор кадров сходятся с дампом:")
    print(" ", describe(extract_payload(ok_read)))
    print(" ", describe(extract_payload(ok_neg)))
    print("  read  22 D8 2B ->", read_did(0x2B).hex())


if __name__ == "__main__":
    self_test()
