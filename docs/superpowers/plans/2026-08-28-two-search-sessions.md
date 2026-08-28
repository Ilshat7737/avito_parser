# Две поисковые сессии — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в веб-режим `avito_parser` поддержку двух независимых поисковых сессий (разные фильтры, разные результаты), которые фоновый воркер обрабатывает по очереди в одном потоке — без увеличения частоты запросов к Avito, — с кросс-сессионным дедупом объявлений на 24 часа.

**Architecture:** `session_id ∈ {1, 2}` становится параметром почти всех функций `db.py` и всего пути `worker.py`; `listings`/`seen_ids` получают composite PK `(session_id, id)` через миграцию-пересборку таблицы, `search_config`/`worker_status` получают ослабленный `CHECK (id IN (1, 2))` той же техникой; новая таблица `cross_session_claims` хранит, какая сессия первой обработала объявление и когда. `background_loop` чередует сессии по одной за итерацию вместо параллельного опроса. Flask-эндпоинты и фронтенд передают `session_id` явным параметром (query string для GET, JSON-поле для POST); в браузере это `?session=1|2` в URL.

**Tech Stack:** Python 3.11, SQLite (`sqlite3` из стандартной библиотеки), Flask, pytest + `monkeypatch`, React + TypeScript (Vite).

**Spec:** `TestTask/SPEC.md`, правила проекта — `TestTask/AGENTS.md` (Часть II)

## Global Constraints

- Не увеличивать частоту запросов к Avito: `MIN_DELAY`, `MAX_DELAY`, `CHECK_INTERVAL_SEC`, `INTERVAL_JITTER_SEC` не менять.
- Ровно 2 сессии, `session_id ∈ {1, 2}`, никакого произвольного N.
- Никакой настоящей многопоточности/параллельных браузеров — один поток, одна сессия за итерацию `background_loop`.
- `HEADLESS` не менять на `True`.
- Миграция схемы БД — только пересборкой таблиц внутри существующего файла (`RENAME` → `CREATE` → `INSERT ... SELECT` → `DROP`), никогда через удаление/пересоздание файла БД целиком.
- CLI-режим (`main.py`, `DB_PATH`) не трогать — вся работа в веб-режиме (`WEB_DB_PATH`).
- Комментарии, логи, сообщения — на русском.

---

## Task 1: Схема БД и функции `db.py` — поддержка сессий

**Files:**
- Modify: `db.py` (весь файл — схемы, миграция, все публичные функции)
- Test: `tests/test_db.py` (весь файл — фикстура и все существующие тесты + новые)

**Interfaces:**
- Produces (используется в Task 2 и Task 3):
  - `db.get_conn(db_path: str) -> sqlite3.Connection` — без изменений сигнатуры, миграция внутри.
  - `db.get_listing(conn, session_id: int, item_id: str) -> dict | None`
  - `db.upsert_new(conn, session_id: int, item: dict) -> None`
  - `db.update_price(conn, session_id: int, item_id: str, new_price: int) -> None`
  - `db.touch(conn, session_id: int, item_id: str) -> None`
  - `db.is_seen(conn, session_id: int, item_id: str) -> bool`
  - `db.mark_seen(conn, session_id: int, item_id: str) -> None`
  - `db.evict_old_listings(conn, session_id: int, keep: int = 100) -> None`
  - `db.get_relevant_listings(conn, session_id: int, include_rejected: bool = False) -> list[dict]`
  - `db.get_unevaluated_listings(conn, session_id: int) -> list[dict]`
  - `db.get_all_listings(conn, session_id: int) -> list[dict]`
  - `db.update_relevance(conn, session_id: int, item_id: str, relevant: int, reason: str) -> None`
  - `db.mark_all_for_rescore(conn, session_id: int) -> None`
  - `db.get_search_config(conn, session_id: int) -> dict | None`
  - `db.save_search_config(conn, session_id: int, cfg: dict) -> None`
  - `db.set_last_check(conn, session_id: int, count: int) -> None`
  - `db.get_status(conn, session_id: int) -> dict`
  - `db.set_paused(conn, session_id: int, paused: bool, reason: str | None = None, error: str | None = None) -> None`
  - `db.set_scanning(conn, session_id: int, scanning: bool) -> None`
  - `db.is_claimed_by_other_session(conn, session_id: int, item_id: str) -> bool` — окно 24ч зашито внутри (`CLAIM_WINDOW_HOURS = 24`).
  - `db.claim_for_session(conn, session_id: int, item_id: str) -> None` — заодно удаляет клеймы старше 24ч.

### Шаг 1.1 — Обновить фикстуру и написать падающие тесты на новую схему

- [ ] Переписать фикстуру `conn` в `tests/test_db.py`, добавить `db.CROSS_SESSION_CLAIMS_SCHEMA`:

```python
@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(db.SCHEMA)
    c.execute(db.SEEN_IDS_SCHEMA)
    c.execute(db.SEARCH_CONFIG_SCHEMA)
    c.execute(db.WORKER_STATUS_SCHEMA)
    c.execute(db.CROSS_SESSION_CLAIMS_SCHEMA)
    c.commit()
    return c
```

- [ ] Добавить в конец `tests/test_db.py` новые тесты (падают, пока `db.py` не переписан):

