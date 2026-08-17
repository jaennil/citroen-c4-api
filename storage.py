"""
Локальный буфер телеметрии в SQLite.

Ноутбук - сборщик, а не архив: пишем всегда, даже когда сети нет, а потом
sync.py досылает накопленное в Postgres кластера. Поэтому у каждой строки есть
флаг synced, и досылка идемпотентна - пропала связь на неделю, вернулся, догналось.

Справочник параметров вынесен в отдельную таблицу: хранить имя в каждой строке
при сотнях миллионов значений расточительно.
"""

import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS param (
    id    INTEGER PRIMARY KEY,
    did   INTEGER NOT NULL UNIQUE,
    name  TEXT NOT NULL,
    unit  TEXT,
    label TEXT              -- человекочитаемое название, см. ru_labels.py
);
CREATE TABLE IF NOT EXISTS reading (
    id       INTEGER PRIMARY KEY,
    ts       REAL NOT NULL,
    param_id INTEGER NOT NULL REFERENCES param(id),
    value    REAL NOT NULL,
    synced   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reading_unsynced ON reading(id) WHERE synced = 0;
CREATE INDEX IF NOT EXISTS idx_reading_param_ts ON reading(param_id, ts);
"""


class Store:
    def __init__(self, path="car.db"):
        self.db = sqlite3.connect(path)
        # WAL: запись не блокирует чтение, и переживает падение процесса
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._ids = {}

    def param_id(self, did: int, name: str, unit: str = "") -> int:
        if did in self._ids:
            return self._ids[did]
        from ru_labels import label as ru_label
        lab = ru_label(did, name)
        cur = self.db.execute("SELECT id FROM param WHERE did=?", (did,))
        row = cur.fetchone()
        if row is None:
            cur = self.db.execute(
                "INSERT INTO param(did, name, unit, label) VALUES (?,?,?,?)",
                (did, name, unit, lab))
            pid = cur.lastrowid
        else:
            pid = row[0]
            # дозаполняем ярлык у баз, созданных до его появления
            self.db.execute("UPDATE param SET label=? WHERE id=? AND (label IS NULL OR label='')",
                            (lab, pid))
        self._ids[did] = pid
        return pid

    def write(self, samples, ts=None):
        """samples: [(did, name, unit, value), ...]"""
        ts = ts if ts is not None else time.time()
        rows = [(ts, self.param_id(d, n, u), float(v))
                for d, n, u, v in samples if isinstance(v, (int, float))]
        self.db.executemany(
            "INSERT INTO reading(ts, param_id, value) VALUES (?,?,?)", rows)
        self.db.commit()
        return len(rows)

    def unsynced(self, limit=5000):
        return self.db.execute(
            "SELECT r.id, r.ts, p.did, p.name, p.unit, r.value, p.label "
            "FROM reading r JOIN param p ON p.id = r.param_id "
            "WHERE r.synced = 0 ORDER BY r.id LIMIT ?", (limit,)).fetchall()

    def mark_synced(self, ids):
        self.db.executemany("UPDATE reading SET synced=1 WHERE id=?",
                            [(i,) for i in ids])
        self.db.commit()

    def stats(self):
        total = self.db.execute("SELECT COUNT(*) FROM reading").fetchone()[0]
        pend = self.db.execute("SELECT COUNT(*) FROM reading WHERE synced=0").fetchone()[0]
        params = self.db.execute("SELECT COUNT(*) FROM param").fetchone()[0]
        return dict(total=total, pending=pend, params=params)

    def close(self):
        self.db.close()
