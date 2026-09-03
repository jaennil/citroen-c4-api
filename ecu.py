"""
Переключение на произвольный ЭБУ и опрос, кто там живой.

Как это устроено, установлено разбором дампа переключения блоков в DiagBox
(switch.log, analyze_switch.py). Выбор блока НЕ передаётся отдельной командой -
он лежит в таблице настройки протокола, кадр b5=16, в записях вида

    50 <индекс> <lo> <hi>      # 16-битное значение, младший байт первым
    50 01 52 07  ->  P01 = 0x0752   адрес запроса на CAN
    50 02 52 06  ->  P02 = 0x0652   адрес ответа

0x752/0x652 - диагностическая пара BSI из PSA DiagOnCan, отсюда и уверенность,
что прочитано верно. Таблица не влезает в один USB-кадр и шлётся двумя.

Полная последовательность входа - пять кадров: сброс сессии, дескриптор,
таблица (два кадра), затем StartCommunication. Кадры берутся ДОСЛОВНО из
ecu_entry.py, а не собираются заново: попытки собирать их самому дважды давали
ложный вывод об успехе, потому что BSI продолжала отвечать на запросы, которые
я считал адресованными другому блоку.

Здесь только чтение. Актуаторы не трогаются.

    ./.venv/bin/python ecu.py --all           # обойти все известные блоки
    ./.venv/bin/python ecu.py --tx 6A8        # войти в один и опросить
    ./.venv/bin/python ecu.py --tx 6A8 --lid 80,C0,01
"""

import argparse
import logging
import sys

from ecu_entry import ENTRY, NAMES
from lexia_proto import Lexia, describe, read_frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Чем опрашивать незнакомый блок. Порядок важен: сперва UDS, потом KWP - у
# KWP-блоков сервис 22 отвечает отказом, а не молчанием, и это само по себе
# полезный признак.
IDENT_UDS = [0xF080, 0xF190, 0xF18C]   # версия ПО, VIN, серийный номер
IDENT_KWP = [0x80, 0xFE, 0x01, 0xC0]   # идентификация, состояние, блоки данных


# Закрытие сессии блока перед уходом. Дословно из записи DiagBox.
#
# Это и была причина, по которой обход умирал на третьем-четвёртом переключении.
# Сравнение дампов показало: перед таблицей следующего блока DiagBox всегда шлёт
# ff/02, а я не шлю ничего. У неё 83 переключения за 997 с без сбоев, у меня три.
# Оставленные открытыми сессии копятся и исчерпывают интерфейс.
#
# Команда своя на каждый протокол: 10 01 - DiagnosticSessionControl в сессию по
# умолчанию у UDS, 82 - StopCommunication у KWP.
LEAVE_UDS = "400917c0ff020200000000000000000000000000000000001001cb"
LEAVE_KWP = "400916c0ff02010000000000000000000000000000000000825c"

_current = None

