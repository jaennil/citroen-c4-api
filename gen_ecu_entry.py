"""
Генератор ecu_entry.py - дословных последовательностей входа в каждый ЭБУ.

Берёт дамп переключения блоков (switch.log) и вырезает из него кадры, которыми
DiagBox открывает сессию с конкретным блоком. Дословно, без попыток собрать их
самому: собственные кадры уже дважды приводили к ложным выводам, а тут есть
проверенная запись реального переключения.

Строение кадра команды:

    одиночный:   40 09 <len> c0 <b4> <b5> <plen> 00 <handle> + 15 нулей + payload + cksum
    первый:      40 09 <len> 80 ... (то же, но продолжение следует)
    последующий: 40 09 <len> <0x40|seq> + данные          <- заголовок всего 4 байта!

Бит 0x40 в байте 3 - признак последнего фрагмента, 0x80 - первого.

Ключ к выбору блока - записи "P" в таблице b5=16: 50 <idx> <lo> <hi>, где
P01 - адрес запроса на CAN, P02 - адрес ответа. У BSI это 0x752/0x652.

    ./.venv/bin/python gen_ecu_entry.py switch.log > ecu_entry.py
"""

import collections
import sys

NOISE = {"410901c0f4", "430901c0f2", "064409", "064009"}

# Имена по адресам. Часть подтверждена этим же дампом по читаемым DID, часть -
# по документации PSA DiagOnCan. Неподтверждённые помечены знаком вопроса.
KNOWN = {
    0x752: "BSI",
    0x6A8: "двигатель? (KWP, сервис 21)",
    0x6B5: "? (KWP, сервис 21)",
    0x6AD: "ABS/ESP",
    0x747: "? (UDS, D7xx/D8xx)",
    0x742: "подрулевой COM2008P",
    0x75F: "щиток приборов CMB",
    0x744: "подушки безопасности",
    0x760: "радио",
    0x731: "?",
    0x730: "?",
    0x75D: "?",
    0x765: "?",
    0x76D: "?",
    0x77B: "?",
    0x6C8: "?",
}


def load(path, dev=None):
    out, devs = [], collections.Counter()
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split(",")
            if len(p) < 12 or not p[11] or p[2] != "BULK":
                continue
            if p[3] == "OUT" and p[1] != "S":
                continue
            if p[3] == "IN" and p[1] != "C":
                continue
            devs[p[6]] += 1
            out.append((float(p[0]), p[3], p[6], p[11].lower()))
    d = dev or devs.most_common(1)[0][0]
    return [r for r in out if r[2] == d]


def is_cmd(h):
    return h.startswith("4009") and len(h) >= 10 and h not in NOISE


def head(h):
    """(byte3, b4, b5) - для одиночных и первых кадров, иначе (byte3, None, None)."""
    b = bytes.fromhex(h)
    if len(b) < 9 or not (b[3] & 0x80):
        return b[3] if len(b) > 3 else 0, None, None
    return b[3], b[4], b[5]


