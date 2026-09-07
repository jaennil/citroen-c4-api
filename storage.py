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
-- События с машиной: ТО, замены, заправки, заметки. Нужны, чтобы на графиках
-- видеть, как меняется поведение после замены масла, свечей и т.д. - в Grafana
-- это аннотации, вертикальные метки на всех панелях сразу. Досылаются в Postgres
-- тем же sync.py и так же идемпотентно.
CREATE TABLE IF NOT EXISTS event (
    id      INTEGER PRIMARY KEY,
    ts      REAL NOT NULL,
    title   TEXT NOT NULL,
    kind    TEXT NOT NULL,      -- service / repair / fuel / note
    details TEXT,
    synced  INTEGER NOT NULL DEFAULT 0
);
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

    def param_id(self, did: int, name: str, unit: str = "", label: str = "") -> int:
        """Строка параметра по ИМЕНИ, с выдачей синтетического did новым именам.

        Раньше опознание шло по did, и это годилось, пока читалась одна BSI. Как
        только добавились другие блоки, схема развалилась: у KWP-параметров
        двигателя своего did нет вовсе, поэтому им ставился 0 - а поле did
        объявлено UNIQUE, и все 189 параметров схлопнулись бы в одну строку. Плюс
        у разных блоков DID совпадают: 22D400 есть и у BSI, и у BSM, и это разные
        величины.

        Поэтому ключ - имя (у чужих блоков оно с приставкой рода блока, вида
        MEV17_4_2:MP_...), а did остаётся техническим и уникальным. Новым именам
        без своего did выдаётся номер выше 16-битного диапазона настоящих DID,
        один раз: дальше он читается из базы, поэтому история не рвётся.
        """
        if name in self._ids:
            return self._ids[name]
        from ru_labels import label as ru_label
        # Готовый ярлык важнее автоперевода: у кодов неисправностей это описание из
        # базы DiagBox, и humanise из мнемоники его не соберёт.
        lab = label or ru_label(did, name)
        row = self.db.execute("SELECT id FROM param WHERE name=?", (name,)).fetchone()
        if row is None:
            taken = did <= 0 or self.db.execute(
                "SELECT 1 FROM param WHERE did=?", (did,)).fetchone() is not None
            if taken:
                top = self.db.execute("SELECT COALESCE(MAX(did), 0) FROM param").fetchone()[0]
                did = max(top + 1, 0x10000)
            cur = self.db.execute(
                "INSERT INTO param(did, name, unit, label) VALUES (?,?,?,?)",
                (did, name, unit, lab))
            pid = cur.lastrowid
        else:
            pid = row[0]
            # дозаполняем ярлык у баз, созданных до его появления
            self.db.execute("UPDATE param SET label=? WHERE id=? AND (label IS NULL OR label='')",
                            (lab, pid))
        self._ids[name] = pid
        return pid

    def write(self, samples, ts=None):
        """samples: [(did, name, unit, value), ...] или [(did, name, unit, value, label), ...]"""
        ts = ts if ts is not None else time.time()
        rows = []
        for s in samples:
            d, n, u, v = s[:4]
            lab = s[4] if len(s) > 4 else ""
            if isinstance(v, (int, float)):
                rows.append((ts, self.param_id(d, n, u, lab), float(v)))
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

    def add_event(self, ts: float, title: str, kind: str = "note", details: str = ""):
        cur = self.db.execute(
            "INSERT INTO event(ts, title, kind, details) VALUES (?,?,?,?)",
            (ts, title, kind, details))
        self.db.commit()
        return cur.lastrowid

    def unsynced_events(self):
        return self.db.execute(
            "SELECT id, ts, title, kind, details FROM event WHERE synced = 0 ORDER BY id"
        ).fetchall()

    def mark_events_synced(self, ids):
        self.db.executemany("UPDATE event SET synced=1 WHERE id=?", [(i,) for i in ids])
        self.db.commit()

    def stats(self):
        total = self.db.execute("SELECT COUNT(*) FROM reading").fetchone()[0]
        pend = self.db.execute("SELECT COUNT(*) FROM reading WHERE synced=0").fetchone()[0]
        params = self.db.execute("SELECT COUNT(*) FROM param").fetchone()[0]
        ev = self.db.execute("SELECT COUNT(*) FROM event WHERE synced=0").fetchone()[0]
        return dict(total=total, pending=pend, params=params, events=ev)

    def close(self):
        self.db.close()
