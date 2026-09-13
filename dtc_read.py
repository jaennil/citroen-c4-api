"""
Чтение кодов неисправностей со всех блоков, как это делает DiagBox.

Два разных сервиса, по протоколу блока:

    UDS-блоки  19 02 FF   ReadDTCInformation, ответ 59 02 <маска> <3 байта кода + статус>...
    KWP-блоки  17 FF 00   ReadDiagnosticTroubleCodes, ответ 57 <кол-во> <2 байта кода + статус>...

Расшифровка идёт из трёх источников, в таком порядке.

  1. dtc_names.py - 54 заводских описания, вытащенных из образа DiagBox
     (см. gen_dtc_names.py). В клоне базы описаний нет: там 12 003 кода, и все
     отображены в хеши вида H_a24bfaeb, а тексты вырезаны. Зато в самом образе они
     лежат открытым текстом, и оттуда их удалось достать.
  2. Таблица KNOWN ниже - стандартные коды OBD-II.
  3. Сам код по SAE J2012 раскладывается арифметикой, без всякой базы:

    старшие 2 бита  система: 00=P двигатель, 01=C шасси, 10=B кузов, 11=U сеть
    биты 13-12      первая цифра
    биты 11-8       вторая цифра
    младший байт    последние две цифры

Проверено на кодах этой машины: 0116 -> P0116, 2299 -> P2299.

Чего словарь не покрывает: в бесплатном образе полной таблицы на 12 003 кода нет,
там всего 54 записи, в основном по кузовному блоку. Незнакомый заводской код
выводится кодом с пометкой смотреть в DiagBox.

Запускать через обёртку, иначе драка со службой за USB:

    ./with-lexia.sh ./.venv/bin/python dtc_read.py            # все блоки
    ./with-lexia.sh ./.venv/bin/python dtc_read.py --tx 6A8   # один блок
"""

import argparse
import logging
import re
import sys
import time

from ecu import enter, is_kwp
from ecu_catalog import ECUS
from poll_all import SKIP_BLOCKS
from lexia_proto import Lexia

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

SYSTEM = {0: "P", 1: "C", 2: "B", 3: "U"}

# Описания стандартных кодов, которые встречались на этой машине или близки к делу.
# Список намеренно короткий: врать про заводские коды PSA хуже, чем не знать.
KNOWN = {
    "P0116": "цепь датчика температуры ОЖ: показания вне допустимого диапазона",
    "P0117": "цепь датчика температуры ОЖ: низкий уровень сигнала",
    "P0118": "цепь датчика температуры ОЖ: высокий уровень сигнала",
    "P0125": "температура ОЖ недостаточна для замкнутого контура топливоподачи",
    "P0128": "термостат: двигатель не выходит на рабочую температуру",
    "P2299": "положение педали тормоза и педали акселератора несовместимы",
    "P0171": "смесь слишком бедная",
    "P0172": "смесь слишком богатая",
    "P0130": "цепь верхнего датчика кислорода",
    "P0136": "цепь нижнего датчика кислорода",
    "P0300": "случайные пропуски воспламенения",
}

# Байт типа отказа в трёхбайтовом коде UDS, по ISO 14229.
FAILURE = {
    0x11: "замыкание на массу",
    0x12: "замыкание на плюс",
    0x13: "обрыв цепи",
    0x14: "обрыв или замыкание на массу",
    0x15: "обрыв или замыкание на плюс",
    0x16: "напряжение ниже порога",
    0x17: "напряжение выше порога",
    0x1C: "напряжение вне диапазона",
    0x21: "сигнал ниже допустимого",
    0x22: "сигнал выше допустимого",
    0x31: "нет сигнала",
    0x38: "данные недостоверны",
    0x43: "рассогласование",
    0x62: "неверный сигнал от другого блока",
    0x88: "шина: нет связи",
}


def code_of(hi: int, lo: int) -> str:
    """Двухбайтовый код -> буквенно-цифровой вид по SAE J2012."""
    v = (hi << 8) | lo
    return f"{SYSTEM[(v >> 14) & 3]}{(v >> 12) & 3}{(v >> 8) & 0xF:X}{v & 0xFF:02X}"


def parse_uds(pl: bytes):
    """59 02 <маска> затем по 4 байта: код (3) + статус (1)."""
    out = []
    body = pl[3:]
    for i in range(0, len(body) - 3, 4):
        code = code_of(body[i], body[i + 1])
        raw = raw_hex(body[i], body[i + 1], body[i + 2])
        out.append((code, body[i + 2], body[i + 3], raw))
    return out


