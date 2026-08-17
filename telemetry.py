"""
Телеметрия Citroen C4 через Lexia 3: опрос параметров BSI и вывод для Grafana.

Читать можно и на заведённом моторе, и на ходу - это пассивные запросы, ничем
не отличающиеся от того, что делает DiagBox. Гейт по двигателю есть только у
актуаторов (2F), к чтению (22) он отношения не имеет.

Производительность, измерено на машине: одна транзакция ~72 мс, в один запрос
влезает до 10 DID. То есть ~139 параметров в секунду суммарно: 10 штук с частотой
14 Гц, либо сотня с частотой 1.4 Гц. Для графиков с запасом.

Вывод:
  --csv FILE     построчно в CSV
  --influx URL   line protocol в InfluxDB/VictoriaMetrics (--db, --measurement)
  по умолчанию   просто в терминал

Примеры:
  ./.venv/bin/python telemetry.py --list rpm
  ./.venv/bin/python telemetry.py rpm speed volt --hz 2
  ./.venv/bin/python telemetry.py --preset engine --csv drive.csv
  ./.venv/bin/python telemetry.py --preset engine --influx http://localhost:8086
"""

import argparse
import csv
import logging
import sys
import time
import urllib.request

from did_catalog import BY_DID, CATALOG
from storage import Store
from lexia_proto import (Lexia, parse_multi, plan_batches, read_did,
                         read_multi_frame)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Короткие имена для ходовых параметров, чтобы не писать мнемонику целиком.
ALIASES = {
    "rpm":    0xDBA8,   # обороты, x0.125
    "speed":  0xDB61,   # скорость, x0.01 км/ч
    "volt":   0xDA44,   # питание BSI, x0.001 В
    "key":    0xDD18,   # положение ключа
    "gmp":    0xDD03,   # состояние силового агрегата
    "gear":   0xDB60,   # положение селектора
    "oiltemp": 0xDB82,  # температура масла
    "batt_soc": 0xDA21, # заряд АКБ
}

PRESETS = {
    "engine": ["rpm", "speed", "volt", "key", "gmp", "oiltemp"],
    "lights": [0xD870, 0xD871, 0xD872, 0xD873, 0xD874, 0xD875],
}

# Режим поездки: быстро меняющееся опрашиваем часто, всё остальное - редко.
# Полный снимок 316 параметров занимает ~4.4 с, для оборотов и скорости это
# слишком грубо, поэтому две разные частоты.
DRIVE_HOT = ["rpm", "speed", "volt", "oiltemp", "key", "gmp", "gear"]
DRIVE_FULL_EVERY = 60.0  # секунд между полными снимками


def did_length(did: int) -> int:
    """Сколько байт данных у DID - максимум по всем его полям из каталога."""
    entries = BY_DID.get(did)
    if not entries:
        return 1
    return max((e["sb"] - 4) + e["ln"] for e in entries) or 1


# Границы правдоподобия по единицам измерения: отсекают мусор, который прошёл
# мимо проверки на маркеры. Проценты больше 100 - это не измерение.
PLAUSIBLE = {
    "%": (0.0, 100.0),
    "V": (0.0, 20.0), "Volt(s)": (0.0, 20.0),
    "°C": (-50.0, 200.0), "°": (-720.0, 720.0), "°/s": (-200.0, 200.0),
    "km/h": (0.0, 300.0),
    "Rpm": (0.0, 8000.0),
    "bar": (0.0, 40.0),
    "litres": (0.0, 100.0), "Litres": (0.0, 100.0),
    "litres/100 km": (0.0, 50.0), "L/100 Kms": (0.0, 50.0),
    "Ohms": (0.0, 100000.0), "mOhms": (0.0, 10000.0),
}


def decode(did: int, raw: bytes):
    """Привести сырые байты к физической величине по первому полю каталога.

    Возвращает (None, unit), если значение недоступно. PSA помечает отсутствие
    данных максимальным сырым кодом или на единицу меньше: 0xFF/0xFE для одного
    байта, 0xFFFF/0xFFFE для двух. Без этой проверки напряжение АКБ показывало
    65.5 В (0xFFFE x 0.001), заряд 254% (0xFE), уровень масла 255% (0xFF).
    """
    entries = BY_DID.get(did)
    if not entries or not raw:
        return (raw.hex() if raw else None), ""
    e = entries[0]
    ln = e["ln"] or len(raw)
    val = int.from_bytes(raw[:ln] or raw, "big")
    unit = e["unit"]

    is_bitfield = e["mask"] is not None
    if not is_bitfield:
        full = (1 << (8 * ln)) - 1
        smax = (1 << (8 * ln - 1)) - 1      # максимум знакового: 0x7F, 0x7FFF
        # Маркеры "нет значения" проверяем только у измеряемых величин: у флагов
        # и счётчиков 0xFF бывает законным. Знаковые величины используют свой
        # маркер - 0x7FFF: скорость рыскания показывала 3276.7 °/s (0x7FFF x 0.1),
        # что физически невозможно, ~9 оборотов машины в секунду.
        if unit and val in (full, full - 1, smax, smax + 1):
            return None, unit

    if is_bitfield:
        try:
            val = (val & int(e["mask"])) >> int(e["shift"] or 0)
        except (TypeError, ValueError):
            pass

    val = val * (e["factor"] or 1.0) + e.get("offset", 0.0)
    lo, hi = PLAUSIBLE.get(unit, (None, None))
    if lo is not None and not (lo <= val <= hi):
        return None, unit
    return round(val, 3), unit


