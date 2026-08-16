"""
Шаг 3: Анализ дампа CAN
Сравнивает два дампа (фары ВЫКЛ vs фары ВКЛ) и показывает разницу.

Использование:
  1. Запусти сниффер, подожди 10 сек с выключенными фарами, останови -> сохрани как off.log
  2. Запусти сниффер, включи фары, подожди 10 сек, останови -> сохрани как on.log
  3. python 03_analyze_dump.py off.log on.log
"""

import sys
import logging
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def parse_dump(filename: str) -> dict[str, set[str]]:
    """Парсим дамп: для каждого CAN ID собираем уникальные значения данных."""
    id_data = defaultdict(set)
    with open(filename) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            parts = line.split(",", 1)
            if len(parts) < 2:
                continue
            raw = parts[1].strip()
            if len(raw) < 4:
                continue
            can_id = raw[:3]
            data = raw[3:]
            id_data[can_id].add(data)
    return dict(id_data)


def main():
    if len(sys.argv) < 3:
        log.error("Использование: python 03_analyze_dump.py <dump_off.log> <dump_on.log>")
        sys.exit(1)

    file_off = sys.argv[1]
    file_on = sys.argv[2]

    log.info(f"Анализируем:")
    log.info(f"  ВЫКЛ: {file_off}")
    log.info(f"  ВКЛ:  {file_on}")

    data_off = parse_dump(file_off)
    data_on = parse_dump(file_on)

    all_ids = sorted(set(list(data_off.keys()) + list(data_on.keys())))

    log.info(f"\nВсего уникальных CAN ID: {len(all_ids)}")
    log.info("=" * 60)

    # Ищем ID которые изменились
    changed = []
    new_ids = []

    for can_id in all_ids:
        off_values = data_off.get(can_id, set())
        on_values = data_on.get(can_id, set())

        if can_id not in data_off and can_id in data_on:
            new_ids.append((can_id, on_values))
        elif off_values != on_values:
            # Есть разница в данных
            only_on = on_values - off_values
            if only_on:
                changed.append((can_id, off_values, on_values, only_on))

    if new_ids:
        log.info("\n>>> НОВЫЕ ID (появились только при включении):")
        for can_id, values in new_ids:
            log.info(f"  CAN ID 0x{can_id}:")
            for v in sorted(values):
                log.info(f"    данные: {v}")

    if changed:
        log.info("\n>>> ИЗМЕНИВШИЕСЯ ID (данные отличаются):")
        for can_id, off_vals, on_vals, only_on in changed:
            log.info(f"  CAN ID 0x{can_id}:")
            log.info(f"    ВЫКЛ: {sorted(off_vals)[:5]}{'...' if len(off_vals) > 5 else ''}")
            log.info(f"    ВКЛ:  {sorted(on_vals)[:5]}{'...' if len(on_vals) > 5 else ''}")
            log.info(f"    НОВОЕ при ВКЛ: {sorted(only_on)[:5]}")

    if not new_ids and not changed:
        log.info("\nРазницы не найдено. Попробуй снять дампы дольше или проверь подключение.")

    log.info("\nГотово! Теперь мы знаем какие CAN ID отвечают за фары.")


if __name__ == "__main__":
    main()
