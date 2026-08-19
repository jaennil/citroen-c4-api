"""
Генератор ecu_catalog.py - каталога параметров ВСЕХ блоков этой машины.

Опознание блоков сделано по дампу переключения (см. id_ecus.py и разбор
switch.log): у каждого блока прочитан его номер детали PSA через 22F080 или
2180, а номер найден в sw_mapping.json базы DiagBox. Двигатель и ABS номерами
не опознались - в том соответствии их нет - и определены по различающему
запросу: 2182 есть у рода MEV17 и приходил только на 0x6A8, а у семейств ABS
его нет вовсе.

Модель доступа у блоков разная, и это главное, что надо помнить:

  * UDS-блоки (BSI, BSM, подрулевой, щиток...) - запрос 22 <DID>, на каждый
    параметр свой DID, в один запрос влезает до 10;
  * KWP-блоки (двигатель, ABS, насос) - запрос 21 <LID> [аргументы], и ОДИН
    запрос возвращает целый блок данных, из которого параметры берутся по
    смещению. У двигателя 313 параметров всего за 15 запросов.

Смещение: индекс в ответе = start_byte - 1. Правило единое для обоих видов,
проверено на живом ответе 2180 блока 0x6A8, где поле с start_byte=3 и длиной 5
дало 98 04 43 62 80 - номер детали PSA в BCD.

Актуаторные группы (VA*) в каталог НЕ попадают: это тесты исполнительных
механизмов, а не чтение, и запускать их скриптом заодно с опросом нельзя.

    ./.venv/bin/python gen_catalog.py > ecu_catalog.py
"""

import json
import os
import sys

DB = "/home/jaennil/life/citroen/diag-server/ecu_groups_jsons"

# адрес -> (род блока, что это по-русски, как опознан)
ECUS = [
    (0x752, 0x652, "BSI2010",          "кузовной компьютер BSI",      "номер 9664992380, плюс отдаёт VIN"),
    (0x747, 0x647, "BSM_2010",         "блок реле моторного отсека",  "номер 9664998880"),
    (0x742, 0x642, "COM2008P",         "подрулевой переключатель",    "номер 9665666777"),
    (0x75F, 0x65F, "COMBINE_UDS",      "щиток приборов",              "номер 9665731480"),
    (0x744, 0x644, "RBG_UDS",          "подушки безопасности",        "номер 9806788680"),
    (0x765, 0x665, "EMF_C_UDS",        "многофункциональный дисплей", "номер 9804496980"),
    (0x75D, 0x65D, "AAS_UDS",          "парктроник",                  "номер 9800409680"),
    (0x730, 0x710, "CDPL_UDS_HELLA",   "датчик дождя и света",        "номер 9665925480"),
    (0x731, 0x711, "BPGA2010",         "модуль двери",                "номер 9665232380"),
    (0x77B, 0x67B, "FMUX",             "панель управления",           "номер 9804078277"),
    (0x6B5, 0x695, "GEP",              "электронасос",                "номер 9803319180"),
    (0x6C8, 0x628, "VCI",              "VCI",                         "номер 9672044777"),
    # Двигатель: сперва каталог был взят MEV17_4_2 по подсказке экрана DiagBox, и это
    # оказалось НЕВЕРНО. Решил дело номер детали, прочитанный с самого блока:
    # 21 80 на 0x6A8 отдаёт 9804436280, а это Valeo V46, не Bosch MEV17.4.2. Сходится
    # и со словами владельца - двигатель EC5, развитие TU5, а не EP6C/5FS. Признак
    # чужого каталога был на виду: температура масла декодировалась как 1225 °C.
    # Осторожно: каталоги VD46 читаются по UDS (22 D4xx), а KWP-запросы 21 80/21 FE
    # блок тоже понимает - это устаревшая идентификация, которую отвечают многие ЭБУ.
    # Поэтому из 14 запросов MEV17 отвечали ровно 7 общих, а специфичные молчали.
    # Семейство подобрано по отпечатку живых запросов, а не по имени: у Valeo много
    # вариантов, и VD46 оказался не тем - он читается по UDS (22 D4xx), а наш блок
    # на UDS-вход молчит и отвечает только по KWP. V46_32_B7 совпал с 5 из 7
    # запросов, которые блок реально отработал, и он же под платформу B7.
    (0x6A8, 0x688, "V46_32",            "двигатель Valeo V46 (EC5)",   "номер 9804436280 с блока, семейство по отпечатку запросов"),
    (0x6AD, 0x68D, "ESP81",            "ABS/ESP",                     "KWP 21C08001 без 2182, адрес по документации PSA"),
]