# Промежуточный блок для возврата с KWP на UDS.
#
# В записи DiagBox 76 переключений, и среди них НЕТ НИ ОДНОГО перехода KWP -> BSI:
# в 0x752 она входила только из UDS-блоков (дважды из самой себя, один раз из
# 0x76E). Все три её перехода KWP -> UDS ведут в 0x6C1 и 0x747 - и именно эти два
# входа записаны в контексте "пришли из KWP". Наш код же ходил KWP -> 0x752
# напрямую, то есть делал переключение, которого DiagBox не делала никогда, и
# канал рушился ровно там - см. предупреждение "возврат на BSI не удался".
#
# Отсюда возврат в два прыжка: KWP -> 0x747 -> цель. Каждый отдельный переход
# после этого повторяет то, что в записи реально есть. Сами последовательности
# входа BSI и BSM структурно одинаковы (дескриптор, таблица, 10 03), различаются
# только адресом в таблице, так что дело не в них, а в контексте перехода.
# Промежуточный блок зависит от того, ИЗ КАКОГО KWP-блока уходим. В записи
# DiagBox все три перехода KWP -> UDS перечислены ниже, и других нет:
#
#     0x6A8 (двигатель) -> 0x6C1      финальная команда входа 10 C0
#     0x6AD (ABS)       -> 0x747      финальная команда входа 10 03
#     0x6B7             -> 0x747      финальная команда входа 10 03
#
# Разное 10 C0 против 10 03 и есть доказательство, что последовательность входа
# зависит от контекста перехода, а не только от адреса цели. Первая попытка
# уводила двигатель через 0x747 - то есть по маршруту, записанному для ABS, - и
# канал рушился на самом прыжке: USBTimeoutError через 13 с, дальше Errno 5 и
# перетык руками. Измерено 2026-09-03.
# ПРОВЕРЕНО НА МАШИНЕ 2026-09-03: ни один прыжок не помогает, поэтому таблица
# пустая и возврат идёт напрямую. Оба маршрута дали ОДИНАКОВЫЙ отказ -
# USBTimeoutError ровно через 13 с после начала возврата, затем Errno 5 и перетык:
#
#     двигатель -> 0x747 -> BSI    13 с, таймаут   (маршрут, записанный для ABS)
#     двигатель -> 0x6C1 -> BSI    13 с, таймаут   (маршрут, записанный для двигателя)
#     двигатель -> BSI напрямую    13 с, таймаут   (как было до всего этого)
#
# Одинаковые 13 с при трёх разных маршрутах говорят, что дело не в адресе цели и
# не в контексте перехода, а в чём-то одном, что упирается в фиксированный
# таймаут. Дальше не гадать: нужен свой usbmon-захват возврата и покадровый диф с
# DiagBox - тем же способом, каким нашлось отсутствие ff/02. См. capture-collect.sh.
#
# Код прыжка оставлен рабочим: заполнить таблицу - и он снова включится.
KWP_EXIT_HOP = {}
KWP_EXIT_HOP_DEFAULT = None


def exit_hop(key):
    """Через какой UDS-блок уходить из KWP-блока key. None - напрямую."""
    return KWP_EXIT_HOP.get(key, KWP_EXIT_HOP_DEFAULT)



def leave(lex):
    """Закрыть сессию блока, в котором мы сейчас. Без него интерфейс исчерпывается."""
    global _current
    if _current is None:
        return
    hx = LEAVE_KWP if is_kwp(_current) else LEAVE_UDS
    try:
        lex.transact(bytes.fromhex(hx))
    except Exception as e:
        log.warning(f"закрыть сессию 0x{_current[0]:03X} не удалось ({type(e).__name__})")
    _current = None


def groups(frames):
    """Разбить кадры на команды: команда кончается кадром с битом 0x40 в байте 3."""
    out, cur = [], []
    for h in frames:
        b = bytes.fromhex(h)
        cur.append(b)
        if b[3] & 0x40:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def enter(lex, tx, rx, verbose=False, _hop=False):
    """Переключить интерфейс на блок tx/rx. Возвращает ответ на последнюю команду.

    Переход KWP -> UDS идёт через промежуточный 0x747, см. KWP_EXIT_HOP.
    """
    global _current
    key = (tx, rx)
    if key not in ENTRY:
        raise KeyError(f"нет записанной последовательности для 0x{tx:03X}")
    if not _hop and _current is not None and is_kwp(_current) and not is_kwp(key):
        hop = exit_hop(_current)
        if hop and key != hop:
            log.info(f"возврат с KWP 0x{_current[0]:03X} через 0x{hop[0]:03X}")
            enter(lex, *hop, verbose=verbose, _hop=True)
    leave(lex)          # сперва закрыть предыдущую сессию, как делает DiagBox
    last = None
    for i, g in enumerate(groups(ENTRY[key]), 1):
        payload, raw = lex.transact_frames(g)
        last = payload
        if verbose:
            what = f"{len(g)} кадр(ов), {sum(len(x) for x in g)} байт"
            log.info(f"   вход {i}: {what} -> "
                     f"{describe(payload) if payload else (raw.hex()[:32] or 'нет ответа')}")
    _current = key
    return last


def ask(lex, payload: bytes):
    """Один произвольный запрос к текущему блоку."""
    pl, raw = lex.transact(read_frame(payload))
    return pl


def ascii_of(b: bytes) -> str:
    s = "".join(chr(x) if 32 <= x < 127 else "." for x in b)
    return s if any(c.isalnum() for c in s) else ""


