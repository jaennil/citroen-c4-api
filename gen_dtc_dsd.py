"""
Описания кодов неисправностей из базы DiagBox, а не из грубого скана образа.

Откуда. Раньше описания добывались из `strings` по всему 14-гигабайтному образу
(gen_dtc_names.py) и давали 54 записи: в тексте образа тройки "код / описание /
мнемоника" встречаются редко. Теперь образ монтируется (mount-diagbox.sh), и
описания берутся прямо из базы DSD.FDB, где они лежат рядом с кодом обычной
строкой с однобайтовой длиной.

Формат записи в DSD.FDB: <мусор>\\x00<длина><\\x00><строка>. Код - отдельная
запись, описание идёт следующей. Поэтому ищем код, а за ним первую строку длиной
от восьми печатных символов, пропуская служебные вроде DTC_* и @P*.

Язык французский: в этой сборке DiagBox описания лежат прямо в базе, а словари
dicoA*.zcl (см. dico.py) пронумерованы под другую версию и по @P-ссылкам не
сходятся - проверено на P0116, номер ведёт на посторонний текст.

    ./.venv/bin/python gen_dtc_dsd.py > dtc_fr.py
"""

import re
import sys

DSD = "/mnt/diagbox/AWRoot/dtrd/comm/data/DSD.FDB"
# коды PSA и OBD: буква системы плюс четыре шестнадцатеричные цифры
CODE = re.compile(rb"(?<![0-9A-Z_])([PBCU][0-9A-F]{4})(?![0-9A-Z_])")
TEXT = re.compile(rb"(.)\x00(?P<s>[ -~\xa0-\xff]{8,110})")
SKIP = (b"DTC_", b"@P", b"DEF_PSA", b"Group_", b"MP_", b"ID_", b"CFG")


def descriptions(blob):
    out = {}
    for m in CODE.finditer(blob):
        code = m.group(1).decode()
        seg = blob[m.end():m.end() + 200]
        for mm in TEXT.finditer(seg):
            t = mm.group("s")
            if any(t.startswith(p) for p in SKIP):
                continue
            # хвост - байт длины следующей записи, он не часть описания
            s = t.decode("cp1252", "replace").rstrip()
            # Последний байт строки - это длина СЛЕДУЮЩЕЙ записи, она попадает в
            # тот же диапазон, что французские буквы с диакритикой. Поэтому режем
            # с конца всё, что не может закончить французскую фразу.
            s = re.sub(r"[^0-9A-Za-zàâäçéèêëîïôöùûüÿœÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸŒ%°)\].,;:'\"»+-]+$",
                       "", s).strip()
            if len(s) >= 8 and not s.isupper():
                out.setdefault(code, s)
            break
    return out


def main():
    try:
        blob = open(DSD, "rb").read()
    except FileNotFoundError:
        print("образ не смонтирован: sudo ./mount-diagbox.sh", file=sys.stderr)
        return 1
    d = descriptions(blob)
    print('"""Описания кодов из DSD.FDB образа DiagBox. Сгенерировано gen_dtc_dsd.py."""')
    print("\nDTC_FR = {")
    for k in sorted(d):
        print(f"    {k!r}: {d[k]!r},")
    print("}")
    print(f"\n# записей: {len(d)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