```python
def test_listings_scoped_per_session_same_id_independent(conn):
    db.upsert_new(conn, 1, {"id": "1", "title": "Session 1 copy", "price": 100, "url": "u"})
    db.upsert_new(conn, 2, {"id": "1", "title": "Session 2 copy", "price": 200, "url": "u"})

    assert db.get_listing(conn, 1, "1")["title"] == "Session 1 copy"
    assert db.get_listing(conn, 2, "1")["title"] == "Session 2 copy"


def test_evict_old_listings_only_evicts_within_own_session(conn):
    for i in range(3):
        db.upsert_new(conn, 1, {"id": f"s1-{i}", "title": "x", "price": 100, "url": "u"})
    db.upsert_new(conn, 2, {"id": "s2-0", "title": "x", "price": 100, "url": "u"})

    db.evict_old_listings(conn, 1, keep=1)

    remaining_s1 = {r["id"] for r in db.get_all_listings(conn, 1)}
    remaining_s2 = {r["id"] for r in db.get_all_listings(conn, 2)}
    assert len(remaining_s1) == 1
    assert remaining_s2 == {"s2-0"}  # эвикшен сессии 1 не задел сессию 2


def test_seen_ids_scoped_per_session(conn):
    db.mark_seen(conn, 1, "x")
    assert db.is_seen(conn, 1, "x") is True
    assert db.is_seen(conn, 2, "x") is False


def test_search_config_and_worker_status_allow_both_session_ids(conn):
    db.save_search_config(conn, 1, {"city": "kazan"})
    db.save_search_config(conn, 2, {"city": "moskva"})
    assert db.get_search_config(conn, 1)["city"] == "kazan"
    assert db.get_search_config(conn, 2)["city"] == "moskva"

    db.set_paused(conn, 1, True, "manual")
    assert db.get_status(conn, 1)["paused"] is True
    assert db.get_status(conn, 2)["paused"] is False


def test_claim_for_session_blocks_other_session_within_24h(conn):
    assert db.is_claimed_by_other_session(conn, 2, "shared-id") is False

    db.claim_for_session(conn, 1, "shared-id")

    assert db.is_claimed_by_other_session(conn, 2, "shared-id") is True
    assert db.is_claimed_by_other_session(conn, 1, "shared-id") is False  # своя же сессия не блокирует сама себя


def test_claim_older_than_24h_no_longer_blocks(conn):
    from datetime import datetime, timedelta

    old_claim = (datetime.now() - timedelta(hours=25)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO cross_session_claims (id, session_id, claimed_at) VALUES (?, ?, ?)",
        ("shared-id", 1, old_claim),
    )
    conn.commit()

    assert db.is_claimed_by_other_session(conn, 2, "shared-id") is False


def test_get_conn_migrates_legacy_single_session_db_to_session_1(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    old_conn = sqlite3.connect(db_path)
    old_conn.execute("""
        CREATE TABLE listings (
            id TEXT PRIMARY KEY, title TEXT, price INTEGER, url TEXT, photo TEXT,
            description TEXT, year INTEGER, mileage INTEGER, relevant INTEGER,
            relevance_reason TEXT, agent_evaluated_at TEXT, first_seen TEXT,
            last_price INTEGER, last_checked TEXT
        )
    """)
    old_conn.execute(
        "INSERT INTO listings (id, title, price, url, first_seen, last_price, last_checked) "
        "VALUES ('legacy-1', 'Старое объявление', 100000, 'u', '2026-01-01T00:00:00', 100000, '2026-01-01T00:00:00')"
    )
    old_conn.execute("CREATE TABLE seen_ids (id TEXT PRIMARY KEY, first_seen TEXT)")
    old_conn.execute("INSERT INTO seen_ids (id, first_seen) VALUES ('legacy-1', '2026-01-01T00:00:00')")
    old_conn.execute("""
        CREATE TABLE search_config (
            id INTEGER PRIMARY KEY CHECK (id = 1), city TEXT, brand TEXT,
            price_min INTEGER, price_max INTEGER, mileage_min INTEGER, mileage_max INTEGER,
            year_min INTEGER, year_max INTEGER, query_text TEXT, search_url TEXT, updated_at TEXT
        )
    """)
    old_conn.execute(
        "INSERT INTO search_config (id, city, updated_at) VALUES (1, 'kazan', '2026-01-01T00:00:00')"
    )
    old_conn.execute("""
        CREATE TABLE worker_status (
            id INTEGER PRIMARY KEY CHECK (id = 1), last_check TEXT, last_count INTEGER,
            paused INTEGER DEFAULT 0, pause_reason TEXT, last_error TEXT, scanning INTEGER DEFAULT 0
        )
    """)
    old_conn.execute("INSERT INTO worker_status (id, last_count) VALUES (1, 7)")
    old_conn.commit()
    old_conn.close()

    conn = db.get_conn(db_path)

    # старые данные сохранились и стали сессией 1
    assert db.get_listing(conn, 1, "legacy-1")["title"] == "Старое объявление"
    assert db.is_seen(conn, 1, "legacy-1") is True
    assert db.get_search_config(conn, 1)["city"] == "kazan"
    assert db.get_status(conn, 1)["last_count"] == 7

    # сессия 2 пуста, но доступна — миграция и её пересобрала
    assert db.get_all_listings(conn, 2) == []
    db.save_search_config(conn, 2, {"city": "moskva"})
    assert db.get_search_config(conn, 2)["city"] == "moskva"
```

