"""
Локальное хранилище объявлений (SQLite) — чтобы отличать новые объявления
от уже виденных, отслеживать изменение цены и хранить оценку LLM-агента.

Веб-режим умеет вести ДВЕ параллельные поисковые сессии (два независимых
окна браузера с разными фильтрами, см. worker.py/app.py) в одном файле
БД. Разделение — через колонку `session_id` (1 или 2) в `listings`,
`seen_ids`, `search_config`, `worker_status`. CLI-режим (main.py) session_id
не передаёт нигде — все функции используют session_id=1 по умолчанию, так
что его поведение и файл БД (config.DB_PATH) не меняются.

`seen_ids` — единственная таблица, которая физически ХРАНИТ обе сессии
рядом (session_id — часть составного ключа, а не колонка-фильтр) именно
для того, чтобы `recently_seen_elsewhere()` мог проверить, не забирала ли
уже то же самое объявление СОСЕДНЯЯ сессия за последние 24 часа — это и
есть механизм "не пересекаться" между двумя окнами.
"""

import sqlite3
import threading
from datetime import datetime, timedelta

SESSION_IDS = (1, 2)

# Веб-режим держит несколько соединений к ОДНОМУ файлу БД одновременно
# (по одному на фоновый поток каждой сессии + соединение Flask, все —
# в одном процессе). WAL-режим (см. get_conn) в теории разрешает одного
# писателя и много читателей параллельно, но на практике под редкими,
# но не строго синхронизированными commit()-ами из разных потоков
# Python-модуль sqlite3 всё равно может словить "database is locked"
# раньше, чем сработает PRAGMA busy_timeout. Проще и надёжнее —
# сериализовать все ЗАПИСИ одним процессным локом; чтения лока не ждут.
# Частота записи низкая (раз в MIN_DELAY..MAX_DELAY секунд на сессию),
# так что это не бьёт по параллельности реального сканирования — оно
# идёт в Playwright, а не в БД.
_WRITE_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT,
    session_id INTEGER NOT NULL DEFAULT 1,
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
    PRIMARY KEY (id, session_id)
);
"""

# id объявления встречается в обеих сессиях (в разное время — после того,
# как cutoff в 24 часа истёк) => составной ключ, а не PK по одному id.
SEEN_IDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_ids (
    id TEXT,
    session_id INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT,
    PRIMARY KEY (id, session_id)
);
"""

