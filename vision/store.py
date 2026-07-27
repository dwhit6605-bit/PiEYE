import os
import sqlite3
import threading
import time


class EventStore:
    """SQLite-backed history of detection events, with snapshot JPEGs on disk.

    Safe for one writer thread (the monitor) plus reader threads (the web server):
    opened with check_same_thread=False, WAL mode, and a lock around writes.
    """

    def __init__(self, db_path, snapshot_dir):
        self.db_path = db_path
        self.snapshot_dir = snapshot_dir
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        os.makedirs(snapshot_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       REAL NOT NULL,
                iso      TEXT NOT NULL,
                camera   TEXT NOT NULL,
                labels   TEXT NOT NULL,
                message  TEXT NOT NULL,
                snapshot TEXT,
                max_conf REAL
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS push_subs (
                endpoint TEXT PRIMARY KEY,
                sub_json TEXT NOT NULL,
                label    TEXT,
                created  REAL NOT NULL
            )
        """)
        # migrate: `clip` column added after the first releases
        cols = {r["name"] for r in self._db.execute("PRAGMA table_info(events)")}
        if "clip" not in cols:
            self._db.execute("ALTER TABLE events ADD COLUMN clip TEXT")
        self._db.commit()
        self.clip_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "clips")
        os.makedirs(self.clip_dir, exist_ok=True)

    def set_event_clip(self, event_id, filename):
        with self._lock:
            self._db.execute("UPDATE events SET clip = ? WHERE id = ?", (filename, event_id))
            self._db.commit()

    def clip_path(self, filename):
        return os.path.join(self.clip_dir, os.path.basename(filename))

    # ---- web push subscriptions ----
    def add_push_sub(self, endpoint, sub_json, label=None, created=None):
        import time as _t
        with self._lock:
            self._db.execute(
                "INSERT INTO push_subs (endpoint, sub_json, label, created) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(endpoint) DO UPDATE SET sub_json=excluded.sub_json",
                (endpoint, sub_json, label, created if created is not None else _t.time()),
            )
            self._db.commit()

    def list_push_subs(self):
        with self._lock:
            rows = self._db.execute("SELECT * FROM push_subs").fetchall()
        return [dict(r) for r in rows]

    def delete_push_sub(self, endpoint):
        with self._lock:
            cur = self._db.execute("DELETE FROM push_subs WHERE endpoint = ?", (endpoint,))
            self._db.commit()
            return cur.rowcount > 0

    def add_event(self, ts, iso, camera, labels, message, jpeg_bytes=None, max_conf=None):
        snapshot = None
        if jpeg_bytes is not None:
            snapshot = f"{int(ts * 1000)}_{camera}.jpg".replace("/", "_")
            with open(os.path.join(self.snapshot_dir, snapshot), "wb") as f:
                f.write(jpeg_bytes)
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events (ts, iso, camera, labels, message, snapshot, max_conf)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, iso, camera, labels, message, snapshot, max_conf),
            )
            self._db.commit()
            return cur.lastrowid

    def list_events(self, limit=50, offset=0, camera=None, label=None):
        q = "SELECT * FROM events WHERE 1=1"
        args = []
        if camera:
            q += " AND camera = ?"
            args.append(camera)
        if label:
            q += " AND labels LIKE ?"
            args.append(f"%{label}%")
        q += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        args += [int(limit), int(offset)]
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def get_event(self, event_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    def delete_event(self, event_id):
        row = self.get_event(event_id)
        if not row:
            return False
        if row.get("snapshot"):
            path = os.path.join(self.snapshot_dir, row["snapshot"])
            if os.path.exists(path):
                os.remove(path)
        if row.get("clip"):
            cpath = self.clip_path(row["clip"])
            if os.path.exists(cpath):
                os.remove(cpath)
        with self._lock:
            self._db.execute("DELETE FROM events WHERE id = ?", (event_id,))
            self._db.commit()
        return True

    def snapshot_path(self, filename):
        # guard against path traversal from the URL
        safe = os.path.basename(filename)
        return os.path.join(self.snapshot_dir, safe)

    def stats(self):
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
            since = time.time() - 86400
            today = self._db.execute("SELECT COUNT(*) c FROM events WHERE ts > ?",
                                     (since,)).fetchone()["c"]
            by_cam = self._db.execute(
                "SELECT camera, COUNT(*) c FROM events GROUP BY camera").fetchall()
        return {"total": total, "last_24h": today,
                "by_camera": {r["camera"]: r["c"] for r in by_cam}}

    def prune(self, retention_days=14, max_events=5000):
        """Drop events older than retention_days, then cap total to max_events."""
        removed = []
        with self._lock:
            if retention_days and retention_days > 0:
                cutoff = time.time() - retention_days * 86400
                rows = self._db.execute(
                    "SELECT id, snapshot, clip FROM events WHERE ts < ?", (cutoff,)).fetchall()
                removed += rows
                self._db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            if max_events and max_events > 0:
                rows = self._db.execute(
                    "SELECT id, snapshot, clip FROM events WHERE id NOT IN "
                    "(SELECT id FROM events ORDER BY ts DESC LIMIT ?)", (max_events,)).fetchall()
                removed += rows
                self._db.execute(
                    "DELETE FROM events WHERE id NOT IN "
                    "(SELECT id FROM events ORDER BY ts DESC LIMIT ?)", (max_events,))
            self._db.commit()
        for r in removed:
            if r["snapshot"]:
                p = os.path.join(self.snapshot_dir, r["snapshot"])
                if os.path.exists(p):
                    os.remove(p)
            if r["clip"]:
                cp = self.clip_path(r["clip"])
                if os.path.exists(cp):
                    os.remove(cp)
        return len(removed)