- [ ] Запустить: `python3 -m pytest tests/test_db.py -v`
      Ожидаемо: новые тесты падают (`TypeError` — функции ещё принимают старую сигнатуру), существующие тоже начнут падать после следующего шага фикстуры — это нормально для этого чекпоинта, дальше чиним всё разом в шаге 1.2.

### Шаг 1.2 — Переписать `db.py`

- [ ] Заменить содержимое `db.py` целиком на:

```python
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
    seen_ids), DROP старой. Никакого удаления файла БД."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
    if "session_id" in existing:
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

    conn.commit()


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
```

- [ ] Обновить **все существующие** вызовы функций `db.py` в `tests/test_db.py`,
      вставив `1` вторым позиционным аргументом (сессия 1) в каждый вызов
      `db.get_listing`, `db.upsert_new`, `db.update_price`, `db.touch`,
      `db.is_seen`, `db.mark_seen`, `db.evict_old_listings`,
      `db.get_relevant_listings`, `db.get_unevaluated_listings`,
      `db.get_all_listings`, `db.update_relevance`, `db.mark_all_for_rescore`,
      `db.get_search_config`, `db.save_search_config`, `db.set_last_check`,
      `db.get_status`, `db.set_paused`, `db.set_scanning`. Пример
      преобразования (применить ту же схему ко всем остальным вызовам в
      файле):

  ```python
  # было:  db.upsert_new(conn, item)
  # стало: db.upsert_new(conn, 1, item)

  # было:  db.get_status(conn)
  # стало: db.get_status(conn, 1)
  ```

  Также заменить `db.get_conn_migrates_old_worker_status_schema`: старый
  тест `test_get_conn_migrates_old_worker_status_schema` по-прежнему
  актуален и должен пройти без изменений в логике теста (только вызовы
  `db.get_status(conn)` → `db.get_status(conn, 1)`), так как эта миграция
  происходит раньше миграции сессий и не конфликтует с ней.

- [ ] Запустить: `python3 -m pytest tests/test_db.py -v`
      Ожидаемо: все тесты (старые и новые) проходят.

- [ ] Закоммитить:

```bash
git add db.py tests/test_db.py
git commit -m "feat(db): поддержка двух поисковых сессий и кросс-сессионного дедупа"
```

---

## Task 2: `worker.py` — чередование сессий и кросс-сессионный claim

**Files:**
- Modify: `worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: все функции `db.py` из Task 1 (сигнатуры с `session_id`).
- Produces (используется в Task 3):
  - `worker.process_cycle(conn, session_id: int, items, query_text, fetch_description_fn, keep=100)`
  - `worker.run_search_cycle(conn, session_id: int, page, search_url, query_text) -> int | None`
  - `worker.run_cycle_with_safety(conn, session_id: int, cycle_fn) -> object | None`
  - `worker.background_loop(conn, stop_event)` — сигнатура не меняется, чередование сессий внутри.

### Шаг 2.1 — Обновить фикстуру и написать падающие тесты

- [ ] В `tests/test_worker.py` добавить `c.execute(db.CROSS_SESSION_CLAIMS_SCHEMA)` в фикстуру `conn` (аналогично Task 1, Шаг 1.1).

- [ ] Добавить новые тесты в конец `tests/test_worker.py`:

```python
def test_process_cycle_skips_item_claimed_by_other_session_within_24h(conn, monkeypatch):
    db.claim_for_session(conn, 1, "shared")
    called = {"agent": False}

    def spy_evaluate(items, query_text):
        called["agent"] = True
        return {}

    monkeypatch.setattr(worker.agent, "evaluate_listings", spy_evaluate)
    items = [{"id": "shared", "title": "Дублируется во второй сессии", "price": 100000, "url": "u"}]

    worker.process_cycle(conn, 2, items, "текст", fake_fetch_description)

    assert db.get_listing(conn, 2, "shared") is None
    assert db.is_seen(conn, 2, "shared") is False  # не помечено виденным — может подхватиться после 24ч
    assert called["agent"] is False


def test_process_cycle_claims_new_item_for_its_session(conn, monkeypatch):
    monkeypatch.setattr(
        worker.agent, "evaluate_listings",
        lambda items, query_text: {"1": {"relevant": True, "reason": "ok"}},
    )
    items = [{"id": "1", "title": "Kia Rio", "price": 700000, "url": "u1"}]

    worker.process_cycle(conn, 1, items, "текст", fake_fetch_description)

    assert db.is_claimed_by_other_session(conn, 2, "1") is True


def test_process_cycle_is_scoped_to_its_own_session(conn, monkeypatch):
    db.upsert_new(conn, 2, {"id": "1", "title": "Уже в сессии 2", "price": 100000, "url": "u"})
    monkeypatch.setattr(
        worker.agent, "evaluate_listings",
        lambda items, query_text: {"1": {"relevant": True, "reason": "ok"}},
    )
    items = [{"id": "1", "title": "Kia Rio", "price": 700000, "url": "u1"}]

    worker.process_cycle(conn, 1, items, "текст", fake_fetch_description)

    assert db.get_listing(conn, 1, "1") is not None  # появилось в сессии 1
    assert db.get_listing(conn, 2, "1")["title"] == "Уже в сессии 2"  # сессия 2 не задета


