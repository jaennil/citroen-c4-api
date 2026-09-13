"""
Словари DiagBox: разбор формата и поиск строки по номеру.

В образе три поколения словарей, у всех одна схема - заголовок, таблица смещений,
подряд идущие строки:

    APP/LEXIA/DICO/citroen/dicoA??.zcl      магия 1B1B, cp1251, строки через \\x00
    APP/OUTILREP/Dico/THESAURU.dbz          магия B\\x13B\\x13, cp1251, ремонтные фразы
    AWRoot/dtrd/trans/POLUXDATA??_??.DU8    магия \\x14#\\x14#, UTF-8, границы по
                                            следующему смещению, БЕЗ нулевого байта

Заголовок: 4 байта магии, дальше три u32 - начало таблицы смещений (обычно 100 или
108), начало строк и число записей. Проверка целостности: начало_таблицы + число*4
должно равняться началу строк, иначе файл разобран неверно.

POLUXDATA - тот самый словарь, на который ссылаются базы записями вида
"@P27831-POLUXDATA". Русский файл на 55 620 строк.

    ./.venv/bin/python dico.py POLUXDATA 27831
    ./.venv/bin/python dico.py --list
"""

import glob
import os
import struct
import sys

MOUNT = "/mnt/diagbox"
FILES = {
    "POLUXDATA": (f"{MOUNT}/AWRoot/dtrd/trans/POLUXDATAru_RU.DU8", "utf-8", False),
    "POLUXDATA_EN": (f"{MOUNT}/AWRoot/dtrd/trans/POLUXDATAen_GB.DU8", "utf-8", False),
    "LEXIA_RU": (f"{MOUNT}/APP/LEXIA/DICO/citroen/dicoARU.zcl", "cp1251", True),
    "LEXIA_EN": (f"{MOUNT}/APP/LEXIA/DICO/citroen/dicoAGB.zcl", "cp1251", True),
    "LEXIA_FR": (f"{MOUNT}/APP/LEXIA/DICO/citroen/dicoAFR.zcl", "cp1251", True),
    "THESAURUS_RU": (f"{MOUNT}/APP/OUTILREP/Dico/THESAURU.dbz", "cp1251", True),
}


class Dico:
    def __init__(self, path, encoding="utf-8", zero_terminated=False):
        self.d = open(path, "rb").read()
        hdr, strings_at, self.count = struct.unpack("<III", self.d[4:16])
        if hdr + self.count * 4 != strings_at:
            raise ValueError(f"{path}: заголовок не сходится "
                             f"({hdr} + {self.count}*4 != {strings_at})")
        self.offs = list(struct.unpack(f"<{self.count}I",
                                       self.d[hdr:hdr + self.count * 4])) + [len(self.d)]
        self.enc, self.zero = encoding, zero_terminated

    def __len__(self):
        return self.count

    def __getitem__(self, i):
        if not 0 <= i < self.count:
            return None
        o = self.offs[i]
        if o <= 0 or o >= len(self.d):
            return ""
        end = self.d.index(b"\x00", o) if self.zero else self.offs[i + 1]
        return self.d[o:end].decode(self.enc, "replace").strip()

    def find(self, text, limit=20):
        """Обратный поиск: индексы строк, содержащих подстроку."""
        t = text.lower()
        out = []
        for i in range(self.count):
            s = self[i]
            if s and t in s.lower():
                out.append((i, s))
                if len(out) >= limit:
                    break
        return out


def open_dico(name="POLUXDATA"):
    path, enc, zero = FILES[name]
    return Dico(path, enc, zero)


def main():
    if "--list" in sys.argv:
        for name, (path, enc, _) in FILES.items():
            ok = os.path.exists(path)
            n = len(open_dico(name)) if ok else 0
            print(f"  {name:14} {'есть' if ok else 'НЕТ '} строк {n:7} {enc:7} {path}")
        return 0
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    d = open_dico(sys.argv[1])
    for a in sys.argv[2:]:
        if a.isdigit():
            print(f"  @P{a}: {d[int(a)]!r}")
        else:
            for i, s in d.find(a):
                print(f"  @P{i}: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
