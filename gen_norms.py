"""
Что DiagBox сама считает нормой - вытаскивается из подсказок каталога.

У параметра в базе DiagBox есть поле help_code, и в нём подставляемые числа:

    MP_TENSION_ALIMENTATION_CALCULATEUR_CONTROLE_MOTEUR   @P10264@\\*12@\\*15
    MP_REGIME_MOTEUR                                      @P28881@\\*200@\\*600

@Pnnnnn - ссылка на строку перевода (её текста у нас нет, таблица строк в образе
не разобрана), @\\*NNN - подставляемое число. Для пар вида 12 и 15 при единице V
смысл однозначен: это границы допустимого. Для 200 и 600 при rpm - нет, там
скорее значение и допуск, потому такие пары и не берутся на веру.

Скрипт только ДОКЛАДЫВАЕТ кандидатов, ничего не генерирует. Отобранные вручную
границы лежат в norms.py с указанием источника - потому что полуавтоматика тут
опаснее ручной работы: GEP:CA_TEMPERATURE_GEP выдаёт "110..118 °C", формально
проходит все проверки, а на машине насос показывает 47 °C. Это не норма, это
порог перегрева, и таблица бы соврала.

    ./.venv/bin/python gen_norms.py            # кандидаты
    ./.venv/bin/python gen_norms.py --all      # и отвергнутые, с причиной
"""

import json
import re
import sqlite3
import sys

from ecu_catalog import ECUS

DB = "/home/jaennil/life/citroen/diag-server/ecu_groups_jsons"

# Физически осмысленные пределы на единицу. Пара, вылезающая за них, разобрана
# неверно - либо это не диапазон, либо не та единица.
PLAUSIBLE = {
    "V": (0, 20), "°C": (-50, 200), "%": (0, 100), "rpm": (0, 8000),
    "Rpm": (0, 8000), "mV": (0, 6000), "km/h": (0, 300), "bar": (0, 40),
    "Ohms": (0, 1e5), "A": (0, 200), "mbar": (0, 4000),
}
NUM = re.compile(r"@\\\*(-?\d+(?:\.\d+)?)")


def observed(path="car.db"):
    """Средние по нашим замерам - ими проверяем, что диапазон не мимо."""
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        return {n: a for n, a in db.execute(
            "SELECT p.name, avg(v.value) FROM param p "
            "JOIN reading v ON v.param_id = p.id GROUP BY p.id")}
    except Exception:
        return {}


def candidates():
    """[(имя, ед., lo, hi, средняя_замеренная|None, причина_отказа|None)]"""
    obs = observed()
    out, seen = [], set()
    for f in sorted({i["file"] for i in ECUS.values()}):
        fam = next(i["fam"] for i in ECUS.values() if i["file"] == f)
        try:
            d = json.load(open(f"{DB}/{f}.json"))
        except Exception:
            continue
        for g in d.values():
            if not isinstance(g, dict):
                continue
            for s in g.get("sensors") or []:
                if not isinstance(s, dict) or not s.get("mnemonic"):
                    continue
                nums = [float(x) for x in NUM.findall(s.get("help_code") or "")]
                unit = (s.get("unit") or "").strip()
                if len(nums) != 2 or nums[0] == nums[1]:
                    continue
                key = s["mnemonic"] if fam == "BSI2010" else f"{fam}:{s['mnemonic']}"
                if key in seen:
                    continue
                seen.add(key)
                lo, hi = sorted(nums)
                why = None
                lim = PLAUSIBLE.get(unit)
                if lim is None:
                    why = f"единица {unit!r} без известных пределов"
                elif not (lim[0] <= lo < hi <= lim[1]):
                    why = f"вне физических пределов {lim} для {unit}"
                av = obs.get(key)
                if why is None and av is not None and not (lo * 0.5 <= av <= hi * 1.5):
                    why = f"наши замеры в среднем {av:.1f}, мимо диапазона"
                out.append((key, unit, lo, hi, av, why))
    return out


def main():
    show_all = "--all" in sys.argv
    rows = candidates()
    good = [r for r in rows if r[5] is None]
    print(f"параметров с парой чисел в подсказке: {len(rows)}, "
          f"прошли проверку: {len(good)}\n")
    for key, unit, lo, hi, av, why in sorted(rows):
        if why and not show_all:
            continue
        mark = "" if av is None else f"  (у нас в среднем {av:.1f})"
        print(f"{'  ОТКАЗ' if why else 'ok    '} {key[:56]:<58} "
              f"{lo:>8} .. {hi:<8} {unit}{mark}")
        if why:
            print(f"           причина: {why}")
    print("\nОтобранные границы вручную перенесены в norms.py: "
          "формально годная пара не значит норму,\nсмотри там оговорку про GEP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