def test_background_loop_alternates_sessions_across_iterations(conn, monkeypatch):
    db.save_search_config(conn, 1, {"city": "kazan", "search_url": "https://example.com/1"})
    db.save_search_config(conn, 2, {"city": "moskva", "search_url": "https://example.com/2"})

    processed_sessions = []

    def fake_run_cycle_with_safety(conn, session_id, cycle_fn):
        processed_sessions.append(session_id)
        return 0

    monkeypatch.setattr(worker, "run_cycle_with_safety", fake_run_cycle_with_safety)
    monkeypatch.setattr(worker.config, "CHECK_INTERVAL_SEC", 0)
    monkeypatch.setattr(worker.config, "INTERVAL_JITTER_SEC", 0)

    stop_event = threading.Event()
    call_count = {"n": 0}
    original_wait = stop_event.wait

    def wait_and_stop_after(seconds):
        call_count["n"] += 1
        if call_count["n"] >= 4:
            stop_event.set()
        return original_wait(0)

    stop_event.wait = wait_and_stop_after
    worker.background_loop(conn, stop_event)

    assert processed_sessions == [1, 2, 1, 2]
```

  (Добавить `import threading` в начало `tests/test_worker.py`, если его
  там ещё нет.)

- [ ] Запустить: `python3 -m pytest tests/test_worker.py -v`
      Ожидаемо: новые тесты падают, существующие — падают из-за смены сигнатур (чиним в следующем шаге).

### Шаг 2.2 — Переписать `worker.py`

- [ ] Заменить содержимое `worker.py` на:

```python
"""
Фоновый цикл опроса Avito для веб-режима: скрапинг -> dedup ->
описание для новых -> батч-оценка агентом -> запись в БД -> уведомления ->
обрезка listings до лимита. Живёт в daemon-потоке, поднятом из app.py.

Поддерживает ровно две независимые поисковые сессии (session_id ∈
{1, 2}). Чтобы не увеличивать частоту запросов к Avito, фоновый цикл не
опрашивает обе сессии одновременно — один поток, одна сессия за
итерацию, сессии чередуются. Кросс-сессионный дедуп (cross_session_claims,
db.py) не даёт одному и тому же объявлению попасть в обе сессии в
течение 24 часов: та сессия, что обработала объявление первой, "клеймит"
его, вторая тихо пропускает.
"""

import random
import threading

import agent
import config
import db
from notify import console_log, notify_new_item, notify_price_change
from scraper import USER_AGENT, fetch_description, fetch_search_results, is_blocked_or_captcha

PAUSE_POLL_SEC = 3
# Размер группы для чанкинга описание+оценка+запись внутри одного цикла —
# совпадает с agent.BATCH_SIZE, чтобы не увеличивать число вызовов агента
# (каждая группа = ровно один вызов agent.evaluate_listings). Обрабатываем
# по группам, а не всё сразу, чтобы уже готовые объявления появлялись на
# экране (опрос /api/listings раз в 3 сек) по мере готовности, а не одним
# большим куском в самом конце цикла — при MIN_DELAY/MAX_DELAY=20-30 сек
# между запросами описаний цикл с десятками новых объявлений может идти
# много минут, и ждать его целиком, прежде чем показать хоть что-то, не
# годится.
CHUNK_SIZE = agent.BATCH_SIZE


def process_cycle(conn, session_id: int, items: list[dict], query_text: str, fetch_description_fn, keep: int = 100):
    new_items = []
    for item in items:
        existing = db.get_listing(conn, session_id, item["id"])
        if existing is not None:
            if item["price"] is not None and existing["price"] != item["price"]:
                notify_price_change(existing, item)
                db.update_price(conn, session_id, item["id"], item["price"])
            else:
                db.touch(conn, session_id, item["id"])
            continue

        if db.is_seen(conn, session_id, item["id"]):
            continue

        if db.is_claimed_by_other_session(conn, session_id, item["id"]):
            # Соседняя сессия уже обработала это объявление менее 24ч
            # назад — не показываем его здесь и не помечаем "виденным",
            # чтобы после истечения окна эта сессия могла подхватить его
            # как новое (см. SPEC.md, сценарий 4).
            continue

        new_items.append(item)

    if not new_items:
        return

    stripped_query = query_text.strip()
    inserted_any = False

    for start in range(0, len(new_items), CHUNK_SIZE):
        chunk = new_items[start:start + CHUNK_SIZE]

        for item in chunk:
            try:
                item["description"] = fetch_description_fn(item["url"])
            except Exception:
                item["description"] = ""

        if not stripped_query:
            verdicts = {item["id"]: {"relevant": True, "reason": "критерий не задан"} for item in chunk}
        else:
            verdicts = agent.evaluate_listings(chunk, stripped_query)

        for item in chunk:
            verdict = verdicts.get(item["id"])
            if verdict is None:
                continue  # ошибка агента — попробуем снова в следующем цикле

            item["relevant"] = 1 if verdict["relevant"] else 0
            item["relevance_reason"] = verdict["reason"]
            db.upsert_new(conn, session_id, item)
            db.mark_seen(conn, session_id, item["id"])
            db.claim_for_session(conn, session_id, item["id"])
            inserted_any = True
            if verdict["relevant"]:
                notify_new_item(item)

    if inserted_any:
        db.evict_old_listings(conn, session_id, keep=keep)


