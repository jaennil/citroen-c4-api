"""
Проверка датчика температуры охлаждающей жидкости на живом двигателе.

Зачем отдельный инструмент. У блока Valeo V46 есть ДВА показания температуры ОЖ:

    MP_TEMPERATURE_D_EAU_MOTEUR_d   21 C0 80 01, байт 6   то, чем пользуется ЭБУ
    MP_TEMP_EAU_NON_CORRIGEE        21 C7 80 01, байт 11   сырое, без коррекции

Оба со смещением -50. Расхождение между ними и есть прямая улика на врущий
датчик: ЭБУ подменяет недостоверное показание расчётным и одновременно гонит
вентилятор, отсюда и жалоба "кулер молотит".

Заодно читаются коды неисправностей (KWP 17 FF 00). Код 0116 - это P0116,
цепь датчика температуры ОЖ.

Запускать ТОЛЬКО через обёртку, иначе драка со службой сбора за USB:

    ./with-lexia.sh ./.venv/bin/python coolant_check.py
    ./with-lexia.sh ./.venv/bin/python coolant_check.py --n 20 --period 1.0
"""

import argparse
import logging
import sys
import time

from ecu import enter
from lexia_proto import Lexia, read_frame

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

ENGINE = (0x6A8, 0x688)
OFFSET = -50.0


def dtcs(lex):
    """Коды неисправностей двигателя: ответ 57 <кол-во> <код-hi> <код-lo> <статус>..."""
    pl, _ = lex.transact(read_frame(b"\x17\xff\x00"), deadline=8.0)
    if not pl or pl[0] != 0x57:
        return None, []
    body = pl[2:]
    out = []
    for i in range(0, len(body) - 2, 3):
        code = (body[i] << 8) | body[i + 1]
        out.append((code, body[i + 2]))
    return pl[1], out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12, help="сколько замеров")
    ap.add_argument("--period", type=float, default=0.7, help="пауза между замерами, с")
    args = ap.parse_args()

    lex = Lexia()
    if not lex.connect():
        return 1
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            print("нет связи с машиной: зажигание включено?")
            return 3
        enter(lex, *ENGINE)

        n, codes = dtcs(lex)
        print(f"коды неисправностей двигателя: {n if n is not None else '?'}")
        for code, st in codes:
            note = "  <- P0116, цепь датчика температуры ОЖ" if code == 0x0116 else ""
            print(f"   {code:04X}  статус {st:02X}{note}")
        if not codes:
            print("   (кодов нет)")

        print("\n №   ОЖ,°C   без коррекции,°C   расхожд.   обороты")
        pairs = []
        for i in range(args.n):
            try:
                a, _ = lex.transact(read_frame(bytes.fromhex("21C08001")), deadline=6.0)
                b, _ = lex.transact(read_frame(bytes.fromhex("21C78001")), deadline=6.0)
            except Exception as e:
                print(f" {i + 1:>2}   сорвалось: {type(e).__name__}")
                break
            eau = a[5] + OFFSET if a and len(a) > 6 else None
            rpm = int.from_bytes(a[2:4], "big") if a and len(a) > 4 else None
            nc = b[10] + OFFSET if b and len(b) > 11 else None
            d = (eau - nc) if (eau is not None and nc is not None) else None
            pairs.append((eau, nc))
            print(f" {i + 1:>2}   {fmt(eau):>5}      {fmt(nc):>7}       {fmt(d):>6}    "
                  f"{rpm if rpm else '-':>5}")
            time.sleep(args.period)

        good = [(e, c) for e, c in pairs if e is not None and c is not None]
        if good:
            eaus = [e for e, _ in good]
            ncs = [c for _, c in good]
            diffs = [abs(e - c) for e, c in good]
            print(f"\nосновная:       мин {min(eaus):.0f}  макс {max(eaus):.0f}  "
                  f"разброс {max(eaus) - min(eaus):.0f}")
            print(f"без коррекции:  мин {min(ncs):.0f}  макс {max(ncs):.0f}  "
                  f"разброс {max(ncs) - min(ncs):.0f}")
            print(f"расхождение:    макс {max(diffs):.0f} °C")
            print()
            if max(diffs) >= 5:
                print("Показания расходятся - ЭБУ не доверяет датчику и подменяет значение.")
            elif max(eaus) - min(eaus) >= 10:
                print("Основное показание пляшет - похоже на плохой контакт или сам датчик.")
            else:
                print("Сейчас датчик читается ровно. Если код 0116 сохранён, вентилятор")
                print("держится защитным режимом по прошлой неисправности, а не по текущей.")
    finally:
        try:
            lex.disconnect()
        except Exception:
            pass
    return 0


def fmt(v):
    return "-" if v is None else f"{v:.0f}"


if __name__ == "__main__":
    sys.exit(main())