def parse_kwp(pl: bytes):
    """57 <кол-во> затем по 3 байта: код (2) + статус (1)."""
    out = []
    body = pl[2:]
    for i in range(0, len(body) - 2, 3):
        out.append((code_of(body[i], body[i + 1]), None, body[i + 2],
                    raw_hex(body[i], body[i + 1])))
    return out


def raw_hex(hi: int, lo: int, extra=None) -> str:
    """Код в том же виде, в каком он лежит в базе DiagBox - для словаря dtc_names."""
    s = f"{hi:02X}{lo:02X}"
    return s + (f"{extra:02X}" if extra is not None else "")


# Вводные слова, которыми блоки по-разному начинают одну и ту же формулировку:
# "Отсутствие связи с BSI" и "ошибка: отсутствие связи с BSI" - это один текст.
_LEAD = re.compile(r"^\s*(ошибка|дефект|неисправность)\s*[-:]?\s*", re.I)


def _norm_desc(v):
    """Ключ сравнения описаний: без вводного слова, регистра и знаков."""
    return re.sub(r"[^\w\s]", "", _LEAD.sub("", v)).strip().lower()


def _gpc(code, fam):
    """Описание из GPC.FDB, привязанное к исполнению блока (gen_dtc_gpc.py).

    Сначала ищем у того блока, из которого код прочитан. Если там его нет - блок
    ЗЕРКАЛИТ чужой код: BSI отдаёт и P0116 двигателя, и C1205 насоса ГУР, хотя в
    его собственном списке из 458 кодов их нет. Тогда берём описание у блоков, где
    этот код есть, и голосуем: обычно все формулируют одно и то же разными словами
    ("отсутствие связи с BSI"). Если расхождение настоящее - показываем оба
    варианта через "/", а не выбираем наугад.
    """
    try:
        from dtc_gpc_ru import DTC_GPC
    except ImportError:
        return None
    if fam and f"{fam}:{code}" in DTC_GPC:
        return DTC_GPC[f"{fam}:{code}"]
    others = [v for k, v in DTC_GPC.items() if k.split(":", 1)[1] == code]
    if not others:
        return None
    from collections import Counter
    votes = Counter(_norm_desc(v) for v in others)
    top = votes.most_common()
    if len(top) == 1 or top[0][1] > top[1][1]:
        return next(v for v in others if _norm_desc(v) == top[0][0])
    seen, uniq = set(), []
    for v in others:
        if _norm_desc(v) not in seen:
            seen.add(_norm_desc(v))
            uniq.append(v)
    return " / ".join(uniq[:2])


def key(fam: str, code: str, failure) -> str:
    """Имя параметра для одного кода. ОДНО на все пути записи.

    Тип отказа обязан быть в имени: один код приходит с несколькими типами
    (B1228 - с пятью), и без суффикса они схлопываются в одну строку, а при записи
    дают несколько замеров с одним именем и одним временем. Какой из них попадёт
    на график - лотерея, причём статусы у них разные (8 и 9), то есть код может
    показаться сохранённым, хотя один из его типов активен.
    """
    return f"DTC:{fam}:{code}" + (f"-{failure:02X}" if failure is not None else "")


def describe(code: str, failure, status, raw=None, fam=None) -> str:
    parts = []
    # Словарь, вытащенный из образа DiagBox (см. gen_dtc_names.py): 54 заводских
    # описания. Их немного - в бесплатном образе полной таблицы на 12 003 кода нет,
    # - но то, что есть, точнее любой догадки, потому берём в первую очередь.
    from dtc_names import NAMES
    # Порядок источников от точного к приблизительному:
    #   1. KNOWN - проверенные вручную описания кодов ЭТОЙ машины;
    #   2. dtc_gpc_ru - 1229 описаний из GPC.FDB, привязанных к исполнению НАШЕГО
    #      блока (gen_dtc_gpc.py). Точнее всех остальных каталожных источников
    #      именно из-за привязки, и текст сразу русский;
    #   3. dtc_names - 54 записи из грубого скана образа, ключ по сырому коду;
    #   4. dtc_names_ru - 370 описаний из базы DSD.FDB образа, переведённых с
    #      французского по словарю терминов (gen_dtc_dsd.py + dtc_tr.py); без
    #      привязки к блоку, поэтому ниже приоритетом;
    #   5. честная отсылка к DiagBox, если не нашлось нигде.
    try:
        from dtc_names_ru import DTC_RU
    except ImportError:
        DTC_RU = {}
    gpc = _gpc(code, fam)
    if code in KNOWN:
        parts.append(KNOWN[code])
    elif gpc:
        parts.append(gpc)
    elif raw and raw in NAMES:
        parts.append(NAMES[raw])
    elif code in DTC_RU:
        parts.append(DTC_RU[code])
    elif code[0] == "P" and code[1] == "0":
        parts.append("стандартный код двигателя, описание смотреть в DiagBox")
    else:
        parts.append("заводской код PSA, описание смотреть в DiagBox")
    if failure is not None and failure in FAILURE:
        parts.append(FAILURE[failure])
    if status is not None:
        flags = []
        if status & 0x01:
            flags.append("активна")
        if status & 0x08:
            flags.append("сохранена")
        if flags:
            parts.append(", ".join(flags))
    return "; ".join(parts)