def run_search_cycle(conn, session_id: int, page, search_url: str, query_text: str) -> int | None:
    """Один проход по уже открытой странице: скрапинг -> process_cycle.
    Если после загрузки страницы похоже на капчу/блокировку — ставит
    паузу (`pause_reason="blocked"`) этой сессии и возвращает None, не
    поднимая исключение (чтобы фоновый цикл не считал это обычным сбоем
    и не повторил попытку сразу же). Любая другая ошибка поднимается
    дальше — её обрабатывает уже существующий широкий try/except в
    background_loop."""
    try:
        items = fetch_search_results(page, search_url)
    except Exception:
        if is_blocked_or_captcha(page):
            console_log(
                f"⚠️ [Сессия {session_id}] Avito показывает капчу или заблокировал "
                "доступ — парсинг этой сессии приостановлен. Решите капчу в открытом "
                "окне браузера (если оно ещё открыто) или подождите, затем нажмите "
                "«Возобновить» в интерфейсе."
            )
            db.set_paused(conn, session_id, True, "blocked")
            return None
        raise

    process_cycle(conn, session_id, items, query_text, lambda url: fetch_description(page, url))
    return len(items)


def run_cycle_with_safety(conn, session_id: int, cycle_fn):
    """Оборачивает один цикл сканирования одной сессии: выставляет
    scanning=True на время выполнения (для индикатора прогрузки в
    интерфейсе) и гарантированно сбрасывает обратно в False. При любой
    непредвиденной ошибке (капча/блокировка уже обработана отдельно
    внутри run_search_cycle и сюда не долетает) ставит паузу этой сессии
    с причиной "error" и текстом ошибки — вместо бесконечных
    автоматических повторов, по прямой просьбе пользователя: один раз не
    получилось — значит ждём ручного вмешательства, а не долбим Avito
    дальше. Пауза одной сессии не трогает статус другой."""
    db.set_scanning(conn, session_id, True)
    try:
        return cycle_fn()
    except Exception as e:
        console_log(f"[Сессия {session_id}] Ошибка фонового цикла: {e}")
        db.set_paused(conn, session_id, True, "error", str(e))
        return None
    finally:
        db.set_scanning(conn, session_id, False)


def background_loop(conn, stop_event: threading.Event):
    console_log("Фоновый поток веб-приложения запущен")
    from playwright.sync_api import sync_playwright

    current_session_id = 1

    while not stop_event.is_set():
        session_id = current_session_id
        current_session_id = 2 if current_session_id == 1 else 1

        status = db.get_status(conn, session_id)
        if status["paused"]:
            stop_event.wait(PAUSE_POLL_SEC)
            continue

        cfg = db.get_search_config(conn, session_id)
        if cfg and cfg.get("search_url"):
            def do_cycle(cfg=cfg, session_id=session_id):
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=config.HEADLESS)
                    context = browser.new_context(user_agent=USER_AGENT, locale="ru-RU")
                    page = context.new_page()
                    count = run_search_cycle(
                        conn, session_id, page, cfg["search_url"], cfg.get("query_text") or ""
                    )
                    browser.close()
                    return count

            count = run_cycle_with_safety(conn, session_id, do_cycle)
            if count is not None:
                db.set_last_check(conn, session_id, count=count)
                console_log(f"[Сессия {session_id}] Проверено объявлений: {count}")
        else:
            console_log(f"[Сессия {session_id}] Поиск ещё не настроен — жду настроек через веб-интерфейс")

        delay = config.CHECK_INTERVAL_SEC + random.uniform(0, config.INTERVAL_JITTER_SEC)
        stop_event.wait(delay)
```

- [ ] Обновить **все существующие** вызовы в `tests/test_worker.py`,
      вставив `1` (или `2`, где по смыслу теста нужна другая сессия)
      вторым позиционным аргументом в каждый вызов `worker.process_cycle`,
      `worker.run_search_cycle`, `worker.run_cycle_with_safety`,
      `db.get_listing`, `db.is_seen`, `db.get_status`, `db.upsert_new`,
      `db.mark_seen` — по той же схеме, что в Task 1. Пример:

  ```python
  # было:  worker.process_cycle(conn, items, "надёжная машина", fake_fetch_description)
  # стало: worker.process_cycle(conn, 1, items, "надёжная машина", fake_fetch_description)

  # было:  worker.run_search_cycle(conn, object(), "https://example.com", "текст")
  # стало: worker.run_search_cycle(conn, 1, object(), "https://example.com", "текст")
  ```

- [ ] Запустить: `python3 -m pytest tests/test_worker.py -v`
      Ожидаемо: все тесты проходят.

- [ ] Закоммитить:

```bash
git add worker.py tests/test_worker.py
git commit -m "feat(worker): чередование двух сессий в одном потоке + кросс-сессионный claim"
```

---

## Task 3: `app.py` — `session_id` в HTTP API

**Files:**
- Modify: `app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: все функции `db.py` из Task 1.
- Produces: та же схема эндпоинтов, но каждый принимает `session_id`
  (query-параметр `?session_id=` для GET, поле `"session_id"` в JSON-теле
  для POST); по умолчанию — `1`. Некорректное значение (не `1`/`2`) → `400`
  с телом `{"error": "session_id должен быть 1 или 2"}`.

### Шаг 3.1 — Обновить фикстуру и написать падающие тесты

