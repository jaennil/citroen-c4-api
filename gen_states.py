"""
Тексты состояний параметров из DSD.FDB образа DiagBox.

Зачем. У параметров-перечислений (положение ключа, состояние силового агрегата,
режим двигателя) в каталоге лежат голые номера, и на дашборде они показываются
числами. Тексты есть в базе DiagBox прямо рядом с параметром.

Формат записи: <МНЕМОНИКА>_<XX> <длина> <текст> ... <длина> <XX>, где XX - значение
в шестнадцатеричном виде. Пример: MP_ETAT_GMP_04 -> "Tournant", значение 04.

ВАЖНО: слепо верить этому нельзя. Каталог DiagBox описывает семейство блоков, а не
конкретно нашу машину, и уже дважды врал (единицы, порядок байт, температура масла
1225 °C). Поэтому скрипт только ДОКЛАДЫВАЕТ, а сверка с замерами - отдельным шагом,
см. --check.

    ./.venv/bin/python gen_states.py            # все найденные состояния
    ./.venv/bin/python gen_states.py --check    # сверить с enums.py и нашими данными
"""

import re
import sys

DSD = "/mnt/diagbox/AWRoot/dtrd/comm/data/DSD.FDB"
# <МНЕМОНИКА>_<HH> <len><len><00> <текст французский> ... <04><02><00> <HH>
REC = re.compile(
    rb"((?:MP|CA)_[A-Z0-9_]+?)_([0-9A-F]{2})"          # мнемоника и значение в имени
    rb"[\x00-\xff]{2}"
    rb"(.)(.)\x00([ -~\xa0-\xff]{2,80}?)"              # длина и текст
    rb"\x80\x00\x80\x00.\x00\x04\x02\x00([0-9A-F]{2})"  # и то же значение отдельным полем
)


def states(blob):
    out = {}
    for m in REC.finditer(blob):
        name, v1, _, ln, txt, v2 = m.groups()
        if v1 != v2:
            continue                      # значение в имени и в поле должны совпасть
        if len(txt) != ln[0]:
            continue                      # длина не сошлась - запись разобрана неверно
        try:
            t = txt.decode("cp1252")
        except Exception:
            continue
        out.setdefault(name.decode(), {})[int(v1, 16)] = t
    return out


def main():
    try:
        blob = open(DSD, "rb").read()
    except FileNotFoundError:
        print("образ не смонтирован: sudo ./mount-diagbox.sh", file=sys.stderr)
        return 1
    st = states(blob)
    print(f"# параметров с состояниями: {len(st)}", file=sys.stderr)
    if "--check" in sys.argv:
        import enums
        for full, ours in enums.ENUMS.items():
            mn = full.split(":")[-1]
            theirs = st.get(mn)
            if not theirs:
                continue
            same = {k: v for k, v in ours.items() if k in theirs}
            print(f"\n{full}")
            print(f"   наши (по замерам): {ours}")
            print(f"   DiagBox:           {theirs}")
            miss = [k for k in ours if k not in theirs]
            if miss:
                print(f"   РАСХОЖДЕНИЕ: наших значений {miss} в базе нет")
        return 0
    print('"""Состояния параметров из DSD.FDB. Сгенерировано gen_states.py, НЕ проверено."""')
    print("\nSTATES_FR = {")
    for k in sorted(st):
        print(f"    {k!r}: {st[k]!r},")
    print("}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
