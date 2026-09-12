import sqlite3
import time
import uuid
from contextlib import contextmanager


class Store:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'jobs.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, kind TEXT NOT NULL,
                quality TEXT NOT NULL, status TEXT NOT NULL, progress REAL DEFAULT 0,
                title TEXT DEFAULT '', filename TEXT DEFAULT '', error TEXT DEFAULT '',
                created REAL NOT NULL)''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self, limit=200):
        with self.connect() as db:
            return [dict(x) for x in db.execute("SELECT * FROM jobs ORDER BY status IN ('queued','downloading','processing','cancelling') DESC, created DESC LIMIT ?", (limit,))]

    def summary(self):
        with self.connect() as db:
            counts = dict(db.execute('SELECT status, count(*) FROM jobs GROUP BY status'))
            paused = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
            return {'counts': counts, 'total': sum(counts.values()), 'paused': bool(paused and paused[0] == '1')}

    def pause(self, paused):
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES ('paused', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('1' if paused else '0',))

    def get(self, ident):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
            return dict(row) if row else None

    def add(self, url, kind, quality):
        ident = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE url=? AND kind=? AND quality=? AND status IN ('queued','downloading','processing','cancelling')", (url, kind, quality)).fetchone():
                raise ValueError('This video and quality are already in your queue.')
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','downloading','processing','cancelling')").fetchone()[0] >= 20:
                raise ValueError('Queue is full. Wait for a download to finish.')
            db.execute('INSERT INTO jobs (id,url,kind,quality,status,created) VALUES (?,?,?,?,?,?)',
                       (ident, url, kind, quality, 'queued', time.time()))
        return self.get(ident)

    def update(self, ident, **fields):
        assert fields.keys() <= {'status', 'progress', 'title', 'filename', 'error'}
        with self.connect() as db:
            db.execute('UPDATE jobs SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?', (*fields.values(), ident))

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            paused = db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
            if paused and paused[0] == '1':
                return None
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='downloading' WHERE id=?", (row['id'],))
                return dict(row)

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE status='cancelling'")
            db.execute("UPDATE jobs SET status='failed',error='Service restarted. Retry this download.' WHERE status IN ('downloading','processing')")