- [ ] В `tests/test_app.py` добавить `c.execute(db.CROSS_SESSION_CLAIMS_SCHEMA)` в фикстуру `conn`.

- [ ] Добавить новые тесты в конец `tests/test_app.py`:

```python
def test_api_listings_session_1_and_2_are_independent(client, conn):
    db.upsert_new(conn, 1, {"id": "1", "title": "Сессия 1", "price": 100, "url": "u1", "relevant": 1})
    db.upsert_new(conn, 2, {"id": "2", "title": "Сессия 2", "price": 100, "url": "u2", "relevant": 1})

    resp1 = client.get("/api/listings?session_id=1")
    resp2 = client.get("/api/listings?session_id=2")

    assert [i["id"] for i in resp1.get_json()] == ["1"]
    assert [i["id"] for i in resp2.get_json()] == ["2"]


def test_api_listings_defaults_to_session_1_without_param(client, conn):
    db.upsert_new(conn, 1, {"id": "1", "title": "Сессия 1", "price": 100, "url": "u1", "relevant": 1})

    resp = client.get("/api/listings")

    assert [i["id"] for i in resp.get_json()] == ["1"]


def test_api_listings_rejects_invalid_session_id(client):
    resp = client.get("/api/listings?session_id=3")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_api_config_post_stores_config_under_its_own_session(client, conn, monkeypatch):
    monkeypatch.setattr(app_module, "build_search_url", lambda **kwargs: "https://example.com/2")

    resp = client.post("/api/config", json={"session_id": 2, "city": "moskva"})

    assert resp.status_code == 200
    assert db.get_search_config(conn, 2)["city"] == "moskva"
    assert db.get_search_config(conn, 1) is None


def test_api_worker_pause_only_affects_its_own_session(client, conn):
    resp = client.post("/api/worker/pause", json={"session_id": 2})

    assert resp.status_code == 200
    assert db.get_status(conn, 2)["paused"] is True
    assert db.get_status(conn, 1)["paused"] is False
```

- [ ] Запустить: `python3 -m pytest tests/test_app.py -v`
      Ожидаемо: новые тесты падают, существующие ломаются на следующем шаге (все db.py-вызовы внутри app.py требуют session_id).

### Шаг 3.2 — Переписать `app.py`

- [ ] Заменить содержимое `app.py` на:

```python
"""
Flask-приложение: отдаёт фронтенд (frontend/dist) и JSON API, поднимает
фоновый поток опроса Avito при старте. Поддерживает две независимые
поисковые сессии (session_id ∈ {1, 2}) — см. worker.py и db.py.
"""

import os
import threading

from flask import Flask, jsonify, request, send_from_directory

import agent
import config
import db
from url_builder import build_search_url
from worker import background_loop

FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")


class InvalidSessionId(ValueError):
    pass


def _parse_session_id(raw) -> int:
    if raw is None:
        return 1
    try:
        session_id = int(raw)
    except (TypeError, ValueError):
        raise InvalidSessionId("session_id должен быть 1 или 2")
    if session_id not in (1, 2):
        raise InvalidSessionId("session_id должен быть 1 или 2")
    return session_id


def create_app(conn) -> Flask:
    flask_app = Flask(__name__, static_folder=None)

    @flask_app.route("/api/listings")
    def api_listings():
        try:
            session_id = _parse_session_id(request.args.get("session_id"))
        except InvalidSessionId as e:
            return jsonify({"error": str(e)}), 400
        include_rejected = request.args.get("include_rejected") == "1"
        return jsonify(db.get_relevant_listings(conn, session_id, include_rejected))

    @flask_app.route("/api/status")
    def api_status():
        try:
            session_id = _parse_session_id(request.args.get("session_id"))
        except InvalidSessionId as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(db.get_status(conn, session_id))

    @flask_app.route("/api/config", methods=["GET"])
    def api_config_get():
        try:
            session_id = _parse_session_id(request.args.get("session_id"))
        except InvalidSessionId as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(db.get_search_config(conn, session_id) or {})

    @flask_app.route("/api/config", methods=["POST"])
    def api_config_post():
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "Ожидается тело запроса в формате JSON"}), 400
        try:
            session_id = _parse_session_id(payload.get("session_id"))
        except InvalidSessionId as e:
            return jsonify({"error": str(e)}), 400
        try:
            search_url = build_search_url(
                city=payload["city"],
                brand=payload.get("brand"),
                price_min=payload.get("price_min"),
                price_max=payload.get("price_max"),
                mileage_min=payload.get("mileage_min"),
                mileage_max=payload.get("mileage_max"),
                year_min=payload.get("year_min"),
                year_max=payload.get("year_max"),
            )
        except Exception as e:
            return jsonify({"error": f"Не удалось собрать ссылку поиска: {e}"}), 400

        payload["search_url"] = search_url
        db.save_search_config(conn, session_id, payload)
        return jsonify(db.get_search_config(conn, session_id))

    @flask_app.route("/api/rescore", methods=["POST"])
    def api_rescore():
        payload = request.get_json(silent=True) or {}
        try:
            session_id = _parse_session_id(payload.get("session_id"))
        except InvalidSessionId as e:
            return jsonify({"error": str(e)}), 400

        cfg = db.get_search_config(conn, session_id)
        query_text = ((cfg or {}).get("query_text") or "").strip()
        items = db.get_all_listings(conn, session_id)

        if not query_text:
            for item in items:
                db.update_relevance(conn, session_id, item["id"], 1, "критерий не задан")
            return jsonify({"rescored": len(items)})

        verdicts = agent.evaluate_listings(items, query_text)
        for item in items:
            verdict = verdicts.get(item["id"])
            if verdict is None:
                continue
            db.update_relevance(conn, session_id, item["id"], 1 if verdict["relevant"] else 0, verdict["reason"])
        return jsonify({"rescored": len(verdicts)})

    @flask_app.route("/api/worker/pause", methods=["POST"])
    def api_worker_pause():
        payload = request.get_json(silent=True) or {}
        try:
            session_id = _parse_session_id(payload.get("session_id"))
        except InvalidSessionId as e:
            return jsonify({"error": str(e)}), 400
        db.set_paused(conn, session_id, True, "manual")
        return jsonify(db.get_status(conn, session_id))

    @flask_app.route("/api/worker/resume", methods=["POST"])
    def api_worker_resume():
        payload = request.get_json(silent=True) or {}
        try:
            session_id = _parse_session_id(payload.get("session_id"))
        except InvalidSessionId as e:
            return jsonify({"error": str(e)}), 400
        db.set_paused(conn, session_id, False, None)
        return jsonify(db.get_status(conn, session_id))

    @flask_app.route("/", defaults={"path": ""})
    @flask_app.route("/<path:path>")
    def serve_frontend(path):
        full_path = os.path.join(FRONTEND_DIST, path) if path else None
        if path and full_path and os.path.exists(full_path):
            return send_from_directory(FRONTEND_DIST, path)
        return send_from_directory(FRONTEND_DIST, "index.html")

    return flask_app


def main():
    conn = db.get_conn(config.WEB_DB_PATH)
    # На случай, если предыдущий запуск был прерван не штатно (например,
    # процесс убит посреди цикла) — scanning=True мог остаться в БД
    # навсегда для одной или обеих сессий, потому что db.set_scanning(...,
    # False) в worker.py выполняется в finally и не срабатывает при
    # жёстком убийстве процесса. При старте ничего ещё не сканируется —
    # сбрасываем явно для обеих сессий.
    db.set_scanning(conn, 1, False)
    db.set_scanning(conn, 2, False)
    stop_event = threading.Event()
    thread = threading.Thread(target=background_loop, args=(conn, stop_event), daemon=True)
    thread.start()

    flask_app = create_app(conn)
    flask_app.run(host="127.0.0.1", port=config.WEB_PORT, debug=False)


if __name__ == "__main__":
    main()
```