SEARCH_CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_config (
    session_id INTEGER PRIMARY KEY CHECK (session_id IN (1, 2)),
    label TEXT,
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
    session_id INTEGER PRIMARY KEY CHECK (session_id IN (1, 2)),
    last_check TEXT,
    last_count INTEGER,
    paused INTEGER DEFAULT 0,
    pause_reason TEXT,
    last_error TEXT,
    scanning INTEGER DEFAULT 0
);
"""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_listings(conn: sqlite3.Connection):
    """Старые файлы БД (до многосессионного режима) хранят listings с PK
    по одному `id`, без `session_id`. Пересоздаём таблицу с составным
    ключом (id, session_id) и переносим старые строки как session_id=1 —
    так, чтобы вторая сессия могла независимо завести те же id позже."""
    if not _table_exists(conn, "listings"):
        return
    if "session_id" in _columns(conn, "listings"):
        return
    conn.execute("ALTER TABLE listings RENAME TO listings_old")
    conn.execute(SCHEMA)
    conn.execute(
        """INSERT INTO listings
           (id, session_id, title, price, url, photo, description, year, mileage,
            relevant, relevance_reason, agent_evaluated_at, first_seen, last_price, last_checked)
           SELECT id, 1, title, price, url, photo, description, year, mileage,
                  relevant, relevance_reason, agent_evaluated_at, first_seen, last_price, last_checked
           FROM listings_old"""
    )
    conn.execute("DROP TABLE listings_old")


def _migrate_seen_ids(conn: sqlite3.Connection):
    if not _table_exists(conn, "seen_ids"):
        return
    if "session_id" in _columns(conn, "seen_ids"):
        return
    conn.execute("ALTER TABLE seen_ids RENAME TO seen_ids_old")
    conn.execute(SEEN_IDS_SCHEMA)
    conn.execute(
        "INSERT INTO seen_ids (id, session_id, first_seen) SELECT id, 1, first_seen FROM seen_ids_old"
    )
    conn.execute("DROP TABLE seen_ids_old")


def _migrate_search_config(conn: sqlite3.Connection):
    """Старая схема — одна строка с id=1 (PK по `id`). Новая — до двух
    строк, PK по `session_id`. Переносим старую строку как session_id=1."""
    if not _table_exists(conn, "search_config"):
        return
    cols = _columns(conn, "search_config")
    if "session_id" in cols:
        return
    conn.execute("ALTER TABLE search_config RENAME TO search_config_old")
    conn.execute(SEARCH_CONFIG_SCHEMA)
    old_cols = _columns(conn, "search_config_old") - {"id"}
    common = [c for c in ("city", "brand", "price_min", "price_max", "mileage_min",
                           "mileage_max", "year_min", "year_max", "query_text",
                           "search_url", "updated_at") if c in old_cols]
    col_list = ", ".join(common)
    conn.execute(
        f"INSERT INTO search_config (session_id, {col_list}) "
        f"SELECT 1, {col_list} FROM search_config_old WHERE id = 1"
    )
    conn.execute("DROP TABLE search_config_old")


def _migrate_worker_status_columns(conn: sqlite3.Connection):
    """CREATE TABLE IF NOT EXISTS не добавляет новые столбцы в уже
    существующую таблицу — если worker_status создана более старой
    версией кода (файл БД пережил несколько обновлений схемы), добавляем
    недостающие столбцы вручную, чтобы не заставлять пользователя
    вручную удалять файл БД при каждом обновлении."""
    if not _table_exists(conn, "worker_status"):
        return
    existing = _columns(conn, "worker_status")
    for column, ddl in (
        ("paused", "ALTER TABLE worker_status ADD COLUMN paused INTEGER DEFAULT 0"),
        ("pause_reason", "ALTER TABLE worker_status ADD COLUMN pause_reason TEXT"),
        ("last_error", "ALTER TABLE worker_status ADD COLUMN last_error TEXT"),
        ("scanning", "ALTER TABLE worker_status ADD COLUMN scanning INTEGER DEFAULT 0"),
    ):
        if column not in existing:
            conn.execute(ddl)


def _migrate_worker_status_session(conn: sqlite3.Connection):
    """Старая схема — одна строка id=1 (PK по `id`). Новая — до двух
    строк, PK по `session_id`. Выполняется ПОСЛЕ
    `_migrate_worker_status_columns`, чтобы старая таблица уже имела все
    столбцы перед копированием."""
    if not _table_exists(conn, "worker_status"):
        return
    if "session_id" in _columns(conn, "worker_status"):
        return
    conn.execute("ALTER TABLE worker_status RENAME TO worker_status_old")
    conn.execute(WORKER_STATUS_SCHEMA)
    conn.execute(
        """INSERT INTO worker_status
           (session_id, last_check, last_count, paused, pause_reason, last_error, scanning)
           SELECT 1, last_check, last_count, paused, pause_reason, last_error, scanning
           FROM worker_status_old WHERE id = 1"""
    )
    conn.execute("DROP TABLE worker_status_old")


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    # WAL + busy_timeout — файл БД теперь может открываться из нескольких
    # потоков одновременно (по одному на сессию поиска, см. worker.py) и
    # из потока Flask-сервера; без этого конкурентная запись из двух
    # сессий сразу кидала бы "database is locked".
    conn.execute("PRAGMA busy_timeout=5000")

    # Всё, что пишет в файл БД (включая саму установку WAL-режима на новом
    # файле и создание схемы/миграции), — под общим локом. Два потока
    # открывают соединение к одному файлу почти синхронно (два воркера
    # сессий стартуют друг за другом в app.main()); без лока даже
    # `PRAGMA journal_mode=WAL` на ещё не существующем файле может
    # столкнуться так же, как обычная запись.
    with _WRITE_LOCK:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(SCHEMA)
        conn.execute(SEEN_IDS_SCHEMA)
        conn.execute(SEARCH_CONFIG_SCHEMA)
        conn.execute(WORKER_STATUS_SCHEMA)

        _migrate_listings(conn)
        _migrate_seen_ids(conn)
        _migrate_search_config(conn)
        _migrate_worker_status_columns(conn)
        _migrate_worker_status_session(conn)

        conn.commit()
    return conn


def get_listing(conn: sqlite3.Connection, item_id: str, session_id: int = 1):
    cur = conn.execute(
        "SELECT * FROM listings WHERE id = ? AND session_id = ?", (item_id, session_id)
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def upsert_new(conn: sqlite3.Connection, item: dict, session_id: int = 1):
    now = datetime.now().isoformat(timespec="seconds")
    relevant = item.get("relevant")
    evaluated_at = now if relevant is not None else None
    with _WRITE_LOCK:
        conn.execute(
            """INSERT INTO listings
               (id, session_id, title, price, url, photo, description, year, mileage,
                relevant, relevance_reason, agent_evaluated_at,
                first_seen, last_price, last_checked)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item["id"], session_id, item["title"], item["price"], item["url"],
                item.get("photo"), item.get("description"),
                item.get("year"), item.get("mileage"),
                relevant, item.get("relevance_reason"), evaluated_at,
                now, item["price"], now,
            ),
        )
        conn.commit()