def addr_of(frame_hex):
    """P01/P02 из таблицы b5=16: записи 50 <idx> <lo> <hi> в области данных."""
    b = bytes.fromhex(frame_hex)
    pl = b[24:]
    res = {}
    i = 0
    while i + 3 < len(pl):
        if pl[i] == 0x50:
            res[pl[i + 1]] = pl[i + 2] | (pl[i + 3] << 8)
            i += 4
        else:
            i += 1
    return res.get(1), res.get(2)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "switch.log"
    recs = load(path)
    outs = [(ts, h) for ts, d, _, h in recs if d == "OUT" and is_cmd(h)]

    # Последовательность входа: от кадра 00/fe до первого ff/01 или ff/02 включительно,
    # при условии что между ними есть таблица 00/16 - именно она несёт адрес.
    seqs = []
    cur = None
    for i, (ts, h) in enumerate(outs):
        b3, b4, b5 = head(h)
        if b4 == 0x00 and b5 == 0xFE:
            cur = {"t": ts, "frames": [h], "addr": None}
            continue
        if cur is None:
            continue
        cur["frames"].append(h)
        if b4 == 0x00 and b5 == 0x16:
            cur["addr"] = addr_of(h)
        elif b4 == 0xFF and b5 in (0x01, 0x02):
            if cur["addr"]:
                cur["end"] = i
                seqs.append(cur)
            cur = None
        elif len(cur["frames"]) > 12:
            cur = None

    # Сколько НАСТОЯЩИХ чтений пошло после каждой последовательности.
    #
    # Это и есть признак рабочей. DiagBox много раз обходит всю шину, опрашивая
    # блоки на наличие, и такая проба выглядит как полноценный вход - те же пять
    # кадров, тот же адрес, - но за ней ничего не читается. Раньше здесь брaлась
    # просто последняя последовательность на адрес, и для двигателя выбиралась
    # именно проба: UDS-таблица с plen=a4 и командой 10 03 вместо KWP-сессии с
    # plen=78 и StartCommunication 81. Проверено на машине: с пробой блок молчит.
    ends = sorted(s["end"] for s in seqs)
    for s in seqs:
        nxt = next((x for x in ends if x > s["end"]), len(outs))
        n = 0
        for _, hh in outs[s["end"] + 1:nxt]:
            f = head(hh)
            if f[1] == 0xFF and f[2] == 0x06:
                n += 1
        s["reads"] = n

    # Правку байта 8 (дескриптора сессии) здесь пробовали и убрали: казалось, что
    # финальную команду отвергают из-за несовпадения handle с кадрами настройки.
    # Настоящая причина другая - устройство отвергает ЛЮБУЮ первую команду после
    # таблицы, и лечится это повтором в lexia_proto.transact. Кадры остаются
    # дословными, как в дампе.

    # У части блоков в дампе есть последовательности входа И по KWP (финал 81), И по
    # UDS (финал 10 03), а отбор по числу чтений может выбрать не ту. Для двигателя
    # это критично: номер детали 9804436280 - это Valeo V46, и его каталог целиком
    # UDS (22 D4xx), тогда как KWP-запросы блок понимает лишь как устаревшую
    # идентификацию. Послать UDS-запрос после KWP-входа значит порвать связь, см.
    # ecu.is_kwp. Поэтому для таких адресов протокол финала задаём явно.
    WANT_FINAL = {0x6A8: "1003"}
    for s in seqs:
        want = WANT_FINAL.get(s["addr"][0])
        if want and bytes.fromhex(s["frames"][-1])[24:-1].hex() != want:
            s["reads"] = -1          # не выбирать такую

    # На каждый адрес оставляем ту, после которой читали больше всего.
    best = {}
    for s in seqs:
        prev = best.get(s["addr"])
        if prev is None or s["reads"] > prev["reads"]:
            best[s["addr"]] = s

    t0 = recs[0][0]
    print('"""')
    print("Дословные последовательности входа в ЭБУ, снятые с DiagBox.")
    print("")
    print("СГЕНЕРИРОВАН gen_ecu_entry.py - править руками не надо, перегенерируй.")
    print("")
    print("ENTRY[(tx, rx)] - список кадров, которые надо отправить по порядку, чтобы")
    print("интерфейс переключился на этот блок. Кадры с байтом 3 без бита 0x40 -")
    print("незавершённые фрагменты: их пишут подряд, а опрос делают после последнего.")
    print('"""')
    print()
    print("ENTRY = {")
    for (tx, rx), s in sorted(best.items()):
        name = KNOWN.get(tx, "?")
        print(f"    # {name} - чтений после входа в дампе: {s['reads']}")
        print(f"    (0x{tx:03X}, 0x{rx:03X}): [")
        for h in s["frames"]:
            b3, b4, b5 = head(h)
            tag = f"b4={b4:02x} b5={b5:02x}" if b4 is not None else f"фрагмент {b3:02x}"
            print(f'        "{h}",  # {tag}')
        print("    ],")
    print("}")
    print()
    print("NAMES = {")
    for tx, n in sorted(KNOWN.items()):
        print(f'    0x{tx:03X}: "{n}",')
    print("}")
    print()
    print("# Блоки, которых нет в ENTRY: DiagBox только опросил их на наличие через")
    print("# 22 F080 и полной последовательности входа для них в дампе нет.")
    print("PROBED_ONLY = [")
    probed = sorted(set(KNOWN) - {tx for tx, _ in best})
    for tx in probed:
        print(f"    0x{tx:03X},  # {KNOWN[tx]}")
    print("]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