- [ ] Обновить **все существующие** вызовы `db.*` в `tests/test_app.py`
      (в самих тестах, не через HTTP-клиент), добавив `1` вторым
      позиционным аргументом — та же схема, что в Task 1/2. Пример:

  ```python
  # было:  db.upsert_new(conn, {"id": "1", ...})
  # стало: db.upsert_new(conn, 1, {"id": "1", ...})

  # было:  db.get_listing(conn, "1")["relevant"]
  # стало: db.get_listing(conn, 1, "1")["relevant"]
  ```

  HTTP-запросы через `client.get(...)`/`client.post(...)` без явного
  `session_id` продолжают работать как раньше (дефолт — сессия 1), их
  менять не нужно, кроме новых тестов из Шага 3.1.

- [ ] Запустить: `python3 -m pytest tests/test_app.py -v`
      Ожидаемо: все тесты проходят.

- [ ] Запустить весь набор тестов проекта: `python3 -m pytest -v`
      Ожидаемо: все тесты (db/worker/app/agent/scraper/url_builder) проходят.

- [ ] Закоммитить:

```bash
git add app.py tests/test_app.py
git commit -m "feat(api): session_id как параметр HTTP API веб-режима"
```

---

## Task 4: Фронтенд — переключение сессии по URL

**Files:**
- Modify: `frontend/src/api.ts`, `frontend/src/App.tsx`

**Interfaces:**
- Consumes: HTTP API из Task 3 (`session_id` как query-параметр/JSON-поле).
- Produces: страница читает `?session=1|2` из URL при загрузке и передаёт это значение во все вызовы API.

### Шаг 4.1 — `api.ts`: session_id во всех вызовах

- [ ] Заменить содержимое `frontend/src/api.ts` на:

```typescript
import type { Listing, SearchConfig, Status } from "./types";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error ?? `Ошибка запроса: ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

export function getListings(sessionId: number, includeRejected: boolean): Promise<Listing[]> {
  const params = new URLSearchParams({ session_id: String(sessionId) });
  if (includeRejected) params.set("include_rejected", "1");
  return requestJson<Listing[]>(`/api/listings?${params.toString()}`);
}

export function getStatus(sessionId: number): Promise<Status> {
  return requestJson<Status>(`/api/status?session_id=${sessionId}`);
}

export function getConfig(sessionId: number): Promise<SearchConfig> {
  return requestJson<SearchConfig>(`/api/config?session_id=${sessionId}`);
}

export function saveConfig(sessionId: number, cfg: Partial<SearchConfig>): Promise<SearchConfig> {
  return requestJson<SearchConfig>("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...cfg, session_id: sessionId }),
  });
}

