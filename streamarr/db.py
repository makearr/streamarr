import json
import os
import sqlite3
import threading
import time

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_items (
    id TEXT PRIMARY KEY,             -- provider:video_id
    indexer_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    series_title TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published INTEGER,
    duration INTEGER,
    ordinal INTEGER,                 -- absolute episode number within series
    meta TEXT,
    updated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_indexer ON cache_items(indexer_id, series_title, ordinal);
CREATE TABLE IF NOT EXISTS stats (
    ts INTEGER NOT NULL,
    indexer_id TEXT NOT NULL,
    event TEXT NOT NULL,             -- search | grab | download_ok | download_fail
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_stats ON stats(indexer_id, event, ts);
CREATE TABLE IF NOT EXISTS subs_seen (
    item_id TEXT PRIMARY KEY,
    indexer_id TEXT NOT NULL,
    series_title TEXT,
    downloaded INTEGER DEFAULT 0,
    added INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subs ON subs_seen(indexer_id, series_title);
CREATE TABLE IF NOT EXISTS jobs (
    nzo_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    indexer_id TEXT,
    provider TEXT,
    url TEXT NOT NULL,
    media TEXT,
    status TEXT NOT NULL,            -- Queued|Downloading|Paused|Completed|Failed
    priority INTEGER DEFAULT 0,
    position INTEGER DEFAULT 0,
    bytes_total INTEGER DEFAULT 0,
    bytes_done INTEGER DEFAULT 0,
    storage TEXT,
    fail_message TEXT,
    outdir TEXT,
    added INTEGER NOT NULL,
    completed INTEGER
);
CREATE TABLE IF NOT EXISTS subs_runs (
    sub_id TEXT PRIMARY KEY,
    last_run INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_indexer_pub ON cache_items(indexer_id, published DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, completed DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(status, priority DESC, position);
CREATE INDEX IF NOT EXISTS idx_stats_ts ON stats(ts);
"""


def _conn():
    if not hasattr(_local, "conn"):
        path = os.path.join(config.CONFIG_DIR, "streamarr.db")
        os.makedirs(config.CONFIG_DIR, exist_ok=True)
        c = sqlite3.connect(path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=-65536")   # 64 MB page cache
        c.execute("PRAGMA temp_store=MEMORY")
        c.execute("PRAGMA mmap_size=134217728")  # 128 MB mmap
        c.executescript(SCHEMA)
        try:
            c.execute("ALTER TABLE jobs ADD COLUMN outdir TEXT")
        except sqlite3.OperationalError:
            pass  # column exists
        _local.conn = c
    return _local.conn


def execute(sql, params=()):
    c = _conn()
    cur = c.execute(sql, params)
    c.commit()
    return cur


def query(sql, params=()):
    return [dict(r) for r in _conn().execute(sql, params).fetchall()]


# --- cache ---

def cache_upsert(items):
    c = _conn()
    for it in items:
        c.execute(
            """INSERT INTO cache_items(id,indexer_id,provider,series_title,title,url,published,duration,ordinal,meta,updated)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,published=excluded.published,
               duration=excluded.duration,ordinal=excluded.ordinal,meta=excluded.meta,updated=excluded.updated""",
            (it["id"], it["indexer_id"], it["provider"], it.get("series_title"), it["title"], it["url"],
             it.get("published"), it.get("duration"), it.get("ordinal"), json.dumps(it.get("meta") or {}),
             int(time.time())))
    c.commit()


def cache_search(indexer_id, text=None, limit=100, offset=0):
    sql = "SELECT * FROM cache_items WHERE indexer_id=?"
    params = [indexer_id]
    if text:
        sql += " AND (title LIKE ? OR series_title LIKE ?)"
        params += [f"%{text}%", f"%{text}%"]
    sql += " ORDER BY published DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return query(sql, params)


def cache_get(item_id):
    rows = query("SELECT * FROM cache_items WHERE id=?", (item_id,))
    return rows[0] if rows else None


def cache_age(indexer_id, series_title=None):
    sql = "SELECT MAX(updated) m FROM cache_items WHERE indexer_id=?"
    params = [indexer_id]
    if series_title:
        sql += " AND series_title=?"
        params.append(series_title)
    r = query(sql, params)
    return r[0]["m"] if r and r[0]["m"] else 0


def cache_prune(days):
    execute("DELETE FROM cache_items WHERE updated < ?", (int(time.time()) - days * 86400,))


# --- stats ---

stat_hook = None  # set by main to feed prometheus


def stat(indexer_id, event, detail=""):
    execute("INSERT INTO stats(ts,indexer_id,event,detail) VALUES(?,?,?,?)",
            (int(time.time()), indexer_id, event, detail))
    if stat_hook:
        stat_hook(indexer_id, event)


def stats_summary():
    return query(
        """SELECT indexer_id, event, COUNT(*) count FROM stats GROUP BY indexer_id, event""")


def stats_timeline(days=14):
    since = int(time.time()) - days * 86400
    return query(
        """SELECT date(ts,'unixepoch') day, indexer_id, event, COUNT(*) count
           FROM stats WHERE ts>=? GROUP BY day, indexer_id, event ORDER BY day""", (since,))


# --- jobs ---

def job_save(j):
    execute(
        """INSERT INTO jobs(nzo_id,name,category,indexer_id,provider,url,media,status,priority,position,
           bytes_total,bytes_done,storage,fail_message,outdir,added,completed)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(nzo_id) DO UPDATE SET name=excluded.name,status=excluded.status,priority=excluded.priority,
           position=excluded.position,bytes_total=excluded.bytes_total,bytes_done=excluded.bytes_done,
           storage=excluded.storage,fail_message=excluded.fail_message,outdir=excluded.outdir,
           completed=excluded.completed""",
        (j["nzo_id"], j["name"], j.get("category"), j.get("indexer_id"), j.get("provider"), j["url"],
         j.get("media"), j["status"], j.get("priority", 0), j.get("position", 0),
         j.get("bytes_total", 0), j.get("bytes_done", 0), j.get("storage"), j.get("fail_message"),
         j.get("outdir"), j.get("added", int(time.time())), j.get("completed")))


def job_get(nzo_id):
    rows = query("SELECT * FROM jobs WHERE nzo_id=?", (nzo_id,))
    return rows[0] if rows else None


def jobs_active():
    return query("SELECT * FROM jobs WHERE status IN ('Queued','Downloading','Paused') ORDER BY position, added")


def jobs_history(limit=100, offset=0):
    return query("SELECT * FROM jobs WHERE status IN ('Completed','Failed') "
                 "ORDER BY completed DESC LIMIT ? OFFSET ?", (limit, offset))


def jobs_history_count():
    return query("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('Completed','Failed')")[0]["n"]


def job_delete(nzo_id):
    execute("DELETE FROM jobs WHERE nzo_id=?", (nzo_id,))


# --- subscriptions ---

def subs_known(indexer_id, series_title):
    return {r["item_id"] for r in query(
        "SELECT item_id FROM subs_seen WHERE indexer_id=? AND series_title=?",
        (indexer_id, series_title))}


def subs_is_new_source(indexer_id, series_title):
    r = query("SELECT COUNT(*) c FROM subs_seen WHERE indexer_id=? AND series_title=?",
              (indexer_id, series_title))
    return r[0]["c"] == 0


def subs_mark(item_id, indexer_id, series_title, downloaded):
    execute("""INSERT INTO subs_seen(item_id,indexer_id,series_title,downloaded,added)
               VALUES(?,?,?,?,?) ON CONFLICT(item_id) DO NOTHING""",
            (item_id, indexer_id, series_title, 1 if downloaded else 0, int(time.time())))


def subs_last_run(sub_id):
    r = query("SELECT last_run FROM subs_runs WHERE sub_id=?", (sub_id,))
    return r[0]["last_run"] if r else 0


def subs_set_run(sub_id):
    execute("""INSERT INTO subs_runs(sub_id,last_run) VALUES(?,?)
               ON CONFLICT(sub_id) DO UPDATE SET last_run=excluded.last_run""",
            (sub_id, int(time.time())))


def stats_timeseries(hours, buckets):
    """(grab counts, avg speed bytes/s) per bucket over the last <hours> hours."""
    import time as _t
    now = int(_t.time())
    start = now - hours * 3600
    width = hours * 3600 // buckets
    rows = query("SELECT ts, event, detail FROM stats WHERE ts>=? AND event IN ('grab','speed')",
                 (start,))
    grabs = [0] * buckets
    speed_sum = [0] * buckets
    speed_n = [0] * buckets
    for r in rows:
        b = min(buckets - 1, (r["ts"] - start) // width)
        if r["event"] == "grab":
            grabs[b] += 1
        else:
            try:
                speed_sum[b] += int(r["detail"] or 0)
                speed_n[b] += 1
            except ValueError:
                pass
    speed = [speed_sum[i] // speed_n[i] if speed_n[i] else 0 for i in range(buckets)]
    return {"start": start, "bucket_seconds": width, "grabs": grabs, "speed": speed}
