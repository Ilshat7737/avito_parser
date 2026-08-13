"""
Локальное хранилище объявлений (SQLite) — чтобы отличать новые объявления
от уже виденных и отслеживать изменение цены.
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
    first_seen TEXT,
    last_price INTEGER,
    last_checked TEXT
);
"""


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
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
    conn.execute(
        """INSERT INTO listings
           (id, title, price, url, photo, description, first_seen, last_price, last_checked)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item["id"], item["title"], item["price"], item["url"],
            item.get("photo"), item.get("description"), now, item["price"], now,
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