export function rescore(sessionId: number): Promise<{ rescored: number }> {
  return requestJson<{ rescored: number }>("/api/rescore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export function pauseWorker(sessionId: number): Promise<Status> {
  return requestJson<Status>("/api/worker/pause", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export function resumeWorker(sessionId: number): Promise<Status> {
  return requestJson<Status>("/api/worker/resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}
```

### Шаг 4.2 — `App.tsx`: читать `?session=` из URL

- [ ] В `frontend/src/App.tsx` добавить рядом с остальными константами
      (после `POLL_INTERVAL_MS`) функцию чтения сессии из URL:

```typescript
function getSessionIdFromUrl(): 1 | 2 {
  const raw = new URLSearchParams(window.location.search).get("session");
  return raw === "2" ? 2 : 1; // всё, что не "2" (включая отсутствие параметра) — сессия 1
}
```

- [ ] В начале компонента `App` добавить:

```typescript
const sessionId = useRef(getSessionIdFromUrl()).current;
```

- [ ] Передать `sessionId` первым аргументом во все вызовы из `api.ts`
      внутри `App.tsx`: `getConfig(sessionId)`, `getListings(sessionId, showRejected)`,
      `getStatus(sessionId)`, `saveConfig(sessionId, cfg)`, `rescore(sessionId)`,
      `pauseWorker(sessionId)`, `resumeWorker(sessionId)` — в каждом месте,
      где эти функции сейчас вызываются без аргумента сессии.

- [ ] В `refresh` заменить:

  ```typescript
  const [freshListings, freshStatus] = await Promise.all([getListings(showRejected), getStatus()]);
  ```

  на:

  ```typescript
  const [freshListings, freshStatus] = await Promise.all([
    getListings(sessionId, showRejected),
    getStatus(sessionId),
  ]);
  ```

- [ ] Заменить заголовок страницы, чтобы было видно, какая сессия
      открыта, и дать ссылку на вторую (без какой-либо доп. стилизации —
      это чисто функциональная метка, редизайн будет отдельным шагом):

  ```tsx
  <h1 className="text-2xl font-semibold">
    Поиск машин на Avito — сессия {sessionId}
    {" · "}
    <a className="text-sm underline" href={sessionId === 1 ? "?session=2" : "?session=1"}>
      открыть сессию {sessionId === 1 ? 2 : 1}
    </a>
  </h1>
  ```

- [ ] Собрать фронтенд и убедиться, что сборка проходит без ошибок типов:

```bash
cd frontend && npm run build && cd ..
```

  Ожидаемо: `tsc`/`vite build` завершаются без ошибок (это статическая
  проверка типов TypeScript — юнит-тестов на фронтенде в проекте нет).

- [ ] Закоммитить:

```bash
git add frontend/src/api.ts frontend/src/App.tsx
git commit -m "feat(frontend): переключение поисковой сессии через ?session= в URL"
```

---

## Task 5: Ручная сквозная проверка

**Files:** нет изменений кода — только проверка запущенного приложения.

**Interfaces:** нет (использует всё, что собрано в Task 1–4).

- [ ] Прогнать весь набор тестов ещё раз с самого начала после всех
      правок: `python3 -m pytest -v`. Ожидаемо: все тесты проходят,
      вывод показать целиком.

- [ ] Проверить миграцию на реальном файле БД, если у пользователя уже
      есть `avito_monitor_web.db` от прошлых запусков: сделать копию
      файла (`cp avito_monitor_web.db /tmp/migration-check.db`), открыть
      её через `python3 -c "import db; c = db.get_conn('/tmp/migration-check.db'); print(db.get_all_listings(c, 1))"`
      и убедиться, что список не пуст и совпадает по количеству со старым
      состоянием, а `db.get_all_listings(c, 2)` возвращает `[]`. Если
      файла `avito_monitor_web.db` ещё не существует — этот пункт
      пропускается, что и зафиксировать в отчёте.

- [ ] Поднять сервер: `python app.py`. В логе консоли должно быть видно,
      как воркер поочерёдно обрабатывает `[Сессия 1]` и `[Сессия 2]`
      (если обе настроены) — приложить фрагмент лога.

- [ ] Открыть `http://localhost:5050/?session=1` и настроить фильтр
      (например, определённая марка/город). Открыть отдельно
      `http://localhost:5050/?session=2` и настроить другой фильтр.
      Показать скриншот обоих окон с разными результатами (или с пустым
      списком, если реальных объявлений под критерии не нашлось за время
      проверки — тогда явно это указать, а не выдавать пустой список за
      успех).

- [ ] Проверить нештатный сценарий: нажать «Остановить парсинг» в одном
      окне и показать, что `worker_status.paused` для этой сессии стал
      `True`, а лог продолжает обрабатывать вторую сессию (не встаёт
      целиком). Можно проверить через `GET /api/status?session_id=1` и
      `?session_id=2` напрямую (`curl`), не дожидаясь реального цикла
      скрапинга.

- [ ] `git status` и `git diff --stat` относительно состояния до Task 1 —
      убедиться, что изменены только: `db.py`, `worker.py`, `app.py`,
      `frontend/src/api.ts`, `frontend/src/App.tsx`, тестовые файлы,
      `docs/superpowers/plans/2026-08-28-two-search-sessions.md`,
      `TestTask/SPEC.md`, `TestTask/AGENTS.md`,
      `TestTask/sessions/session-1.md` — `main.py`, `scraper.py`,
      `config.py` не тронуты.