def read_block(lex, tx, rx):
    """Коды одного блока. Возвращает (список (код, тип_отказа, статус, raw), сырьё, ok).

    ok РАЗЛИЧАЕТ два случая, которые раньше выглядели одинаково и стоили испорченной
    истории: блок ответил и кодов действительно нет (ok=True, пустой список), и блок
    промолчал или отказал (ok=False). Молчание здесь обычное дело - "тишина это
    свойство состояния, а не запроса", - и записывать её как "коды пропали" нельзя.
    """
    enter(lex, tx, rx)
    if is_kwp((tx, rx)):
        pl, _ = lex.read(b"\x17\xff\x00", deadline=8.0)
        if pl and pl[0] == 0x57:
            return parse_kwp(pl), None, True
        return [], pl, False
    pl, _ = lex.read(b"\x19\x02\xff", deadline=8.0)
    if pl and pl[0] == 0x59:
        return parse_uds(pl), None, True
    return [], pl, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx", help="один блок, hex (например 6A8)")
    ap.add_argument("--sqlite", help="записать найденные коды в базу телеметрии")
    args = ap.parse_args()

    # Пропускать те же блоки, что и poll_all - список один, чтобы не разъезжался.
    # Проверено 01.09.2026: без пропуска VCI (0x6C8) обход умирает. Прочитались
    # пять блоков, потом девять подряд отвалились по USBTimeoutError, и коллапс
    # начался ровно через один блок после VCI - в точности как описано выше про
    # 24 молчащих запроса подряд.
    targets = [k for k in sorted(ECUS) if k[0] not in SKIP_BLOCKS]
    if args.tx:
        want = int(args.tx, 16)
        targets = [k for k in targets if k[0] == want]
        if not targets:
            print("известные адреса: " + " ".join(f"{t:03X}" for t, _ in sorted(ECUS)))
            return 2

    store = None
    if args.sqlite:
        from storage import Store
        store = Store(args.sqlite)

    lex = Lexia()
    if not lex.connect():
        return 1
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            print("нет связи с машиной: зажигание включено?")
            return 3
        total = 0
        for tx, rx in targets:
            info = ECUS[(tx, rx)]
            try:
                codes, raw, ok = read_block(lex, tx, rx)
            except Exception as e:
                print(f"\n0x{tx:03X} {info['ru']}: сорвалось ({type(e).__name__})")
                continue
            print(f"\n0x{tx:03X} {info['ru']} ({'KWP' if is_kwp((tx, rx)) else 'UDS'})")
            if not codes:
                print("    кодов нет" if ok
                      else f"    отказ на запрос: NRC {raw[2]:02X}")
                continue
            total += len(codes)
            if store:
                # Код как параметр: имя DTC:<блок>:<код>, значение - байт статуса,
                # ярлык - описание. Так он попадает в Grafana обычной таблицей, без
                # отдельной схемы, а история статусов остаётся видна во времени.
                store.write([(0, key(info["fam"], c, f), "статус", float(st),
                              describe(c, f, st, rw, info["fam"]))
                             for c, f, st, rw in codes], ts=time.time())
            for code, failure, status, raw_code in codes:
                fail = f" тип {failure:02X}" if failure is not None else ""
                print(f"    {code}{fail}  статус {status:02X}")
                print(f"        {describe(code, failure, status, raw_code)}")
        print(f"\nвсего кодов: {total}")
        if store:
            print(f"записано в {args.sqlite}")
    finally:
        try:
            lex.disconnect()
        except Exception:
            pass
        if store:
            store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
