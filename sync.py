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

import json
import psycopg

from storage import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS param (
    id    serial PRIMARY KEY,
    did   integer NOT NULL UNIQUE,
    name  text NOT NULL,
    unit  text,
    label text            -- человекочитаемое название, см. ru_labels.py
);
ALTER TABLE param ADD COLUMN IF NOT EXISTS label text;
CREATE TABLE IF NOT EXISTS reading (
    ts       timestamptz NOT NULL,
    param_id integer NOT NULL REFERENCES param(id),
    value    double precision NOT NULL
);
-- Миграция 2026-09-11: id был smallserial, и счётчик упёрся в 32767 при 470
-- параметрах - INSERT ... ON CONFLICT DO UPDATE тратит значение nextval на КАЖДУЮ
-- строку при каждой досылке, даже когда ничего не вставляет. Досылка вставала с
-- SequenceGeneratorLimitExceeded, буфер рос. Расширяем тип один раз и дальше не
-- трогаем; таблица reading на 68 МБ переписывается за секунды.
DO $$
BEGIN
    IF (SELECT data_type FROM information_schema.columns
        WHERE table_name = 'param' AND column_name = 'id') = 'smallint' THEN
        ALTER TABLE reading ALTER COLUMN param_id TYPE integer;
        ALTER TABLE param ALTER COLUMN id TYPE integer;
        ALTER SEQUENCE param_id_seq AS integer MAXVALUE 2147483647;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_reading_param_ts ON reading (param_id, ts DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reading_dedup ON reading (param_id, ts);
CREATE TABLE IF NOT EXISTS event (
    id      serial PRIMARY KEY,
    ts      timestamptz NOT NULL,
    title   text NOT NULL,
    kind    text NOT NULL,
    details text,
    UNIQUE (ts, title)
);
CREATE TABLE IF NOT EXISTS norm_zone (
    name  text NOT NULL,       -- имя параметра, как в param.name
    title text NOT NULL,
    lo    double precision,    -- красный отрезок [lo, hi); NULL - без границы
    hi    double precision,
    zone  text NOT NULL,       -- подпись отрезка для сообщения алерта
    PRIMARY KEY (name, zone)
);
CREATE TABLE IF NOT EXISTS maintenance (
    item            text PRIMARY KEY,
    title           text NOT NULL,
    interval_km     integer,
    interval_months integer,
    last_ts         timestamptz,
    last_km         double precision,
    notes           text
);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default="car.db")
    ap.add_argument("--dsn", default=os.environ.get("CAR_PG"),
                    help="строка подключения, либо переменная CAR_PG")
    ap.add_argument("--batch", type=int, default=5000)
    ap.add_argument("--dry-run", action="store_true", help="только показать, что бы отправилось")
    ap.add_argument("--state", default=None,
                    help="файл водяного знака для ВТОРОГО получателя (локальный Postgres): "
                         "слать по id, флаг synced не трогать")
    args = ap.parse_args()

    store = Store(args.sqlite)
    st = store.stats()
    log.info(f"локально: {st['total']} значений, {st['params']} параметров, "
             f"к отправке {st['pending']}")
    if args.dry_run:
        for r in store.unsynced(10):
            log.info(f"  {r}")
        return 0
    if not args.dsn:
        log.error("не задано подключение: --dsn или переменная CAR_PG")
        return 2

    sent = 0
    with psycopg.connect(args.dsn) as conn:
        # Схема - при КАЖДОМ подключении, до проверки "есть ли что отправлять".
        # Иначе новая таблица (event для аннотаций Grafana) не появлялась в
        # Postgres, пока не накопится хоть одна строка, а дашборд уже ссылался
        # на неё и падал с "relation event does not exist".
        with conn.cursor() as cur:
            cur.execute(PG_SCHEMA)
        conn.commit()
        # красные зоны из norms.py - целиком, каждый раз: общее правило алертов в
        # Grafana читает их из этой таблицы, так что новая зона в norms.py становится
        # алертом сама, без правки правил
        from norms import red_zones
        with conn.cursor() as cur:
            cur.execute("DELETE FROM norm_zone")
            cur.executemany(
                "INSERT INTO norm_zone(name, title, lo, hi, zone) VALUES (%s,%s,%s,%s,%s)",
                [(z["name"], z["title"], z["lo"], z["hi"], z["zone"]) for z in red_zones()])
        conn.commit()
        # Ярлыки параметров - целиком, каждый раз. Они уточняются отдельно от замеров
        # (описания кодов выросли с 54 до 370 после разбора базы DiagBox), а обычная
        # досылка трогает только строки с новыми значениями: если новых замеров нет,
        # исправленный текст в кластер не попадёт никогда.
        labels = [(lab, did) for did, lab in store.db.execute(
            "SELECT did, label FROM param WHERE label IS NOT NULL AND label <> ''")]
        if labels:
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE param SET label = %s WHERE did = %s AND label IS DISTINCT FROM %s",
                    [(l, d, l) for l, d in labels])
            conn.commit()
            log.info(f"  ярлыков сверено: {len(labels)}")

        # расписание обслуживания - целиком, каждый раз: строк единицы, а правки
        # (сделали ТО, уточнили интервал) должны доезжать без отдельного флага
        rows = store.maintenance_all()
        if rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO maintenance(item,title,interval_km,interval_months,last_ts,last_km,notes) "
                    "VALUES (%s,%s,%s,%s,to_timestamp(%s),%s,%s) "
                    "ON CONFLICT (item) DO UPDATE SET title=EXCLUDED.title, "
                    "interval_km=EXCLUDED.interval_km, interval_months=EXCLUDED.interval_months, "
                    "last_ts=EXCLUDED.last_ts, last_km=EXCLUDED.last_km, notes=EXCLUDED.notes",
                    rows)
            conn.commit()
        state = {"reading": 0, "event": 0}
        if args.state and os.path.exists(args.state):
            with open(args.state) as f:
                state.update(json.load(f))
        if not args.state and not st["pending"] and not st.get("events"):
            log.info("схема на месте, отправлять нечего.")
            return 0
        while True:
            rows = store.after(state["reading"], args.batch) if args.state \
                else store.unsynced(args.batch)
            if not rows:
                break
            with conn.cursor() as cur:
                # справочник параметров
                params = {(did, name, unit, lab)
                          for _, _, did, name, unit, _, lab in rows}
                # Вставляем только НОВЫЕ did: ON CONFLICT DO UPDATE на всех сжигал
                # значение счётчика id на каждую строку при каждой досылке и за
                # три недели довёл smallserial до потолка. Ярлык обновляем
                # отдельно и только если он изменился.
                cur.execute("SELECT did, id FROM param")
                pid = dict(cur.fetchall())
                fresh = sorted(p for p in params if p[0] not in pid)
                if fresh:
                    cur.executemany(
                        "INSERT INTO param(did,name,unit,label) VALUES (%s,%s,%s,%s) "
                        "ON CONFLICT (did) DO NOTHING", fresh)
                    cur.execute("SELECT did, id FROM param")
                    pid = dict(cur.fetchall())
                cur.executemany(
                    "UPDATE param SET label = %s WHERE did = %s AND label IS DISTINCT FROM %s",
                    [(lab, did, lab) for did, _, _, lab in sorted(params)])
                # сами значения; повтор по (param_id, ts) молча игнорируется
                cur.executemany(
                    "INSERT INTO reading(ts, param_id, value) "
                    "VALUES (to_timestamp(%s), %s, %s) ON CONFLICT DO NOTHING",
                    [(ts, pid[did], val) for _, ts, did, _, _, val, _ in rows])
            conn.commit()
            if args.state:
                state["reading"] = rows[-1][0]
                with open(args.state, "w") as f:
                    json.dump(state, f)
            else:
                store.mark_synced([r[0] for r in rows])
            sent += len(rows)
            log.info(f"  отправлено {sent}...")

        # события - отдельной таблицей, тоже идемпотентно по (ts, title)
        evs = store.events_after(state["event"]) if args.state else store.unsynced_events()
        if evs:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO event(ts, title, kind, details) "
                    "VALUES (to_timestamp(%s), %s, %s, %s) ON CONFLICT DO NOTHING",
                    [(ts, t, k, d) for _, ts, t, k, d in evs])
            conn.commit()
            if args.state:
                state["event"] = evs[-1][0]
                with open(args.state, "w") as f:
                    json.dump(state, f)
            else:
                store.mark_events_synced([e[0] for e in evs])
            log.info(f"  событий отправлено {len(evs)}")

    log.info(f"готово, отправлено {sent} значений.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
