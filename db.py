"""
Локальное хранилище объявлений (SQLite) — чтобы отличать новые объявления
от уже виденных, отслеживать изменение цены и хранить оценку LLM-агента.
"""

import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,
    title TEXT,
    price INTEGER,
    url TEXT,
    photo TEXT,
    description TEXT,
    year INTEGER,
    mileage INTEGER,
    relevant INTEGER,
    relevance_reason TEXT,
    agent_evaluated_at TEXT,
    first_seen TEXT,
    last_price INTEGER,
    last_checked TEXT
);
"""

SEEN_IDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_ids (
    id TEXT PRIMARY KEY,
    first_seen TEXT
);
"""

SEARCH_CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    city TEXT,
    brand TEXT,
    price_min INTEGER,
    price_max INTEGER,
    mileage_min INTEGER,
    mileage_max INTEGER,
    year_min INTEGER,
    year_max INTEGER,
    query_text TEXT,
    search_url TEXT,
    updated_at TEXT
);
"""

WORKER_STATUS_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_check TEXT,
    last_count INTEGER
);
"""


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(SCHEMA)
    conn.execute(SEEN_IDS_SCHEMA)
    conn.execute(SEARCH_CONFIG_SCHEMA)
    conn.execute(WORKER_STATUS_SCHEMA)
    conn.commit()
    return conn


def get_listing(conn: sqlite3.Connection, item_id: str):
    cur = conn.execute("SELECT * FROM listings WHERE id = ?", (item_id,))
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def upsert_new(conn: sqlite3.Connection, item: dict):
    now = datetime.now().isoformat(timespec="seconds")
    relevant = item.get("relevant")
    evaluated_at = now if relevant is not None else None
    conn.execute(
        """INSERT INTO listings
           (id, title, price, url, photo, description, year, mileage,
            relevant, relevance_reason, agent_evaluated_at,
            first_seen, last_price, last_checked)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item["id"], item["title"], item["price"], item["url"],
            item.get("photo"), item.get("description"),
            item.get("year"), item.get("mileage"),
            relevant, item.get("relevance_reason"), evaluated_at,
            now, item["price"], now,
        ),
    )
    conn.commit()


def update_price(conn: sqlite3.Connection, item_id: str, new_price: int):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE listings SET price = ?, last_price = ?, last_checked = ? WHERE id = ?",
        (new_price, new_price, now, item_id),
    )
    conn.commit()


def touch(conn: sqlite3.Connection, item_id: str):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE listings SET last_checked = ? WHERE id = ?", (now, item_id))
    conn.commit()


def is_seen(conn: sqlite3.Connection, item_id: str) -> bool:
    cur = conn.execute("SELECT 1 FROM seen_ids WHERE id = ?", (item_id,))
    return cur.fetchone() is not None


def mark_seen(conn: sqlite3.Connection, item_id: str):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR IGNORE INTO seen_ids (id, first_seen) VALUES (?, ?)",
        (item_id, now),
    )
    conn.commit()


def evict_old_listings(conn: sqlite3.Connection, keep: int = 100):
    conn.execute(
        """DELETE FROM listings
           WHERE id NOT IN (
               SELECT id FROM listings ORDER BY first_seen DESC, rowid DESC LIMIT ?
           )""",
        (keep,),
    )
    conn.commit()


def get_relevant_listings(conn: sqlite3.Connection, include_rejected: bool = False) -> list[dict]:
    if include_rejected:
        query = "SELECT * FROM listings WHERE relevant IS NOT NULL ORDER BY first_seen DESC"
    else:
        query = "SELECT * FROM listings WHERE relevant = 1 ORDER BY first_seen DESC"
    cur = conn.execute(query)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_unevaluated_listings(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute("SELECT * FROM listings WHERE agent_evaluated_at IS NULL")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_relevance(conn: sqlite3.Connection, item_id: str, relevant: int, reason: str):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE listings SET relevant = ?, relevance_reason = ?, agent_evaluated_at = ? WHERE id = ?",
        (relevant, reason, now, item_id),
    )
    conn.commit()


def mark_all_for_rescore(conn: sqlite3.Connection):
    conn.execute(
        "UPDATE listings SET relevant = NULL, relevance_reason = NULL, agent_evaluated_at = NULL"
    )
    conn.commit()


def get_search_config(conn: sqlite3.Connection):
    cur = conn.execute(
        """SELECT city, brand, price_min, price_max, mileage_min, mileage_max,
                  year_min, year_max, query_text, search_url, updated_at
           FROM search_config WHERE id = 1"""
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def save_search_config(conn: sqlite3.Connection, cfg: dict):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO search_config
           (id, city, brand, price_min, price_max, mileage_min, mileage_max,
            year_min, year_max, query_text, search_url, updated_at)
           VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             city=excluded.city, brand=excluded.brand,
             price_min=excluded.price_min, price_max=excluded.price_max,
             mileage_min=excluded.mileage_min, mileage_max=excluded.mileage_max,
             year_min=excluded.year_min, year_max=excluded.year_max,
             query_text=excluded.query_text, search_url=excluded.search_url,
             updated_at=excluded.updated_at""",
        (
            cfg.get("city"), cfg.get("brand"),
            cfg.get("price_min"), cfg.get("price_max"),
            cfg.get("mileage_min"), cfg.get("mileage_max"),
            cfg.get("year_min"), cfg.get("year_max"),
            cfg.get("query_text"), cfg.get("search_url"), now,
        ),
    )
    conn.commit()


def set_last_check(conn: sqlite3.Connection, count: int):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO worker_status (id, last_check, last_count) VALUES (1, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             last_check = excluded.last_check, last_count = excluded.last_count""",
        (now, count),
    )
    conn.commit()


def get_status(conn: sqlite3.Connection) -> dict:
    cur = conn.execute("SELECT last_check, last_count FROM worker_status WHERE id = 1")
    row = cur.fetchone()
    if row is None:
        return {"last_check": None, "last_count": 0}
    return {"last_check": row[0], "last_count": row[1]}