def resolve(tokens):
    """Разобрать аргументы: короткое имя, 0xXXXX, или подстрока мнемоники."""
    out = []
    for t in tokens:
        if t in ALIASES:
            out.append(ALIASES[t])
            continue
        try:
            out.append(int(t, 16) if t.lower().startswith("0x") else int(t, 16))
            continue
        except ValueError:
            pass
        hits = [e["did"] for e in CATALOG if t.upper() in e["name"]]
        if hits:
            out.append(hits[0])
        else:
            log.warning(f"не нашёл параметр: {t}")
    return out


def name_of(did: int) -> str:
    e = BY_DID.get(did)
    return e[0]["name"] if e else f"DID_{did:04X}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("params", nargs="*", help="имена/DID параметров")
    ap.add_argument("--preset", choices=sorted(PRESETS), help="готовый набор")
    ap.add_argument("--all", action="store_true",
                    help="все параметры, реально существующие на этой машине (live_dids.py)")
    ap.add_argument("--hz", type=float, default=1.0, help="частота опроса")
    ap.add_argument("--csv", help="писать в CSV")
    ap.add_argument("--sqlite", help="писать в локальный SQLite (буфер под sync.py)")
    ap.add_argument("--influx", help="URL InfluxDB, напр. http://localhost:8086")
    ap.add_argument("--db", default="car", help="имя базы для InfluxDB")
    ap.add_argument("--measurement", default="c4", help="measurement для InfluxDB")
    ap.add_argument("--list", metavar="ПОДСТРОКА", help="найти параметры по имени и выйти")
    args = ap.parse_args()

    if args.list:
        q = args.list.upper()
        found = [e for e in CATALOG if q in e["name"]]
        print(f"найдено {len(found)}:")
        for e in found[:60]:
            print(f"  0x{e['did']:04X}  {e['name']:<58} {e['unit']}")
        return 0

    if args.all:
        from live_dids import LIVE
        tokens = [f"0x{d:04X}" for d, _ in LIVE]
    else:
        tokens = args.params or (PRESETS[args.preset] if args.preset else [])
    if not tokens:
        print("Укажи параметры или --preset. Поиск: --list VITESSE")
        print("Короткие имена:", ", ".join(sorted(ALIASES)))
        return 2
    dids = resolve([t if isinstance(t, str) else f"0x{t:04X}" for t in tokens])
    if not dids:
        return 2
    lengths = {d: did_length(d) for d in dids}

    lex = Lexia()
    if not lex.connect():
        return 1
    try:
        lex.drain()
        if not lex.device_boot(verbose=False):
            log.error("Рукопожатие не прошло: Lexia в OBD? зажигание?")
            return 3
        lex.boot(verbose=False)
        log.info(f"Связь есть. Опрашиваю {len(dids)} параметров с частотой {args.hz} Гц.")

        writer = None
        fh = None
        store = Store(args.sqlite) if args.sqlite else None
        if args.csv:
            fh = open(args.csv, "w", newline="", buffering=1)
            writer = csv.writer(fh)
            writer.writerow(["ts"] + [name_of(d) for d in dids])

        period = 1.0 / args.hz if args.hz > 0 else 0
        try:
            while True:
                t0 = time.time()
                values = {}
                # пачки ограничены и числом DID, и длиной ответа
                for chunk in plan_batches(dids, lengths):
                    payload, _ = lex.transact(read_multi_frame(chunk))
                    values.update(parse_multi(payload, lengths))
                # добираем поштучно те, что не пришли в пачке
                for d in dids:
                    if d not in values:
                        p2, _ = lex.transact(read_multi_frame([d]))
                        values.update(parse_multi(p2, lengths))

                row, shown = [], []
                for d in dids:
                    val, unit = decode(d, values.get(d, b""))
                    row.append(val)
                    shown.append(f"{name_of(d)[:26]}={val}{unit}")
                ts = time.time()

                if writer:
                    writer.writerow([f"{ts:.3f}"] + row)
                if store:
                    entries = BY_DID.get(0) or []
                    samples = []
                    for i, d in enumerate(dids):
                        e = BY_DID.get(d)
                        samples.append((d, name_of(d), e[0]["unit"] if e else "", row[i]))
                    store.write(samples, ts=ts)
                if args.influx:
                    fields = ",".join(
                        f"{name_of(d)}={row[i]}" for i, d in enumerate(dids)
                        if isinstance(row[i], (int, float))
                    )
                    if fields:
                        line = f"{args.measurement} {fields} {int(ts * 1e9)}"
                        try:
                            urllib.request.urlopen(
                                f"{args.influx}/write?db={args.db}",
                                data=line.encode(), timeout=2)
                        except Exception as e:
                            log.warning(f"InfluxDB: {e}")
                if not writer and not args.influx and not args.sqlite:
                    print(" | ".join(shown))

                dt = period - (time.time() - t0)
                if dt > 0:
                    time.sleep(dt)
        except KeyboardInterrupt:
            log.info("Остановлено.")
        finally:
            if fh:
                fh.close()
            if store:
                log.info(f"SQLite: {store.stats()}")
                store.close()
    finally:
        lex.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
