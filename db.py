"""
Локальное хранилище объявлений (SQLite) — чтобы отличать новые объявления
от уже виденных, отслеживать изменение цены и хранить оценку LLM-агента.
Веб-режим поддерживает ровно две независимые поисковые сессии
(session_id ∈ {1, 2}): у каждой свои listings/seen_ids/search_config/
worker_status, и общая таблица cross_session_claims не даёт одному и тому
же объявлению попасть в обе сессии в течение 24 часов.
"""

import sqlite3
from datetime import datetime, timedelta

CLAIM_WINDOW_HOURS = 24

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    session_id INTEGER NOT NULL DEFAULT 1,
    id TEXT NOT NULL,
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
    last_checked TEXT,
    PRIMARY KEY (session_id, id)
);
"""

SEEN_IDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_ids (
    session_id INTEGER NOT NULL DEFAULT 1,
    id TEXT NOT NULL,
    first_seen TEXT,
    PRIMARY KEY (session_id, id)
);
"""

SEARCH_CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_config (
    id INTEGER PRIMARY KEY CHECK (id IN (1, 2)),
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
    id INTEGER PRIMARY KEY CHECK (id IN (1, 2)),
    last_check TEXT,
    last_count INTEGER,
    paused INTEGER DEFAULT 0,
    pause_reason TEXT,
    last_error TEXT,
    scanning INTEGER DEFAULT 0
);
"""

CROSS_SESSION_CLAIMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS cross_session_claims (
    id TEXT PRIMARY KEY,
    session_id INTEGER NOT NULL,
    claimed_at TEXT NOT NULL
);
"""


def _migrate_worker_status(conn: sqlite3.Connection):
    """CREATE TABLE IF NOT EXISTS не добавляет новые столбцы в уже
    существующую таблицу — если worker_status создана более старой
    версией кода (файл БД пережил несколько обновлений схемы), добавляем
    недостающие столбцы вручную, чтобы не заставлять пользователя
    вручную удалять файл БД при каждом обновлении."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(worker_status)")}
    for column, ddl in (
        ("paused", "ALTER TABLE worker_status ADD COLUMN paused INTEGER DEFAULT 0"),
        ("pause_reason", "ALTER TABLE worker_status ADD COLUMN pause_reason TEXT"),
        ("last_error", "ALTER TABLE worker_status ADD COLUMN last_error TEXT"),
        ("scanning", "ALTER TABLE worker_status ADD COLUMN scanning INTEGER DEFAULT 0"),
    ):
        if column not in existing:
            conn.execute(ddl)


def _migrate_to_sessions(conn: sqlite3.Connection):
    """Одноразовая миграция однопользовательской схемы (без понятия
    сессии) к схеме с двумя параллельными поисковыми сессиями.

    Сигнал — отсутствие колонки session_id в listings: раз её нет,
    значит и search_config/worker_status ещё в старом виде
    (CHECK id = 1), все четыре таблицы мигрируют одной пересборкой.
    ALTER TABLE в SQLite не умеет менять PRIMARY KEY или CHECK — поэтому
    таблицы не патчатся на месте, а пересоздаются внутри того же файла
    БД: RENAME старой в *_old, CREATE новой по актуальной схеме,
    INSERT ... SELECT данных из старой (с session_id=1 для listings/
    seen_ids), DROP старой. Никакого удаления файла БД.

    Вся последовательность (включая сам guard-чек выше) обёрнута в одну
    явную SQLite-транзакцию. Это не просто украшение: модуль sqlite3 по
    умолчанию (isolation_level='') неявно коммитит уже открытую
    транзакцию перед выполнением КАЖДОГО DDL-стейтмента (CREATE/ALTER/
    DROP) — то есть при таком режиме RENAME/CREATE/DROP из цикла ниже
    коммитятся поодиночке независимо от финального conn.commit(). Если
    процесс падает между, например, CREATE TABLE listings (уже
    закоммичен, с session_id) и последующим INSERT ... SELECT (ещё не
    закоммичен), guard-чек при следующем запуске увидит "session_id"
    в listings и решит, что миграция уже прошла — а данные так и
    останутся в listings_old, недостижимо и навсегда.

    Чтобы это исключить, на время миграции переключаем соединение в
    isolation_level=None (autocommit passthrough — sqlite3 перестаёт
    сам управлять транзакциями) и оборачиваем всё явными BEGIN/COMMIT.
    В этом режиме DDL-стейтменты подчиняются обычной SQLite-транзакции
    как любые другие, поэтому либо коммитятся все четыре таблицы разом,
    либо (при исключении или падении процесса до COMMIT) не коммитится
    ни одна — SQLite сама откатывает незавершённую транзакцию при
    следующем открытии файла. Проверено экспериментально (см.
    tests/test_db.py::test_migrate_to_sessions_atomic_on_crash_mid_migration)."""
    previous_isolation_level = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN")

        existing = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
        if "session_id" in existing:
            conn.execute("ROLLBACK")  # ничего не писали, просто закрываем чтение
            return  # уже мигрировано

        conn.execute("ALTER TABLE listings RENAME TO listings_old")
        conn.execute(SCHEMA)
        conn.execute("""
            INSERT INTO listings (session_id, id, title, price, url, photo,
                description, year, mileage, relevant, relevance_reason,
                agent_evaluated_at, first_seen, last_price, last_checked)
            SELECT 1, id, title, price, url, photo, description, year, mileage,
                relevant, relevance_reason, agent_evaluated_at, first_seen,
                last_price, last_checked
            FROM listings_old
        """)
        conn.execute("DROP TABLE listings_old")

        conn.execute("ALTER TABLE seen_ids RENAME TO seen_ids_old")
        conn.execute(SEEN_IDS_SCHEMA)
        conn.execute("""
            INSERT INTO seen_ids (session_id, id, first_seen)
            SELECT 1, id, first_seen FROM seen_ids_old
        """)
        conn.execute("DROP TABLE seen_ids_old")

        conn.execute("ALTER TABLE search_config RENAME TO search_config_old")
        conn.execute(SEARCH_CONFIG_SCHEMA)
        conn.execute("INSERT INTO search_config SELECT * FROM search_config_old")
        conn.execute("DROP TABLE search_config_old")

        conn.execute("ALTER TABLE worker_status RENAME TO worker_status_old")
        conn.execute(WORKER_STATUS_SCHEMA)
        conn.execute("INSERT INTO worker_status SELECT * FROM worker_status_old")
        conn.execute("DROP TABLE worker_status_old")

        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = previous_isolation_level


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute(SCHEMA)
    conn.execute(SEEN_IDS_SCHEMA)
    conn.execute(SEARCH_CONFIG_SCHEMA)
    conn.execute(WORKER_STATUS_SCHEMA)
    conn.execute(CROSS_SESSION_CLAIMS_SCHEMA)
    _migrate_worker_status(conn)
    _migrate_to_sessions(conn)
    conn.commit()
    return conn


def get_listing(conn: sqlite3.Connection, session_id: int, item_id: str):
    cur = conn.execute(
        "SELECT * FROM listings WHERE session_id = ? AND id = ?", (session_id, item_id)
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def upsert_new(conn: sqlite3.Connection, session_id: int, item: dict):
    now = datetime.now().isoformat(timespec="seconds")
    relevant = item.get("relevant")
    evaluated_at = now if relevant is not None else None
    conn.execute(
        """INSERT INTO listings
           (session_id, id, title, price, url, photo, description, year, mileage,
            relevant, relevance_reason, agent_evaluated_at,
            first_seen, last_price, last_checked)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id, item["id"], item["title"], item["price"], item["url"],
            item.get("photo"), item.get("description"),
            item.get("year"), item.get("mileage"),
            relevant, item.get("relevance_reason"), evaluated_at,
            now, item["price"], now,
        ),
    )
    conn.commit()