def is_kwp(key) -> bool:
    """KWP-блок или UDS - видно по финальной команде входа.

    Полезная нагрузка 81 - это StartCommunication из KWP2000, 10 xx -
    DiagnosticSessionControl из UDS. Различать обязательно: если послать
    KWP-блоку сразу UDS-запрос 22 F080, только что поднятая связь рвётся, и блок
    замолкает на всё остальное. Измерено на двигателе: при опросе с UDS вперёд он
    молчал, при опросе с 21 - отвечал.
    """
    fr = ENTRY.get(key)
    if not fr:
        return False
    b = bytes.fromhex(fr[-1])
    return b[24:-1][:1] == b"\x81"


def probe_one(lex, tx, rx, uds=None, kwp=None, verbose=False):
    """Войти в блок и попробовать его опознать. Возвращает список строк-находок."""
    found = []
    try:
        enter(lex, tx, rx, verbose=verbose)
    except Exception as e:
        return [f"вход не удался: {type(e).__name__}: {e}"]

    # У KWP-блока сперва спрашиваем по-KWP и UDS не трогаем вовсе, иначе рвём связь.
    if is_kwp((tx, rx)):
        for lid in (kwp if kwp is not None else IDENT_KWP):
            pl = ask(lex, bytes([0x21, lid]))
            if pl and pl[0] == 0x61:
                data = pl[2:]
                found.append(f"21 {lid:02X} -> {data.hex()[:60]} {ascii_of(data)}".rstrip())
            elif pl and pl[0] == 0x7F:
                found.append(f"21 {lid:02X} -> отказ NRC {pl[2]:02X}")
        return found

    for did in (uds if uds is not None else IDENT_UDS):
        pl = ask(lex, bytes([0x22, (did >> 8) & 0xFF, did & 0xFF]))
        if pl and pl[0] == 0x62:
            data = pl[3:]
            found.append(f"22 {did:04X} -> {data.hex()} {ascii_of(data)}".rstrip())
        elif pl and pl[0] == 0x7F:
            found.append(f"22 {did:04X} -> отказ NRC {pl[2]:02X}")

    for lid in (kwp if kwp is not None else IDENT_KWP):
        pl = ask(lex, bytes([0x21, lid]))
        if pl and pl[0] == 0x61:
            data = pl[2:]
            found.append(f"21 {lid:02X} -> {data.hex()[:60]} {ascii_of(data)}".rstrip())
        elif pl and pl[0] == 0x7F:
            found.append(f"21 {lid:02X} -> отказ NRC {pl[2]:02X}")
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="обойти все известные блоки")
    ap.add_argument("--tx", help="адрес одного блока, hex (например 6A8)")
    ap.add_argument("--did", help="какие DID читать через 22, hex через запятую")
    ap.add_argument("--lid", help="какие ID читать через 21, hex через запятую")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not args.all and not args.tx:
        ap.error("укажи --all или --tx")

    targets = sorted(ENTRY)
    if args.tx:
        want = int(args.tx, 16)
        targets = [k for k in targets if k[0] == want]
        if not targets:
            print(f"нет последовательности для 0x{want:03X}. Известны: "
                  + " ".join(f"{t:03X}" for t, _ in sorted(ENTRY)))
            return 2

    uds = [int(x, 16) for x in args.did.split(",")] if args.did else None
    kwp = [int(x, 16) for x in args.lid.split(",")] if args.lid else None

    lex = Lexia()
    if not lex.connect():
        return 1
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            log.error("нет связи с машиной: Lexia в OBD? зажигание включено?")
            return 3
        log.info(f"связь есть, обхожу {len(targets)} блок(ов)")
        alive = 0
        for tx, rx in targets:
            name = NAMES.get(tx, "")
            print(f"\n=== 0x{tx:03X}/0x{rx:03X} {name}")
            try:
                res = probe_one(lex, tx, rx, uds, kwp, verbose=args.verbose)
            except Exception as e:
                print(f"    сорвалось: {type(e).__name__}: {e}")
                continue
            if not res:
                print("    молчит")
                continue
            alive += 1
            for line in res:
                print(f"    {line}")
        print(f"\nответили: {alive} из {len(targets)}")
    finally:
        lex.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