# Порядок предпочтения файла: платформа этой машины B7 (путь в DiagBox
# Vehicle\B7\INJ), дальше близкие подварианты.
PREFER = ["_B7_", "_B71", "_B73", "_B78", "_B75C", "_B7"]


# Файлы с этими метками - особые случаи, а не обычная прошивка: аварийный режим,
# стендовый вариант, страновые сборки. Берём их только если больше нечего.
AVOID = ("DEGRAD", "MESS", "MODE_", "Chine", "CHINE", "Russie", "RUSSIE", "TLCD")


def pick_file(fam):
    names = [f[:-5] for f in os.listdir(DB)
             if f.startswith(fam + "_") and f.endswith(".json")]
    if not names:
        return None
    def rank(n):
        return (any(a in n for a in AVOID), len(n), n)
    for tag in PREFER:
        cand = sorted((n for n in names if tag in n), key=rank)
        if cand:
            return cand[0]
    return sorted(names, key=rank)[0]


def num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def integer(x, default=None):
    try:
        return int(str(x), 0)
    except (TypeError, ValueError):
        return default


def params_of(path):
    d = json.load(open(path))
    out, seen = [], set()
    for gname, g in d.items():
        if not isinstance(g, dict) or "sensors" not in g:
            continue
        if gname.startswith("VA"):        # актуаторные тесты - не читаем
            continue
        for s in g["sensors"]:
            if not isinstance(s, dict):
                continue
            m, req = s.get("mnemonic"), s.get("req_frame_hex")
            if not m or not req:
                continue
            sb = integer(s.get("start_byte"))
            if sb is None:
                continue
            key = (req.upper(), sb, m)
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(
                name=m, req=req.upper(), sb=sb,
                ln=integer(s.get("byte_length"), 1) or 1,
                factor=num(s.get("factor"), 1.0) or 1.0,
                offset=num(s.get("offset"), 0.0),
                unit=(s.get("unit") or ""),
                mask=integer(s.get("bit_mask")),
                shift=integer(s.get("bit_shift")),
                group=gname,
            ))
    return out


def main():
    print('"""')
    print("Каталог параметров всех блоков машины. СГЕНЕРИРОВАН gen_catalog.py.")
    print("")
    print("ECUS[(tx, rx)] = dict(fam, ru, how, file, params)")
    print("params - список dict(name, req, sb, ln, factor, offset, unit, mask, shift, group)")
    print("")
    print("Значение = сырое * factor + offset, данные с индекса sb-1 в ответе ЭБУ.")
    print("Поле endian из базы СОЗНАТЕЛЬНО не переносится: на BSI оно врёт")
    print("(LittleEndian давал запас хода 27650 км), на проводе big-endian.")
    print('"""')
    print()
    print("ECUS = {")
    total = 0
    for tx, rx, fam, ru, how in ECUS:
        f = pick_file(fam)
        if not f:
            print(f"    # {ru}: файла для рода {fam} в базе нет", file=sys.stderr)
            continue
        ps = params_of(os.path.join(DB, f + ".json"))
        total += len(ps)
        reqs = sorted({p["req"] for p in ps})
        print(f"    # {ru} - {len(ps)} параметров за {len(reqs)} запросов")
        print(f"    (0x{tx:03X}, 0x{rx:03X}): dict(")
        print(f"        fam={fam!r}, ru={ru!r}, how={how!r}, file={f!r},")
        print(f"        requests={reqs!r},")
        print("        params=[")
        for p in sorted(ps, key=lambda x: (x["req"], x["sb"])):
            print(f"            {p!r},")
        print("        ]),")
        print(f"{fam:<18} {f:<28} параметров {len(ps):>4} запросов {len(reqs):>3}",
              file=sys.stderr)
    print("}")
    print()
    print(f"# всего параметров по всем блокам: {total}")
    print(f"ИТОГО: {total} параметров", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