def update_price(conn: sqlite3.Connection, session_id: int, item_id: str, new_price: int):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE listings SET last_price = price, price = ?, last_checked = ? "
        "WHERE session_id = ? AND id = ?",
        (new_price, now, session_id, item_id),
    )
    conn.commit()


def touch(conn: sqlite3.Connection, session_id: int, item_id: str):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE listings SET last_checked = ? WHERE session_id = ? AND id = ?",
        (now, session_id, item_id),
    )
    conn.commit()


def is_seen(conn: sqlite3.Connection, session_id: int, item_id: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM seen_ids WHERE session_id = ? AND id = ?", (session_id, item_id)
    )
    return cur.fetchone() is not None


def mark_seen(conn: sqlite3.Connection, session_id: int, item_id: str):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR IGNORE INTO seen_ids (session_id, id, first_seen) VALUES (?, ?, ?)",
        (session_id, item_id, now),
    )
    conn.commit()


def evict_old_listings(conn: sqlite3.Connection, session_id: int, keep: int = 100):
    conn.execute(
        """DELETE FROM listings
           WHERE session_id = ? AND id NOT IN (
               SELECT id FROM listings WHERE session_id = ?
               ORDER BY first_seen DESC, rowid DESC LIMIT ?
           )""",
        (session_id, session_id, keep),
    )
    conn.commit()


