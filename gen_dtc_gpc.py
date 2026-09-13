"""
Описания кодов неисправностей из GPC.FDB - запросом, с привязкой к нашему блоку.

Почему это работает там, где не работало раньше. В GPC описания лежат не текстом, а
ссылками вида "@P29613-POLUXDATA" на словарь, и прошлая попытка признала их битыми:
для P0116 ссылка вела на текст про блокировку гидротрансформатора. Ошибка была не в
ссылках, а в выборе строки. Код 0116 встречается в базе 254 раза с 21 разной меткой -
по строке на каждое исполнение блока у каждой машины. Взяли чужую, получили чужой
текст.

С привязкой к нашему блоку метка ровно одна. Цепочка:

    VEHICULE (наша платформа B7 = VEHID 28)
      -> FAMILY   по адресу на шине, FAMIDCANE/FAMIDCANR = наши TX/RX
      -> ECU      по ECUEDOFILENAME, он называет исполнение (INJ_V46_32_493_B7.xml)
      -> I_ECUDTC -> DTC

Словарь POLUXDATA сразу РУССКИЙ, так что переводить, в отличие от inline-текстов
DSD.FDB, ничего не нужно.

    ./.venv/bin/python gen_dtc_gpc.py > dtc_gpc_ru.py
    ./.venv/bin/python gen_dtc_gpc.py --raw     # только ссылки, без словаря
"""

import json
import os
import re
import sys
from subprocess import run

HERE = __file__.rsplit("/", 1)[0]
CACHE = f"{HERE}/dtc_gpc_raw.json"

# Наше семейство -> файл исполнения блока в GPC. Взято выборкой по адресу на шине
# при VEHID = 28 (платформа B7); датчик дождя у нас по каталогу B78, как и BSI.
BLOCKS = {
    "BSI2010": "BMF_BSI2010_376_B7.xml",
    "V46_32": "INJ_V46_32_493_B7.xml",
    "ESP81": "ABRASR_ESP81_116_B7.xml",
    "GEP": "DIRECTN_GEP_101_B7.xml",
    "BSM_2010": "BSM_BSM_2010_349_B7.xml",
    "COM2008P": "HDC_COM2008P_345_B7.xml",
    "RBG_UDS": "AIRBAG_RBG_UDS_344_B7.xml",
    "COMBINE_UDS": "COMBINE_COMBINE_UDS_368_B7.xml",
    "AAS_UDS": "AAS_AAS_UDS_360_B7.xml",
    "BPGA2010": "BPGA_BPGA2010_370_B7.xml",
    "EMF_C_UDS": "AFFICHEUR_EMF_C_UDS_365_B7.xml",
    "FMUX": "FMUX_FMUX_346_B7.xml",
    "CDPL_UDS_HELLA": "CPL_CDPL_UDS_HELLA_502_B78.xml",
}

SQL = """
SET LIST OFF;
SELECT DISTINCT d.DTCCODE || '|' || COALESCE(d.DTCLABEL, '-') AS R
FROM ECU e
JOIN I_ECUDTC i ON i.I_ECUGRPDTCID = e.I_ECUGRPDTCID
JOIN DTC d ON d.DTCID = i.DTCID
WHERE e.ECUEDOFILENAME = '{edo}' AND d.DTCCODE IS NOT NULL;
"""

SYSTEM = {0: "P", 1: "C", 2: "B", 3: "U"}
REF = re.compile(r"@P(\d+)-POLUXDATA")


def code_of(raw):
    """Сырой код PSA "9137" -> "B1137" по SAE J2012."""
    try:
        v = int(raw, 16)
    except ValueError:
        return None
    return (f"{SYSTEM[(v >> 14) & 3]}{(v >> 12) & 3}"
            f"{(v >> 8) & 0xF:X}{v & 0xFF:02X}")


def fetch():
    """Семейство -> {код: сырая метка со ссылками}. Кэшируется."""
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    res = {}
    for fam, edo in BLOCKS.items():
        out = run([f"{HERE}/diagbox-sql.sh", "GPC", SQL.format(edo=edo)],
                  capture_output=True, timeout=1800).stdout.decode("cp1252", "replace")
        got = {}
        for line in out.splitlines():
            parts = line.strip().split("|", 1)
            if len(parts) != 2:
                continue
            code = code_of(parts[0].strip())
            lab = parts[1].strip()
            if code and lab and lab != "-":
                got[code] = lab
        res[fam] = got
        print(f"  {fam:16} {edo:34} кодов {len(got)}", file=sys.stderr)
    json.dump(res, open(CACHE, "w"), ensure_ascii=False)
    return res


def resolve(label, dico):
    """Метка со ссылками -> читаемый текст.

    В метке бывает несколько ссылок и служебные знаки: @T завершает вставку,
    @\\+ склеивает части. Незнакомые знаки убираем, но текст ссылок не выдумываем:
    неразрешённая ссылка остаётся как есть и видна.
    """
    def sub(m):
        try:
            return dico[int(m.group(1))]
        except (IndexError, KeyError, ValueError):
            return m.group(0)
    s = REF.sub(sub, label)
    s = s.replace("@\\+", " ").replace("@T", " ")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s


def main():
    raw = fetch()
    if "--raw" in sys.argv:
        for fam, codes in raw.items():
            for c, lab in sorted(codes.items()):
                print(f"{fam}:{c}\t{lab}")
        return 0

    sys.path.insert(0, HERE)
    import dico as dico_mod
    try:
        d = dico_mod.open_dico("POLUXDATA")
    except (FileNotFoundError, OSError) as e:
        print(f"нет словаря POLUXDATA: {e}\n"
              f"смонтируй образ (sudo ./mount-diagbox.sh) - копия сохранится локально",
              file=sys.stderr)
        return 1

    print('"""Описания кодов неисправностей из GPC.FDB по нашим блокам.\n\n'
          "СГЕНЕРИРОВАН gen_dtc_gpc.py. Текст сразу русский - словарь POLUXDATA\n"
          'русскоязычный, переводить ничего не нужно.\n"""')
    print("\nDTC_GPC = {")
    left = 0
    for fam in sorted(raw):
        for c, lab in sorted(raw[fam].items()):
            txt = resolve(lab, d)
            if "-POLUXDATA" in txt:
                left += 1
            print(f"    {fam + ':' + c!r}: {txt!r},")
    print("}")
    print(f"неразрешённых ссылок: {left}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
