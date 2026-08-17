"""
Досылка накопленной телеметрии из локального SQLite в Postgres кластера.

Запускать когда есть связь с домом (туннель sish или домашняя сеть). Идемпотентно:
отправляет только строки с synced=0 и помечает их лишь после успешной вставки,
поэтому обрыв посреди передачи ничего не теряет и не задваивает.

Подключение задаётся переменной окружения:
    export CAR_PG="postgresql://car:пароль@localhost:5432/car"
    ./.venv/bin/python sync.py

Схема в Postgres создаётся автоматически при первом запуске (--init по умолчанию).
"""

import argparse
import logging
import os
import sys

import psycopg

from storage import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS param (
    id    smallserial PRIMARY KEY,
    did   integer NOT NULL UNIQUE,
    name  text NOT NULL,
    unit  text,
    label text            -- человекочитаемое название, см. ru_labels.py
);
ALTER TABLE param ADD COLUMN IF NOT EXISTS label text;
CREATE TABLE IF NOT EXISTS reading (
    ts       timestamptz NOT NULL,
    param_id smallint NOT NULL REFERENCES param(id),
    value    double precision NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reading_param_ts ON reading (param_id, ts DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reading_dedup ON reading (param_id, ts);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default="car.db")
    ap.add_argument("--dsn", default=os.environ.get("CAR_PG"),
                    help="строка подключения, либо переменная CAR_PG")
    ap.add_argument("--batch", type=int, default=5000)
    ap.add_argument("--dry-run", action="store_true", help="только показать, что бы отправилось")
    args = ap.parse_args()

    store = Store(args.sqlite)
    st = store.stats()
    log.info(f"локально: {st['total']} значений, {st['params']} параметров, "
             f"к отправке {st['pending']}")
    if not st["pending"]:
        log.info("отправлять нечего.")
        return 0
    if args.dry_run:
        for r in store.unsynced(10):
            log.info(f"  {r}")
        return 0
    if not args.dsn:
        log.error("не задано подключение: --dsn или переменная CAR_PG")
        return 2

    sent = 0
    with psycopg.connect(args.dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(PG_SCHEMA)
        conn.commit()
        while True:
            rows = store.unsynced(args.batch)
            if not rows:
                break
            with conn.cursor() as cur:
                # справочник параметров
                params = {(did, name, unit, lab)
                          for _, _, did, name, unit, _, lab in rows}
                cur.executemany(
                    "INSERT INTO param(did,name,unit,label) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (did) DO UPDATE SET label = EXCLUDED.label",
                    sorted(params))
                cur.execute("SELECT did, id FROM param")
                pid = dict(cur.fetchall())
                # сами значения; повтор по (param_id, ts) молча игнорируется
                cur.executemany(
                    "INSERT INTO reading(ts, param_id, value) "
                    "VALUES (to_timestamp(%s), %s, %s) ON CONFLICT DO NOTHING",
                    [(ts, pid[did], val) for _, ts, did, _, _, val, _ in rows])
            conn.commit()
            store.mark_synced([r[0] for r in rows])
            sent += len(rows)
            log.info(f"  отправлено {sent}...")

    log.info(f"готово, отправлено {sent} значений.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
