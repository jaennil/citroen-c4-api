"""
Записать событие с машиной: ТО, замену детали, заправку, заметку.

В Grafana это аннотация - вертикальная метка на всех графиках, по которой видно,
как изменилось поведение после замены масла, свечей и т.д. Событие пишется в
локальный car.db и досылается в Postgres обычным sync.py.

    ./.venv/bin/python event.py "ТО: свечи, фильтры, масло" --kind service \
        --at "2026-09-06 14:00" --details "Bosch 0242229797 x4, MANN C4371/1, ..."
    ./.venv/bin/python event.py "Заправка 40 л АИ-95" --kind fuel
    ./.venv/bin/python event.py --list

Время задаётся локальное; без --at берётся сейчас. Виды: service, repair, fuel, note.
"""

import argparse
import os
import sys
import time
from datetime import datetime

from storage import Store

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = ("service", "repair", "fuel", "note")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("title", nargs="?", help="короткое название, как подпись на графике")
    ap.add_argument("--kind", choices=KINDS, default="note")
    ap.add_argument("--at", help="когда, локальное время: 2026-09-06 14:00 (по умолчанию сейчас)")
    ap.add_argument("--details", default="", help="подробности: артикулы, пробег, кто делал")
    ap.add_argument("--db", default=os.path.join(HERE, "car.db"))
    ap.add_argument("--list", action="store_true", help="показать записанные события")
    args = ap.parse_args()

    store = Store(args.db)
    if args.list:
        rows = store.db.execute(
            "SELECT ts, kind, title, details, synced FROM event ORDER BY ts").fetchall()
        for ts, kind, title, details, synced in rows:
            when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            print(f"{when}  [{kind:7s}] {title}{'  - ' + details if details else ''}"
                  f"{'' if synced else '   (не отправлено)'}")
        if not rows:
            print("событий нет")
        return 0
    if not args.title:
        ap.error("нужно название события или --list")
    ts = datetime.fromisoformat(args.at).timestamp() if args.at else time.time()
    store.add_event(ts, args.title, args.kind, args.details)
    print(f"записано: {datetime.fromtimestamp(ts):%Y-%m-%d %H:%M} [{args.kind}] {args.title}")
    print("в Grafana попадёт после sync.py (или sync-now.sh)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
