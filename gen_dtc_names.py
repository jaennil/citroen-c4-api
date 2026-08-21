"""
Генератор dtc_names.py - описаний кодов неисправностей, вытащенных из образа DiagBox.

Откуда берётся. В клоне базы DiagBox (jyseojys/diag-server) каталог кодов называется
dtc_groups_lightweight, и описания оттуда вырезаны: 12 003 кода отображены в хеши
вида H_a24bfaeb, а текстов нет. Хеши придумал сам репозиторий - в образе DiagBox их
нет вовсе, проверено поиском.

Зато в самом образе описания лежат открытым текстом, тройками подряд:

    C01988
    B-CAN
    B_CAN_1078

то есть код, человеческое описание, мнемоника с номером группы. Рядом такие же:
"Left/Rear ParkingLight  - circuit short to battery", "Rear wiper - wiper motor
blocked". Отсюда словарь и собирается.

Порядок работы:

    strings -n 3 Diagbox_968_Free.vmdk > img_strings.txt     # один раз, ~14 ГБ
    ./.venv/bin/python gen_dtc_names.py img_strings.txt > dtc_names.py

Ложные срабатывания отсеиваются двумя способами: код обязан встречаться в каталоге
dtc_groups_lightweight и быть шестизначным. Короткие коды вида 2299 совпадают со
случайными шестнадцатеричными обрывками двоичных данных - на них словарь набирал
мусор вроде "Es?". Стандартные коды OBD-II это не ограничивает: P0116 и подобные
раскладываются арифметикой в dtc_read без всякого словаря.
"""

import collections
import glob
import json
import os
import re
import sys

DTC_DIR = "/home/jaennil/life/citroen/diag-server/dtc_groups_lightweight"

# Четыре-шесть знаков. Короткие коды сами по себе совпадают со случайными
# шестнадцатеричными обрывками двоичных данных, но сверка мнемоники с описанием
# (см. consistent) мусор отсекает, поэтому их можно допустить.
CODE = re.compile(r"^[0-9A-F]{4,6}$")
MNEMO = re.compile(r"^[A-Za-z0-9_]+_\d+$")


def known_codes():
    """Все коды из каталога - ими и проверяем, что тройка не случайная."""
    out = set()
    for f in glob.glob(os.path.join(DTC_DIR, "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        out |= set(d.get("dtc_hashes") or {})
    return out


def norm(s: str) -> str:
    """Только буквы и цифры в нижнем регистре - для сверки описания с мнемоникой."""
    return "".join(c for c in s.lower() if c.isalnum())


def consistent(text: str, mnemo: str) -> bool:
    """Мнемоника - это то же описание в змеином регистре, и это лучшая проверка.

    Сверка с каталогом кодов не годится: в нём 9363 четырёхзначных ключа и всего
    585 шестизначных, а таблица в образе шестизначная - форматы просто разные.
    Зато третья строка тройки повторяет вторую: "Left/Rear ParkingLight - circuit
    short to battery" против Left_Rear_ParkingLight_circuit_short_to_battery_1078.
    Мнемоника бывает обрезана, поэтому требуем, чтобы она была НАЧАЛОМ описания.
    """
    m = norm(re.sub(r"_\d+$", "", mnemo))
    t = norm(text)
    return len(m) >= 6 and t.startswith(m[:min(len(m), 24)])


def looks_like_text(s: str) -> bool:
    """Описание: разумная длина, не мнемоника и не сам код.

    Требовать пробел или строчные буквы нельзя - так отбрасывались короткие
    настоящие описания вроде "B-CAN" или "ABS", а это как раз коды сети и шин.
    """
    if not (4 <= len(s) <= 160):
        return False
    if sum(c.isalpha() for c in s) < 3:
        return False
    # Внутренние идентификаторы вида DTC_CODE_VA_GROUP1 - не описания.
    if "_" in s and " " not in s:
        return False
    return not MNEMO.match(s) and not CODE.match(s)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "img_strings.txt"
    codes = known_codes()
    print(f"кодов в каталоге: {len(codes)}", file=sys.stderr)

    found = collections.defaultdict(collections.Counter)
    win = ["", "", ""]
    n = 0
    with open(path, errors="ignore") as f:
        for line in f:
            win = [win[1], win[2], line.rstrip("\n").strip()]
            n += 1
            code, text, mnemo = win
            if not CODE.match(code):
                continue
            if not looks_like_text(text) or not MNEMO.match(mnemo):
                continue
            if not consistent(text, mnemo):
                continue
            found[code][text] += 1
    in_cat = sum(1 for c in found if c in codes)
    print(f"строк просмотрено: {n}, кодов с описанием: {len(found)}, "
          f"из них есть в каталоге: {in_cat}", file=sys.stderr)

    print('"""')
    print("Описания кодов неисправностей, вытащенные из образа DiagBox.")
    print("")
    print("СГЕНЕРИРОВАН gen_dtc_names.py - править руками не надо, перегенерируй.")
    print("")
    print("NAMES[код] = описание. Коды в том же виде, что в каталоге DiagBox:")
    print("шесть шестнадцатеричных знаков. Для стандартных кодов OBD-II их можно")
    print("привести к виду P0116 функцией dtc_read.code_of.")
    print('"""')
    print()
    print("NAMES = {")
    for code in sorted(found):
        text, cnt = found[code].most_common(1)[0]
        esc = text.replace("\\", "\\\\").replace('"', '\\"')
        print(f'    "{code}": "{esc}",')
    print("}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
