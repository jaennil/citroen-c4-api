"""
Расписание обслуживания: что и когда меняли, когда менять в следующий раз.

Данные лежат в car.db (таблица maintenance), досылаются в Postgres обычным
sync.py, а дашборд считает по ним остаток ресурса в км и месяцах против текущего
пробега BSI и красит: зелёный - в норме, жёлтый - 80 % ресурса, красный - пора или
дата последней замены неизвестна.

    ./.venv/bin/python maintenance.py --list
    ./.venv/bin/python maintenance.py --done oil --at "2026-09-06 15:00" --km 195826
    ./.venv/bin/python maintenance.py --set fuel_filter "Топливный фильтр" --km 40000
    ./.venv/bin/python maintenance.py --seed        # начальный набор позиций этой машины
"""

import argparse
import os
import sys
import time
from datetime import datetime

from storage import Store

HERE = os.path.dirname(os.path.abspath(__file__))

# Начальный набор. Интервалы - под эту машину: 195 тыс. км, 5W-40, российская пыль,
# а не заводские 20-30 тыс. Позиции без даты - NULL, дашборд покажет "пора".
SEED = [
    # item, title, km, months, notes
    ("oil", "Масло и масляный фильтр", 10000, 12,
     "Газпромнефть Premium N 5W-40 5 л + Knecht OX 339/2 D"),
    ("air_filter", "Воздушный фильтр", 20000, 24, "MANN C 4371/1"),
    ("cabin_filter", "Салонный фильтр", 15000, 12, "угольный, Exist E68609FCB"),
    ("spark_plugs", "Свечи зажигания", 30000, 36, "Bosch 0 242 229 797 x4, медь"),
    ("fuel_filter", "Топливный фильтр", 40000, None,
     "PSA 1567.C6 / Ufi 31.948.00 в запасе. Под задним сиденьем, быстросъёмы ломаются - "
     "менять по симптомам, сервис советует сначала заказать клипсы"),
    ("brake_fluid", "Тормозная жидкость", None, 24,
     "Brembo L 04 010 куплена. Дата последней замены неизвестна"),
    ("timing_belt", "Ремень ГРМ с роликами и насосом ОЖ", 120000, 120,
     "Gates KP15581XS, Авторусь 10 994. Дата неизвестна - если не найдётся, менять"),
    ("aux_belt", "Ремень навесного и натяжной ролик", 60000, 60,
     "Dayco 6PK1065 + INA 531086610. Неровный износ = перекос ролика"),
    ("coolant", "Антифриз", 100000, 60,
     "менян с радиатором ~10 тыс. км назад, дата неизвестна"),
    ("rear_brakes", "Задние тормоза", None, None,
     "по диагностике: звук на малой скорости, сервис рекомендовал обслужить"),
]


def fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "неизвестно"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "car.db"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seed", action="store_true", help="записать начальный набор позиций")
    ap.add_argument("--set", nargs=2, metavar=("ITEM", "TITLE"), help="добавить/изменить позицию")
    ap.add_argument("--km", type=float, help="интервал (с --set) или пробег (с --done)")
    ap.add_argument("--months", type=int, help="интервал в месяцах (с --set)")
    ap.add_argument("--done", metavar="ITEM", help="отметить выполненным")
    ap.add_argument("--at", help="когда сделано, локальное время; по умолчанию сейчас")
    ap.add_argument("--note", help="заметка")
    args = ap.parse_args()
    store = Store(args.db)

    if args.seed:
        for item, title, km, mo, notes in SEED:
            store.maintenance_set(item, title, km, mo, notes)
        print(f"записано позиций: {len(SEED)}")
    if args.set:
        store.maintenance_set(args.set[0], args.set[1], args.km, args.months, args.note or "")
        print(f"позиция {args.set[0]} записана")
    if args.done:
        ts = datetime.fromisoformat(args.at).timestamp() if args.at else time.time()
        if args.km is None:
            ap.error("--done требует --km (пробег на момент замены)")
        n = store.maintenance_done(args.done, ts, args.km, args.note)
        print(f"{args.done}: сделано {fmt_ts(ts)} на {args.km:.0f} км" if n else f"нет позиции {args.done}")
    if args.list or not (args.seed or args.set or args.done):
        for item, title, km, mo, lts, lkm, notes in store.maintenance_all():
            iv = " / ".join(x for x in ((f"{km} км" if km else ""), (f"{mo} мес" if mo else "")) if x) or "по состоянию"
            last = f"{fmt_ts(lts)}" + (f", {lkm:.0f} км" if lkm else "")
            print(f"{item:13s} {title:38s} каждые {iv:18s} последний раз: {last}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