def update_price(conn: sqlite3.Connection, item_id: str, new_price: int, session_id: int = 1):
    now = datetime.now().isoformat(timespec="seconds")
    with _WRITE_LOCK:
        conn.execute(
            "UPDATE listings SET last_price = price, price = ?, last_checked = ? WHERE id = ? AND session_id = ?",
            (new_price, now, item_id, session_id),
        )
        conn.commit()


def touch(conn: sqlite3.Connection, item_id: str, session_id: int = 1):
    now = datetime.now().isoformat(timespec="seconds")
    with _WRITE_LOCK:
        conn.execute(
            "UPDATE listings SET last_checked = ? WHERE id = ? AND session_id = ?",
            (now, item_id, session_id),
        )
        conn.commit()


def is_seen(conn: sqlite3.Connection, item_id: str, session_id: int = 1) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM seen_ids WHERE id = ? AND session_id = ?", (item_id, session_id)
    )
    return cur.fetchone() is not None


def mark_seen(conn: sqlite3.Connection, item_id: str, session_id: int = 1):
    now = datetime.now().isoformat(timespec="seconds")
    with _WRITE_LOCK:
        conn.execute(
            "INSERT OR IGNORE INTO seen_ids (id, session_id, first_seen) VALUES (?, ?, ?)",
            (item_id, session_id, now),
        )
        conn.commit()


def recently_seen_elsewhere(conn: sqlite3.Connection, item_id: str, session_id: int, hours: int = 24) -> bool:
    """True, если это объявление уже обрабатывалось ДРУГОЙ параллельной
    сессией (другим окном с другими фильтрами) за последние `hours`
    часов. Вызывается перед тем, как считать объявление новым — не даёт
    двум сессиям одновременно скачивать описание, звать LLM-агента и
    слать уведомление об одном и том же объявлении, если диапазоны их
    фильтров пересеклись. Спустя `hours` часов проверка снова пропускает
    объявление как потенциально новое (например, если оно было
    отклонено агентом в одной сессии, а критерии второй сессии другие)."""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    cur = conn.execute(
        "SELECT 1 FROM seen_ids WHERE id = ? AND session_id != ? AND first_seen >= ? LIMIT 1",
        (item_id, session_id, cutoff),
    )
    return cur.fetchone() is not None


def evict_old_listings(conn: sqlite3.Connection, session_id: int = 1, keep: int = 100):
    with _WRITE_LOCK:
        conn.execute(
            """DELETE FROM listings
               WHERE session_id = ? AND id NOT IN (
                   SELECT id FROM listings WHERE session_id = ?
                   ORDER BY first_seen DESC, rowid DESC LIMIT ?
               )""",
            (session_id, session_id, keep),
        )
        conn.commit()