def get_relevant_listings(conn: sqlite3.Connection, session_id: int, include_rejected: bool = False) -> list[dict]:
    if include_rejected:
        query = "SELECT * FROM listings WHERE session_id = ? AND relevant IS NOT NULL ORDER BY first_seen DESC"
    else:
        query = "SELECT * FROM listings WHERE session_id = ? AND relevant = 1 ORDER BY first_seen DESC"
    cur = conn.execute(query, (session_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_unevaluated_listings(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    cur = conn.execute(
        "SELECT * FROM listings WHERE session_id = ? AND agent_evaluated_at IS NULL", (session_id,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_all_listings(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    cur = conn.execute("SELECT * FROM listings WHERE session_id = ?", (session_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_relevance(conn: sqlite3.Connection, session_id: int, item_id: str, relevant: int, reason: str):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE listings SET relevant = ?, relevance_reason = ?, agent_evaluated_at = ? "
        "WHERE session_id = ? AND id = ?",
        (relevant, reason, now, session_id, item_id),
    )
    conn.commit()


def mark_all_for_rescore(conn: sqlite3.Connection, session_id: int):
    conn.execute(
        "UPDATE listings SET relevant = NULL, relevance_reason = NULL, agent_evaluated_at = NULL "
        "WHERE session_id = ?",
        (session_id,),
    )
    conn.commit()


def get_search_config(conn: sqlite3.Connection, session_id: int):
    cur = conn.execute(
        """SELECT city, brand, price_min, price_max, mileage_min, mileage_max,
                  year_min, year_max, query_text, search_url, updated_at
           FROM search_config WHERE id = ?""",
        (session_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def save_search_config(conn: sqlite3.Connection, session_id: int, cfg: dict):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO search_config
           (id, city, brand, price_min, price_max, mileage_min, mileage_max,
            year_min, year_max, query_text, search_url, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             city=excluded.city, brand=excluded.brand,
             price_min=excluded.price_min, price_max=excluded.price_max,
             mileage_min=excluded.mileage_min, mileage_max=excluded.mileage_max,
             year_min=excluded.year_min, year_max=excluded.year_max,
             query_text=excluded.query_text, search_url=excluded.search_url,
             updated_at=excluded.updated_at""",
        (
            session_id, cfg.get("city"), cfg.get("brand"),
            cfg.get("price_min"), cfg.get("price_max"),
            cfg.get("mileage_min"), cfg.get("mileage_max"),
            cfg.get("year_min"), cfg.get("year_max"),
            cfg.get("query_text"), cfg.get("search_url"), now,
        ),
    )
    conn.commit()


def set_last_check(conn: sqlite3.Connection, session_id: int, count: int):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO worker_status (id, last_check, last_count) VALUES (?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             last_check = excluded.last_check, last_count = excluded.last_count""",
        (session_id, now, count),
    )
    conn.commit()


def get_status(conn: sqlite3.Connection, session_id: int) -> dict:
    cur = conn.execute(
        """SELECT last_check, last_count, paused, pause_reason, last_error, scanning
           FROM worker_status WHERE id = ?""",
        (session_id,),
    )
    row = cur.fetchone()
    if row is None:
        return {
            "last_check": None, "last_count": 0, "paused": False,
            "pause_reason": None, "last_error": None, "scanning": False,
        }
    return {
        "last_check": row[0],
        "last_count": row[1],
        "paused": bool(row[2]),
        "pause_reason": row[3],
        "last_error": row[4],
        "scanning": bool(row[5]),
    }


def set_paused(conn: sqlite3.Connection, session_id: int, paused: bool, reason: str | None = None, error: str | None = None):
    conn.execute(
        """INSERT INTO worker_status (id, last_count, paused, pause_reason, last_error)
           VALUES (?, 0, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             paused = excluded.paused, pause_reason = excluded.pause_reason,
             last_error = excluded.last_error""",
        (session_id, 1 if paused else 0, reason, error),
    )
    conn.commit()


def set_scanning(conn: sqlite3.Connection, session_id: int, scanning: bool):
    conn.execute(
        """INSERT INTO worker_status (id, last_count, scanning) VALUES (?, 0, ?)
           ON CONFLICT(id) DO UPDATE SET scanning = excluded.scanning""",
        (session_id, 1 if scanning else 0),
    )
    conn.commit()


def is_claimed_by_other_session(conn: sqlite3.Connection, session_id: int, item_id: str) -> bool:
    cutoff = (datetime.now() - timedelta(hours=CLAIM_WINDOW_HOURS)).isoformat(timespec="seconds")
    cur = conn.execute(
        "SELECT 1 FROM cross_session_claims WHERE id = ? AND session_id != ? AND claimed_at > ?",
        (item_id, session_id, cutoff),
    )
    return cur.fetchone() is not None


def claim_for_session(conn: sqlite3.Connection, session_id: int, item_id: str):
    now = datetime.now().isoformat(timespec="seconds")
    cutoff = (datetime.now() - timedelta(hours=CLAIM_WINDOW_HOURS)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM cross_session_claims WHERE claimed_at <= ?", (cutoff,))
    conn.execute(
        "INSERT OR IGNORE INTO cross_session_claims (id, session_id, claimed_at) VALUES (?, ?, ?)",
        (item_id, session_id, now),
    )
    conn.commit()