def get_relevant_listings(conn: sqlite3.Connection, session_id: int = 1, include_rejected: bool = False) -> list[dict]:
    if include_rejected:
        query = "SELECT * FROM listings WHERE session_id = ? AND relevant IS NOT NULL ORDER BY first_seen DESC"
    else:
        query = "SELECT * FROM listings WHERE session_id = ? AND relevant = 1 ORDER BY first_seen DESC"
    cur = conn.execute(query, (session_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_unevaluated_listings(conn: sqlite3.Connection, session_id: int = 1) -> list[dict]:
    cur = conn.execute(
        "SELECT * FROM listings WHERE session_id = ? AND agent_evaluated_at IS NULL", (session_id,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_all_listings(conn: sqlite3.Connection, session_id: int = 1) -> list[dict]:
    cur = conn.execute("SELECT * FROM listings WHERE session_id = ?", (session_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_relevance(conn: sqlite3.Connection, item_id: str, relevant: int, reason: str, session_id: int = 1):
    now = datetime.now().isoformat(timespec="seconds")
    with _WRITE_LOCK:
        conn.execute(
            "UPDATE listings SET relevant = ?, relevance_reason = ?, agent_evaluated_at = ? WHERE id = ? AND session_id = ?",
            (relevant, reason, now, item_id, session_id),
        )
        conn.commit()


def mark_all_for_rescore(conn: sqlite3.Connection, session_id: int = 1):
    with _WRITE_LOCK:
        conn.execute(
            "UPDATE listings SET relevant = NULL, relevance_reason = NULL, agent_evaluated_at = NULL WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()


def get_search_config(conn: sqlite3.Connection, session_id: int = 1):
    cur = conn.execute(
        """SELECT session_id, label, city, brand, price_min, price_max, mileage_min, mileage_max,
                  year_min, year_max, query_text, search_url, updated_at
           FROM search_config WHERE session_id = ?""",
        (session_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def save_search_config(conn: sqlite3.Connection, cfg: dict, session_id: int = 1):
    now = datetime.now().isoformat(timespec="seconds")
    with _WRITE_LOCK:
        conn.execute(
            """INSERT INTO search_config
               (session_id, label, city, brand, price_min, price_max, mileage_min, mileage_max,
                year_min, year_max, query_text, search_url, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                 label=excluded.label, city=excluded.city, brand=excluded.brand,
                 price_min=excluded.price_min, price_max=excluded.price_max,
                 mileage_min=excluded.mileage_min, mileage_max=excluded.mileage_max,
                 year_min=excluded.year_min, year_max=excluded.year_max,
                 query_text=excluded.query_text, search_url=excluded.search_url,
                 updated_at=excluded.updated_at""",
            (
                session_id, cfg.get("label"), cfg.get("city"), cfg.get("brand"),
                cfg.get("price_min"), cfg.get("price_max"),
                cfg.get("mileage_min"), cfg.get("mileage_max"),
                cfg.get("year_min"), cfg.get("year_max"),
                cfg.get("query_text"), cfg.get("search_url"), now,
            ),
        )
        conn.commit()


def set_last_check(conn: sqlite3.Connection, count: int, session_id: int = 1):
    now = datetime.now().isoformat(timespec="seconds")
    with _WRITE_LOCK:
        conn.execute(
            """INSERT INTO worker_status (session_id, last_check, last_count) VALUES (?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                 last_check = excluded.last_check, last_count = excluded.last_count""",
            (session_id, now, count),
        )
        conn.commit()


def get_status(conn: sqlite3.Connection, session_id: int = 1) -> dict:
    cur = conn.execute(
        """SELECT last_check, last_count, paused, pause_reason, last_error, scanning
           FROM worker_status WHERE session_id = ?""",
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


def set_paused(conn: sqlite3.Connection, paused: bool, reason: str | None = None, error: str | None = None, session_id: int = 1):
    with _WRITE_LOCK:
        conn.execute(
            """INSERT INTO worker_status (session_id, last_count, paused, pause_reason, last_error)
               VALUES (?, 0, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                 paused = excluded.paused, pause_reason = excluded.pause_reason,
                 last_error = excluded.last_error""",
            (session_id, 1 if paused else 0, reason, error),
        )
        conn.commit()


def set_scanning(conn: sqlite3.Connection, scanning: bool, session_id: int = 1):
    with _WRITE_LOCK:
        conn.execute(
            """INSERT INTO worker_status (session_id, last_count, scanning) VALUES (?, 0, ?)
               ON CONFLICT(session_id) DO UPDATE SET scanning = excluded.scanning""",
            (session_id, 1 if scanning else 0),
        )
        conn.commit()
