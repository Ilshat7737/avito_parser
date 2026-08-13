# Веб-приложение поиска машин на Avito — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Превратить CLI-монитор объявлений Avito в локальное веб-приложение
для поиска машин: ввод критериев через браузер, LLM-агент фильтрует и
ранжирует по релевантности, автообновление раз в минуту, БД ограничена
100 строками без потери dedup-памяти.

**Architecture:** Flask-сервер (`app.py`) отдаёт React-фронтенд как статику и
JSON `/api/*`; фоновый `threading.Thread` (`worker.py`) раз в ~60 сек
скрапит Avito (`scraper.py`), различает новые/известные/вытесненные
объявления через `db.py` (`listings` + `seen_ids`), для новых тянет описание
и батчем зовёт `agent.py` (Claude Haiku 4.5, tool use) для оценки
релевантности. `url_builder.py` один раз при сохранении настроек заполняет
реальную форму фильтров Avito через Playwright и возвращает готовый URL —
вместо угадывания недокументированного формата `f=`.

**Tech Stack:** Python 3.11+, Flask, Playwright (sync API), SQLite (`sqlite3`
из стандартной библиотеки), Anthropic SDK (Claude Haiku 4.5), pytest;
Vite + React + TypeScript + Tailwind + shadcn/21st.dev + Framer Motion на
фронтенде.

**Spec:** `docs/superpowers/specs/2026-08-13-car-search-web-app-design.md`

## Global Constraints

- Все селекторы Avito — только через `data-marker`, никогда через CSS-классы
  (см. `CLAUDE.md`).
- Не уменьшать и не убирать `MIN_DELAY`, `MAX_DELAY`, `INTERVAL_JITTER_SEC` —
  единственное осознанное отступление от анти-бан политики, явно
  разрешённое пользователем, это интервал цикла `CHECK_INTERVAL_SEC = 60`
  (было 300); джиттер и внутрицикловые задержки остаются как есть.
- `HEADLESS = False` по умолчанию — не менять.
- Все уведомления/тексты UI — на русском языке; консоль/Telegram сохраняют
  стиль эмодзи 🆕/💰 из `notify.py`.
- `ANTHROPIC_API_KEY` — только из переменной окружения/`.env`, никогда не
  хардкодить в `config.py` или коммитить.
- Жёсткий лимит `listings` — 100 строк, FIFO по `first_seen`:
  `DELETE FROM listings WHERE id NOT IN (SELECT id FROM listings ORDER BY first_seen DESC LIMIT 100)`.
- Ошибка вызова агента за цикл не должна приводить к потере карточки —
  она просто не попадает в `listings`/`seen_ids` в этом цикле и
  обрабатывается заново в следующем.
- Никакого нового функционала массового сбора персональных данных/контактов
  третьих лиц.

---

## Файловая структура (после реализации)

```
avito_parser/
├── config.py            # изменён: CHECK_INTERVAL_SEC=60, ANTHROPIC_API_KEY, AGENT_MODEL
├── db.py                 # изменён: новые таблицы/столбцы, новые функции
├── scraper.py             # изменён: парсинг year/mileage
├── url_builder.py         # новый: сборка search_url через реальную форму фильтров
├── agent.py               # новый: батч-оценка релевантности (Claude Haiku 4.5)
├── worker.py               # новый: фоновый цикл опроса для веб-режима
├── app.py                   # новый: Flask-приложение и API
├── main.py                   # без изменений (CLI-режим для отладки скрапинга)
├── notify.py                  # без изменений
├── requirements.txt            # изменён: + flask, anthropic, python-dotenv, pytest
├── .env.example                 # новый
├── .gitignore                    # изменён: .env, avito_monitor.db, frontend/node_modules, frontend/dist
├── tests/
│   ├── conftest.py
│   ├── test_db.py
│   ├── test_scraper.py
│   ├── test_agent.py
│   ├── test_worker.py
│   └── test_app.py
└── frontend/
    ├── index.html
    ├── tailwind.config.ts
    └── src/
        ├── index.css
        ├── types.ts
        ├── api.ts
        ├── App.tsx
        └── components/
            ├── ListingCard.tsx
            ├── SearchForm.tsx
            └── StatusBar.tsx
```

---

### Task 1: Схема БД — новые таблицы и функции в `db.py`

**Files:**
- Modify: `db.py`
- Test: `tests/test_db.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Consumes: ничего (базовый слой)
- Produces:
  - `db.get_conn(db_path: str) -> sqlite3.Connection`
  - `db.get_listing(conn, item_id: str) -> dict | None`
  - `db.upsert_new(conn, item: dict) -> None` — принимает `id, title, price,
    url`, опционально `photo, description, year, mileage, relevant,
    relevance_reason`
  - `db.update_price(conn, item_id: str, new_price: int) -> None`
  - `db.touch(conn, item_id: str) -> None`
  - `db.is_seen(conn, item_id: str) -> bool`
  - `db.mark_seen(conn, item_id: str) -> None`
  - `db.evict_old_listings(conn, keep: int = 100) -> None`
  - `db.get_relevant_listings(conn, include_rejected: bool = False) -> list[dict]`
  - `db.get_unevaluated_listings(conn) -> list[dict]`
  - `db.update_relevance(conn, item_id: str, relevant: int, reason: str) -> None`
  - `db.mark_all_for_rescore(conn) -> None`
  - `db.get_search_config(conn) -> dict | None`
  - `db.save_search_config(conn, cfg: dict) -> None`
  - `db.set_last_check(conn, count: int) -> None`
  - `db.get_status(conn) -> dict` (`{"last_check": str|None, "last_count": int}`)

- [ ] **Step 1: Создать `tests/conftest.py`**

```python
import os

# Гарантирует, что config.py и agent.py импортируются без реального
# ANTHROPIC_API_KEY в тестовом окружении (тесты мокают вызовы API).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")
```

- [ ] **Step 2: Написать падающий тест на новую схему**

```python
# tests/test_db.py
import sqlite3

import pytest

import db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(db.SCHEMA)
    c.execute(db.SEEN_IDS_SCHEMA)
    c.execute(db.SEARCH_CONFIG_SCHEMA)
    c.execute(db.WORKER_STATUS_SCHEMA)
    c.commit()
    return c


def test_upsert_new_stores_year_mileage_and_relevance(conn):
    item = {
        "id": "111", "title": "Kia Rio 1.4 MT, 2017, 77 491 км",
        "price": 700000, "url": "https://www.avito.ru/kazan/1",
        "photo": "https://img/1.jpg", "description": "Один хозяин",
        "year": 2017, "mileage": 77491,
        "relevant": 1, "relevance_reason": "Подходит по цене и году",
    }
    db.upsert_new(conn, item)

    stored = db.get_listing(conn, "111")
    assert stored["year"] == 2017
    assert stored["mileage"] == 77491
    assert stored["relevant"] == 1
    assert stored["relevance_reason"] == "Подходит по цене и году"
    assert stored["agent_evaluated_at"] is not None


def test_upsert_new_without_agent_fields_leaves_them_null(conn):
    item = {
        "id": "222", "title": "iPhone 13", "price": 45000,
        "url": "https://www.avito.ru/moskva/2",
    }
    db.upsert_new(conn, item)

    stored = db.get_listing(conn, "222")
    assert stored["year"] is None
    assert stored["mileage"] is None
    assert stored["relevant"] is None
    assert stored["agent_evaluated_at"] is None
```

- [ ] **Step 3: Запустить и убедиться, что тест падает**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'SEEN_IDS_SCHEMA'`

- [ ] **Step 4: Обновить схему и `upsert_new` в `db.py`**

Заменить содержимое `db.py` целиком:

```python
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
               SELECT id FROM listings ORDER BY first_seen DESC LIMIT ?
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
```

- [ ] **Step 5: Запустить тесты, убедиться, что проходят**

Run: `pytest tests/test_db.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Дописать тесты на eviction, seen_ids, config, status, rescore**

Добавить в `tests/test_db.py`:

```python
def test_evict_old_listings_keeps_only_most_recent(conn):
    for i in range(5):
        db.upsert_new(conn, {
            "id": str(i), "title": f"Item {i}", "price": 1000,
            "url": f"https://avito.ru/{i}",
        })
    db.evict_old_listings(conn, keep=3)

    cur = conn.execute("SELECT id FROM listings ORDER BY first_seen")
    remaining = [row[0] for row in cur.fetchall()]
    assert len(remaining) == 3
    assert remaining == ["2", "3", "4"]


def test_seen_ids_tracks_ids_independently_of_listings(conn):
    assert db.is_seen(conn, "999") is False
    db.mark_seen(conn, "999")
    assert db.is_seen(conn, "999") is True

    db.upsert_new(conn, {"id": "1", "title": "A", "price": 100, "url": "u"})
    db.evict_old_listings(conn, keep=0)
    assert db.get_listing(conn, "1") is None
    assert db.is_seen(conn, "999") is True  # seen_ids не чистится эвикшеном


def test_get_relevant_listings_filters_by_relevant_flag(conn):
    db.upsert_new(conn, {"id": "1", "title": "Relevant", "price": 100, "url": "u1", "relevant": 1})
    db.upsert_new(conn, {"id": "2", "title": "Rejected", "price": 100, "url": "u2", "relevant": 0})
    db.upsert_new(conn, {"id": "3", "title": "Unevaluated", "price": 100, "url": "u3"})

    relevant_only = db.get_relevant_listings(conn)
    assert [r["id"] for r in relevant_only] == ["1"]

    with_rejected = db.get_relevant_listings(conn, include_rejected=True)
    assert {r["id"] for r in with_rejected} == {"1", "2"}


def test_mark_all_for_rescore_clears_evaluation_fields(conn):
    db.upsert_new(conn, {"id": "1", "title": "A", "price": 100, "url": "u", "relevant": 1, "relevance_reason": "ok"})
    db.mark_all_for_rescore(conn)

    stored = db.get_listing(conn, "1")
    assert stored["relevant"] is None
    assert stored["relevance_reason"] is None
    assert stored["agent_evaluated_at"] is None


def test_search_config_roundtrip(conn):
    assert db.get_search_config(conn) is None

    db.save_search_config(conn, {
        "city": "kazan", "brand": "Kia", "price_min": 500000, "price_max": 900000,
        "mileage_min": None, "mileage_max": 100000, "year_min": 2015, "year_max": None,
        "query_text": "надёжная семейная машина",
        "search_url": "https://www.avito.ru/kazan/avtomobili?f=abc",
    })
    cfg = db.get_search_config(conn)
    assert cfg["city"] == "kazan"
    assert cfg["price_max"] == 900000
    assert cfg["query_text"] == "надёжная семейная машина"


def test_status_defaults_then_updates(conn):
    assert db.get_status(conn) == {"last_check": None, "last_count": 0}

    db.set_last_check(conn, count=12)
    status = db.get_status(conn)
    assert status["last_count"] == 12
    assert status["last_check"] is not None


def test_get_unevaluated_listings_excludes_already_scored_items(conn):
    db.upsert_new(conn, {"id": "1", "title": "Оценено", "price": 100, "url": "u1", "relevant": 1})
    db.upsert_new(conn, {"id": "2", "title": "Не оценено", "price": 100, "url": "u2"})

    unevaluated = db.get_unevaluated_listings(conn)
    assert [item["id"] for item in unevaluated] == ["2"]
```

- [ ] **Step 7: Запустить полный набор тестов `db.py`**

Run: `pytest tests/test_db.py -v`
Expected: PASS (9 passed)

- [ ] **Step 8: Commit**

```bash
git add db.py tests/test_db.py tests/conftest.py
git commit -m "feat(db): добавить seen_ids, search_config, worker_status и оценку агента"
```

---

### Task 2: Парсинг года и пробега в `scraper.py`

**Files:**
- Modify: `scraper.py`
- Test: `tests/test_scraper.py`

**Interfaces:**
- Consumes: ничего нового
- Produces:
  - `scraper._extract_mileage(params_text: str) -> int | None`
  - `scraper._extract_year(title: str) -> int | None`
  - `scraper._parse_card(card) -> dict | None` — теперь возвращает также
    `year` и `mileage`

Найдено по реальной разметке карточки Avito (`data-marker="item"`):
пробег — первый сегмент строки `data-marker="item-specific-params"`
(например `"42 313 км, 1.6 MT (106 л.с.), универсал, передний, бензин"`);
отдельного маркера года на карточке нет — год встречается только в
заголовке (`data-marker="item-title"`), например
`"ВАЗ (LADA) Largus Cross 1.6 MT, 2019, 42 313 км"`.

- [ ] **Step 1: Написать падающие тесты на извлечение пробега и года**

```python
# tests/test_scraper.py
from scraper import _extract_mileage, _extract_year


def test_extract_mileage_from_specific_params():
    text = "42 313 км, 1.6 MT (106 л.с.), универсал, передний, бензин"
    assert _extract_mileage(text) == 42313


def test_extract_mileage_returns_none_when_no_digits():
    assert _extract_mileage("новый автомобиль, 1.6 MT") is None


def test_extract_year_from_title():
    title = "ВАЗ (LADA) Largus Cross 1.6 MT, 2019, 42 313 км"
    assert _extract_year(title) == 2019


def test_extract_year_returns_none_when_absent():
    assert _extract_year("Kia Rio без пробега в описании") is None
```

- [ ] **Step 2: Запустить и убедиться, что тесты падают**

Run: `pytest tests/test_scraper.py -v`
Expected: FAIL — `ImportError: cannot import name '_extract_mileage'`

- [ ] **Step 3: Добавить функции и обновить `_parse_card` в `scraper.py`**

Добавить после `_extract_id`:

```python
def _extract_mileage(params_text: str) -> int | None:
    """Пробег — первый сегмент data-marker="item-specific-params",
    например "42 313 км, 1.6 MT (106 л.с.), универсал, передний, бензин"."""
    first_segment = params_text.split(",")[0]
    digits = re.sub(r"\D", "", first_segment)
    return int(digits) if digits else None


def _extract_year(title: str) -> int | None:
    """Год выпуска на карточке отдельным маркером не отдаётся — он есть
    только в заголовке, например "..., 2019, 42 313 км"."""
    match = re.search(r"\b(19|20)\d{2}\b", title)
    return int(match.group(0)) if match else None
```

Заменить `_parse_card`:

```python
def _parse_card(card) -> dict | None:
    title_el = card.query_selector('[data-marker="item-title"]')
    if not title_el:
        return None

    title = title_el.inner_text().strip()
    href = title_el.get_attribute("href")
    if not href:
        return None
    url = href if href.startswith("http") else f"https://www.avito.ru{href}"

    price_el = card.query_selector('[data-marker="item-price"]')
    price_text = price_el.inner_text() if price_el else ""
    price_digits = re.sub(r"\D", "", price_text)
    price = int(price_digits) if price_digits else None

    img_el = card.query_selector("img")
    photo = img_el.get_attribute("src") if img_el else None

    params_el = card.query_selector('[data-marker="item-specific-params"]')
    mileage = _extract_mileage(params_el.inner_text()) if params_el else None
    year = _extract_year(title)

    return {
        "id": _extract_id(url),
        "title": title,
        "price": price,
        "url": url,
        "photo": photo,
        "mileage": mileage,
        "year": year,
    }
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `pytest tests/test_scraper.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add scraper.py tests/test_scraper.py
git commit -m "feat(scraper): парсить год и пробег из карточки объявления"
```

---

### Task 3: `url_builder.py` — сборка `search_url` через реальную форму фильтров

**Files:**
- Create: `url_builder.py`

**Interfaces:**
- Consumes: `config.HEADLESS`
- Produces: `url_builder.build_search_url(city: str, brand: str | None = None,
  price_min: int | None = None, price_max: int | None = None,
  mileage_min: int | None = None, mileage_max: int | None = None,
  year_min: int | None = None, year_max: int | None = None) -> str`

Подтверждённые `data-marker`-селекторы формы фильтров Avito (сняты с
реальной разметки страницы `/kazan/avtomobili`):
- Цена: `price-from/input`, `price-to/input`
- Год выпуска: `params[164669]-from/input`, `params[164669]-to/input`
- Пробег: `params[2687]-from/input`, `params[2687]-to/input`
- Марка (мультиселект с поиском по вводу): поле поиска
  `params[110000]/multiselect-outline-input/input`, чекбоксы с префиксом
  `params[110000]/multiselect-button-checkbox`
- Город — не поле формы, а сегмент URL: `https://www.avito.ru/{city}/avtomobili`
  (пользователь вводит `city` как латинский слаг, например `kazan`, а не
  «Казань» — конвертация кириллицы в слаг через geo-suggest API Avito не
  реализуется, YAGNI).
- Явной кнопки «Применить» в снятой разметке формы нет — фильтры на Avito
  реактивны (SPA обновляет `location.href` при потере фокуса полем). Код
  снимает фокус (`Tab`) и ждёт смены `page.url`; если URL не поменялся за
  8 секунд (например, ни один фильтр не задан), используется URL как есть.

- [ ] **Step 1: Написать `url_builder.py`**

```python
"""
Сборка ссылки поиска Avito по критериям пользователя через реальную форму
фильтров (Playwright), а не через недокументированный формат параметра f=.
См. docs/superpowers/specs/2026-08-13-car-search-web-app-design.md,
раздел "Сборка ссылки поиска Avito".
"""

from playwright.sync_api import sync_playwright

import config

BASE_URL = "https://www.avito.ru/{city}/avtomobili"


def _fill_range(page, from_marker: str, to_marker: str, min_value, max_value):
    if min_value is not None:
        page.fill(f'[data-marker="{from_marker}/input"]', str(min_value))
    if max_value is not None:
        page.fill(f'[data-marker="{to_marker}/input"]', str(max_value))


def _select_brand(page, brand: str):
    search_input = '[data-marker="params[110000]/multiselect-outline-input/input"]'
    page.fill(search_input, brand)
    checkbox = page.locator(
        '[data-marker^="params[110000]/multiselect-button-checkbox"]',
        has_text=brand,
    ).first
    checkbox.wait_for(state="visible", timeout=5000)
    checkbox.click()


def build_search_url(
    city: str,
    brand: str | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    mileage_min: int | None = None,
    mileage_max: int | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
) -> str:
    """Открывает страницу поиска Avito, заполняет фильтры через реальную
    форму и возвращает получившийся URL."""
    start_url = BASE_URL.format(city=city)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        context = browser.new_context(locale="ru-RU")
        page = context.new_page()
        page.goto(start_url, wait_until="domcontentloaded")

        if brand:
            _select_brand(page, brand)

        _fill_range(page, "price-from", "price-to", price_min, price_max)
        _fill_range(page, "params[164669]-from", "params[164669]-to", year_min, year_max)
        _fill_range(page, "params[2687]-from", "params[2687]-to", mileage_min, mileage_max)

        page.keyboard.press("Tab")
        before_url = page.url
        try:
            page.wait_for_function(
                "url => location.href !== url", arg=before_url, timeout=8000
            )
        except Exception:
            pass  # фильтры могли не менять URL (например, ничего не задано)

        result_url = page.url
        browser.close()
        return result_url
```

- [ ] **Step 2: Ручная проверка на машине пользователя (нет сети/браузера в песочнице)**

Запустить и прислать результат:

```bash
python -c "from url_builder import build_search_url; print(build_search_url('kazan', brand='Kia', price_min=500000, price_max=900000, year_min=2015))"
```

Ожидается: печатается реальный URL с примененными фильтрами Avito (можно
свериться, открыв тот же URL в обычном браузере — марка/цена/год должны
совпадать с введёнными). Если фильтр не применился (URL совпадает со
стартовым) или упал таймаут — прислать вывод/скриншот, чтобы уточнить
селекторы формы (возможно, разметка отличается для другого города/категории).

- [ ] **Step 3: Commit**

```bash
git add url_builder.py
git commit -m "feat: собирать search_url через реальную форму фильтров Avito"
```

---

### Task 4: `agent.py` — батч-оценка релевантности через Claude Haiku 4.5

**Files:**
- Create: `agent.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: `config.ANTHROPIC_API_KEY`, `config.AGENT_MODEL` (из Task 5)
- Produces: `agent.evaluate_listings(items: list[dict], query_text: str) ->
  dict[str, dict]` — возвращает `{id: {"relevant": bool, "reason": str}}`;
  при ошибке API или пустом `items` возвращает `{}`

- [ ] **Step 1: Написать падающие тесты**

```python
# tests/test_agent.py
from unittest.mock import MagicMock

import agent


def test_evaluate_listings_returns_empty_dict_for_empty_items():
    assert agent.evaluate_listings([], "любой текст") == {}


def test_evaluate_listings_parses_tool_use_response(monkeypatch):
    fake_block = MagicMock()
    fake_block.type = "tool_use"
    fake_block.name = "submit_relevance"
    fake_block.input = {
        "results": [
            {"id": "111", "relevant": True, "reason": "Подходит по цене и марке"},
        ]
    }
    fake_response = MagicMock()
    fake_response.content = [fake_block]
    monkeypatch.setattr(agent.client.messages, "create", lambda **kwargs: fake_response)

    items = [{
        "id": "111", "title": "Kia Rio", "price": 700000,
        "year": 2019, "mileage": 42313, "description": "Один хозяин",
    }]
    result = agent.evaluate_listings(items, "недорогая надёжная машина")

    assert result == {"111": {"relevant": True, "reason": "Подходит по цене и марке"}}


def test_evaluate_listings_returns_empty_dict_on_api_error(monkeypatch):
    def raise_error(**kwargs):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(agent.client.messages, "create", raise_error)

    items = [{
        "id": "111", "title": "Kia Rio", "price": 700000,
        "year": 2019, "mileage": 42313, "description": "",
    }]
    assert agent.evaluate_listings(items, "текст") == {}
```

- [ ] **Step 2: Запустить и убедиться, что тесты падают**

Run: `pytest tests/test_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent'`

- [ ] **Step 3: Написать `agent.py`**

```python
"""
Батч-оценка релевантности новых объявлений текстовому уточнению пользователя
через Claude Haiku 4.5 (tool use — структурированный ответ, не свободный текст).
"""

import anthropic

import config

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

EVALUATE_TOOL = {
    "name": "submit_relevance",
    "description": "Вернуть релевантность и краткое обоснование для каждого объявления из списка.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "relevant": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "relevant", "reason"],
                },
            }
        },
        "required": ["results"],
    },
}


def evaluate_listings(items: list[dict], query_text: str) -> dict[str, dict]:
    """Возвращает {id: {"relevant": bool, "reason": str}} для каждого id из
    items. При ошибке API возвращает {} — карточки останутся неоценёнными
    и будут обработаны в следующем цикле."""
    if not items:
        return {}

    listing_blocks = []
    for item in items:
        listing_blocks.append(
            f"id={item['id']}\n"
            f"Заголовок: {item['title']}\n"
            f"Цена: {item.get('price')} ₽\n"
            f"Год: {item.get('year')}\n"
            f"Пробег: {item.get('mileage')} км\n"
            f"Описание: {item.get('description', '')}"
        )

    prompt = (
        f"Критерий пользователя: {query_text}\n\n"
        "Ниже список объявлений о продаже машин. Для КАЖДОГО id из списка "
        "определи, соответствует ли оно критерию пользователя, и коротко "
        "(1 предложение, на русском) объясни решение. Верни результат "
        "вызовом инструмента submit_relevance для всех id без исключения.\n\n"
        + "\n---\n".join(listing_blocks)
    )

    try:
        response = client.messages.create(
            model=config.AGENT_MODEL,
            max_tokens=2048,
            tools=[EVALUATE_TOOL],
            tool_choice={"type": "tool", "name": "submit_relevance"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return {}

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_relevance":
            results = block.input.get("results", [])
            return {r["id"]: {"relevant": r["relevant"], "reason": r["reason"]} for r in results}
    return {}
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `pytest tests/test_agent.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_agent.py
git commit -m "feat: батч-оценка релевантности объявлений через Claude Haiku 4.5"
```

---

### Task 5: Секреты и конфигурация — `.env`, `config.py`, `requirements.txt`

**Files:**
- Modify: `config.py`
- Modify: `requirements.txt`
- Create: `.env.example`
- Modify (or create): `.gitignore`

**Interfaces:**
- Produces: `config.ANTHROPIC_API_KEY: str`, `config.AGENT_MODEL: str`,
  `config.CHECK_INTERVAL_SEC = 60`

- [ ] **Step 1: Обновить `requirements.txt`**

```
playwright>=1.44
requests>=2.31
flask>=3.0
anthropic>=0.40
python-dotenv>=1.0
pytest>=8.0
```

- [ ] **Step 2: Создать `.env.example`**

```
ANTHROPIC_API_KEY=sk-ant-...
```

- [ ] **Step 3: Создать/дополнить `.gitignore`**

```
__pycache__/
*.pyc
avito_monitor.db
.env
frontend/node_modules/
frontend/dist/
```

- [ ] **Step 4: Обновить `config.py`**

Добавить в начало файла (после docstring) и заменить строку с интервалом:

```python
import os

from dotenv import load_dotenv

load_dotenv()
```

Заменить:
```python
CHECK_INTERVAL_SEC = 300  # 5 минут
```
на:
```python
CHECK_INTERVAL_SEC = 60  # 1 минута — осознанный риск, см. спеку раздел
                          # "Осознанно принятый риск: интервал в 1 минуту"
```

Добавить в конец файла:

```python
# --- LLM-агент релевантности (веб-режим поиска машин) ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
```

- [ ] **Step 5: Проверить, что импорт не падает**

Run: `python -c "import config; print(config.CHECK_INTERVAL_SEC, config.AGENT_MODEL)"`
Expected: `60 claude-haiku-4-5-20251001`

- [ ] **Step 6: Commit**

```bash
git add config.py requirements.txt .env.example .gitignore
git commit -m "feat: интервал 60 сек, ANTHROPIC_API_KEY через .env"
```

---

### Task 6: `worker.py` — фоновый цикл опроса для веб-режима

**Files:**
- Create: `worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `db.get_listing`, `db.is_seen`, `db.mark_seen`, `db.upsert_new`,
  `db.evict_old_listings`, `db.get_search_config`, `db.set_last_check`
  (Task 1); `agent.evaluate_listings` (Task 4); `scraper.fetch_search_results`,
  `scraper.fetch_description`, `scraper.USER_AGENT` (existing); `notify.notify_new_item`,
  `notify.notify_price_change`, `notify.console_log` (existing); `config.HEADLESS`,
  `config.CHECK_INTERVAL_SEC`, `config.INTERVAL_JITTER_SEC` (existing/Task 5)
- Produces:
  - `worker.process_cycle(conn, items: list[dict], query_text: str,
    fetch_description_fn: Callable[[str], str]) -> None`
  - `worker.background_loop(conn, stop_event: threading.Event) -> None`

- [ ] **Step 1: Написать падающие тесты для `process_cycle`**

```python
# tests/test_worker.py
import sqlite3

import pytest

import db
import worker


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(db.SCHEMA)
    c.execute(db.SEEN_IDS_SCHEMA)
    c.execute(db.SEARCH_CONFIG_SCHEMA)
    c.execute(db.WORKER_STATUS_SCHEMA)
    c.commit()
    return c


def fake_fetch_description(url):
    return "Тестовое описание"


def test_process_cycle_evaluates_and_stores_new_relevant_item(conn, monkeypatch):
    monkeypatch.setattr(
        worker.agent, "evaluate_listings",
        lambda items, query_text: {"1": {"relevant": True, "reason": "Подходит"}},
    )
    items = [{"id": "1", "title": "Kia Rio", "price": 700000, "url": "u1", "year": 2019, "mileage": 40000}]

    worker.process_cycle(conn, items, "надёжная машина", fake_fetch_description)

    stored = db.get_listing(conn, "1")
    assert stored is not None
    assert stored["relevant"] == 1
    assert stored["description"] == "Тестовое описание"
    assert db.is_seen(conn, "1") is True


def test_process_cycle_skips_items_evicted_but_still_seen(conn, monkeypatch):
    db.mark_seen(conn, "2")  # вытеснено ранее, но встречено снова в выдаче
    called = {"agent": False}

    def spy_evaluate(items, query_text):
        called["agent"] = True
        return {}

    monkeypatch.setattr(worker.agent, "evaluate_listings", spy_evaluate)
    items = [{"id": "2", "title": "Старое объявление", "price": 100000, "url": "u2"}]

    worker.process_cycle(conn, items, "текст", fake_fetch_description)

    assert db.get_listing(conn, "2") is None  # не появилось заново в listings
    assert called["agent"] is False  # агент не звался для уже виденных


def test_process_cycle_updates_price_for_known_listing(conn, monkeypatch):
    db.upsert_new(conn, {"id": "3", "title": "Известное", "price": 500000, "url": "u3"})

    def spy_evaluate(items, query_text):
        raise AssertionError("агент не должен звать для уже известных карточек")

    monkeypatch.setattr(worker.agent, "evaluate_listings", spy_evaluate)
    items = [{"id": "3", "title": "Известное", "price": 480000, "url": "u3"}]

    worker.process_cycle(conn, items, "текст", fake_fetch_description)

    stored = db.get_listing(conn, "3")
    assert stored["price"] == 480000


def test_process_cycle_leaves_item_unstored_when_agent_call_fails(conn, monkeypatch):
    monkeypatch.setattr(worker.agent, "evaluate_listings", lambda items, query_text: {})
    items = [{"id": "4", "title": "Новое", "price": 300000, "url": "u4"}]

    worker.process_cycle(conn, items, "текст", fake_fetch_description)

    assert db.get_listing(conn, "4") is None
    assert db.is_seen(conn, "4") is False  # попробуем снова в следующем цикле


def test_process_cycle_evicts_after_inserting_new_items(conn, monkeypatch):
    for i in range(3):
        db.upsert_new(conn, {"id": str(i), "title": f"Item {i}", "price": 1000, "url": f"u{i}"})
        db.mark_seen(conn, str(i))

    monkeypatch.setattr(
        worker.agent, "evaluate_listings",
        lambda items, query_text: {"new": {"relevant": True, "reason": "ok"}},
    )
    items = [{"id": "new", "title": "Новое", "price": 999999, "url": "u_new"}]

    worker.process_cycle(conn, items, "текст", fake_fetch_description, keep=3)

    cur = conn.execute("SELECT id FROM listings")
    remaining_ids = {row[0] for row in cur.fetchall()}
    assert len(remaining_ids) == 3
    assert "new" in remaining_ids
    assert "0" not in remaining_ids  # самая старая запись вытеснена
```

- [ ] **Step 2: Запустить и убедиться, что тесты падают**

Run: `pytest tests/test_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'worker'`

- [ ] **Step 3: Написать `worker.py`**

```python
"""
Фоновый цикл опроса Avito для веб-режима: скрапинг -> dedup ->
описание для новых -> батч-оценка агентом -> запись в БД -> уведомления ->
обрезка listings до лимита. Живёт в daemon-потоке, поднятом из app.py.
"""

import random
import threading

import agent
import config
import db
from notify import console_log, notify_new_item, notify_price_change
from scraper import USER_AGENT, fetch_description, fetch_search_results


def process_cycle(conn, items: list[dict], query_text: str, fetch_description_fn, keep: int = 100):
    new_items = []
    for item in items:
        existing = db.get_listing(conn, item["id"])
        if existing is not None:
            if item["price"] is not None and existing["price"] != item["price"]:
                notify_price_change(existing, item)
                db.update_price(conn, item["id"], item["price"])
            else:
                db.touch(conn, item["id"])
            continue

        if db.is_seen(conn, item["id"]):
            continue

        try:
            item["description"] = fetch_description_fn(item["url"])
        except Exception:
            item["description"] = ""
        new_items.append(item)

    if not new_items:
        return

    verdicts = agent.evaluate_listings(new_items, query_text)

    inserted_any = False
    for item in new_items:
        verdict = verdicts.get(item["id"])
        if verdict is None:
            continue  # ошибка агента — попробуем снова в следующем цикле

        item["relevant"] = 1 if verdict["relevant"] else 0
        item["relevance_reason"] = verdict["reason"]
        db.upsert_new(conn, item)
        db.mark_seen(conn, item["id"])
        inserted_any = True
        if verdict["relevant"]:
            notify_new_item(item)

    if inserted_any:
        db.evict_old_listings(conn, keep=keep)


def background_loop(conn, stop_event: threading.Event):
    console_log("Фоновый поток веб-приложения запущен")
    from playwright.sync_api import sync_playwright

    while not stop_event.is_set():
        cfg = db.get_search_config(conn)
        if cfg and cfg.get("search_url"):
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=config.HEADLESS)
                    context = browser.new_context(user_agent=USER_AGENT, locale="ru-RU")
                    page = context.new_page()
                    items = fetch_search_results(page, cfg["search_url"])
                    process_cycle(
                        conn, items, cfg.get("query_text") or "",
                        lambda url: fetch_description(page, url),
                    )
                    browser.close()
                db.set_last_check(conn, count=len(items))
                console_log(f"Проверено объявлений: {len(items)}")
            except Exception as e:
                console_log(f"Ошибка фонового цикла: {e}")
        else:
            console_log("Поиск ещё не настроен — жду настроек через веб-интерфейс")

        delay = config.CHECK_INTERVAL_SEC + random.uniform(0, config.INTERVAL_JITTER_SEC)
        stop_event.wait(delay)
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `pytest tests/test_worker.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add worker.py tests/test_worker.py
git commit -m "feat: фоновый цикл опроса для веб-режима с dedup и агентом"
```

---

### Task 7: `app.py` — Flask-приложение и API

**Files:**
- Create: `app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `db.*` (Task 1), `agent.evaluate_listings` (Task 4),
  `url_builder.build_search_url` (Task 3), `worker.background_loop` (Task 6)
- Produces:
  - `app.create_app(conn) -> flask.Flask`
  - `app.main() -> None` (запускает фоновый поток + `app.run`)
  - Роуты: `GET /api/listings`, `GET /api/status`, `GET /api/config`,
    `POST /api/config`, `POST /api/rescore`, `GET /` и `GET /<path:path>`
    (отдача `frontend/dist`)

- [ ] **Step 1: Написать падающие тесты**

```python
# tests/test_app.py
import sqlite3

import pytest

import app as app_module
import db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(db.SCHEMA)
    c.execute(db.SEEN_IDS_SCHEMA)
    c.execute(db.SEARCH_CONFIG_SCHEMA)
    c.execute(db.WORKER_STATUS_SCHEMA)
    c.commit()
    return c


@pytest.fixture
def client(conn):
    flask_app = app_module.create_app(conn)
    flask_app.testing = True
    return flask_app.test_client()


def test_api_status_returns_defaults_when_no_checks_yet(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    assert resp.get_json() == {"last_check": None, "last_count": 0}


def test_api_listings_returns_only_relevant_by_default(client, conn):
    db.upsert_new(conn, {"id": "1", "title": "Relevant", "price": 100, "url": "u1", "relevant": 1})
    db.upsert_new(conn, {"id": "2", "title": "Rejected", "price": 100, "url": "u2", "relevant": 0})

    resp = client.get("/api/listings")
    ids = [item["id"] for item in resp.get_json()]
    assert ids == ["1"]

    resp_all = client.get("/api/listings?include_rejected=1")
    ids_all = {item["id"] for item in resp_all.get_json()}
    assert ids_all == {"1", "2"}


def test_api_config_post_saves_config_and_calls_url_builder(client, conn, monkeypatch):
    monkeypatch.setattr(
        app_module, "build_search_url",
        lambda **kwargs: "https://www.avito.ru/kazan/avtomobili?f=abc",
    )
    resp = client.post("/api/config", json={"city": "kazan", "brand": "Kia", "price_min": 500000})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["search_url"] == "https://www.avito.ru/kazan/avtomobili?f=abc"
    assert db.get_search_config(conn)["city"] == "kazan"


def test_api_config_post_returns_400_and_keeps_old_url_on_builder_failure(client, conn, monkeypatch):
    db.save_search_config(conn, {"city": "kazan", "search_url": "https://old-url"})

    def fail(**kwargs):
        raise RuntimeError("Playwright timeout")
    monkeypatch.setattr(app_module, "build_search_url", fail)

    resp = client.post("/api/config", json={"city": "kazan"})

    assert resp.status_code == 400
    assert db.get_search_config(conn)["search_url"] == "https://old-url"


def test_api_rescore_updates_relevance(client, conn, monkeypatch):
    db.upsert_new(conn, {"id": "1", "title": "Kia Rio", "price": 700000, "url": "u1"})
    monkeypatch.setattr(
        app_module.agent, "evaluate_listings",
        lambda items, query_text: {"1": {"relevant": True, "reason": "Подходит"}},
    )

    resp = client.post("/api/rescore")

    assert resp.status_code == 200
    assert resp.get_json() == {"rescored": 1}
    assert db.get_listing(conn, "1")["relevant"] == 1
```

- [ ] **Step 2: Запустить и убедиться, что тесты падают**

Run: `pytest tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Написать `app.py`**

```python
"""
Flask-приложение: отдаёт фронтенд (frontend/dist) и JSON API, поднимает
фоновый поток опроса Avito при старте.
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


def create_app(conn) -> Flask:
    flask_app = Flask(__name__, static_folder=FRONTEND_DIST, static_url_path="")

    @flask_app.route("/api/listings")
    def api_listings():
        include_rejected = request.args.get("include_rejected") == "1"
        return jsonify(db.get_relevant_listings(conn, include_rejected))

    @flask_app.route("/api/status")
    def api_status():
        return jsonify(db.get_status(conn))

    @flask_app.route("/api/config", methods=["GET"])
    def api_config_get():
        return jsonify(db.get_search_config(conn) or {})

    @flask_app.route("/api/config", methods=["POST"])
    def api_config_post():
        payload = request.get_json(force=True)
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
        db.save_search_config(conn, payload)
        return jsonify(db.get_search_config(conn))

    @flask_app.route("/api/rescore", methods=["POST"])
    def api_rescore():
        cfg = db.get_search_config(conn)
        query_text = (cfg or {}).get("query_text") or ""
        db.mark_all_for_rescore(conn)
        items = db.get_unevaluated_listings(conn)
        verdicts = agent.evaluate_listings(items, query_text)
        for item in items:
            verdict = verdicts.get(item["id"])
            if verdict is None:
                continue
            db.update_relevance(conn, item["id"], 1 if verdict["relevant"] else 0, verdict["reason"])
        return jsonify({"rescored": len(verdicts)})

    @flask_app.route("/", defaults={"path": ""})
    @flask_app.route("/<path:path>")
    def serve_frontend(path):
        full_path = os.path.join(FRONTEND_DIST, path) if path else None
        if path and full_path and os.path.exists(full_path):
            return send_from_directory(FRONTEND_DIST, path)
        return send_from_directory(FRONTEND_DIST, "index.html")

    return flask_app


def main():
    conn = db.get_conn(config.DB_PATH)
    stop_event = threading.Event()
    thread = threading.Thread(target=background_loop, args=(conn, stop_event), daemon=True)
    thread.start()

    flask_app = create_app(conn)
    flask_app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Запустить тесты и убедиться, что проходят**

Run: `pytest tests/test_app.py -v`
Expected: PASS (5 passed) — тесты `serve_frontend` не запускаются здесь,
т.к. `frontend/dist` ещё не собран (Task 8/9); маршрут вернёт 404 на
`send_from_directory`, что нормально до сборки фронтенда.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: Flask-приложение с API и фоновым потоком"
```

---

### Task 8: Frontend — каркас, дизайн-токены

**Files:**
- Create: `frontend/` (через Vite CLI)
- Create: `frontend/tailwind.config.ts`
- Create: `frontend/src/index.css`
- Modify: `frontend/index.html`

**Interfaces:**
- Consumes: ничего (UI-каркас)
- Produces: CSS-токены (`--color-*`), шрифты Fira Sans/Fira Code, доступные
  всем компонентам Task 9

- [ ] **Step 1: Создать проект и поставить зависимости**

```bash
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p
npm install framer-motion lucide-react sonner clsx
npx shadcn@latest init -d
npx shadcn@latest add card badge skeleton sonner input slider textarea button switch
cd ..
```

- [ ] **Step 2: Написать `frontend/tailwind.config.ts`**

```typescript
import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: "#0F172A",
        "surface-raised": "#1E293B",
        accent: "#22C55E",
        danger: "#EF4444",
      },
      fontFamily: {
        sans: ["Fira Sans", "system-ui", "sans-serif"],
        mono: ["Fira Code", "monospace"],
      },
      borderRadius: {
        xl: "0.75rem",
      },
      keyframes: {
        "price-flash": {
          "0%": { backgroundColor: "rgba(34, 197, 94, 0.35)" },
          "100%": { backgroundColor: "transparent" },
        },
      },
      animation: {
        "price-flash": "price-flash 1.2s ease-out",
      },
    },
  },
  plugins: [],
} satisfies Config;
```

- [ ] **Step 3: Написать `frontend/src/index.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --background: 210 40% 98%;
  --foreground: 222 47% 11%;
  --card: 0 0% 100%;
  --card-foreground: 222 47% 11%;
  --accent: 142 71% 45%;
  --danger: 0 84% 60%;
}

.dark {
  --background: 222 47% 11%;
  --foreground: 210 40% 98%;
  --card: 217 33% 17%;
  --card-foreground: 210 40% 98%;
  --accent: 142 71% 45%;
  --danger: 0 84% 60%;
}

body {
  background-color: hsl(var(--background));
  color: hsl(var(--foreground));
  font-family: "Fira Sans", system-ui, sans-serif;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 4: Обновить `frontend/index.html`**

```html
<!doctype html>
<html lang="ru" class="dark">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500&family=Fira+Sans:wght@400;500;600&display=swap"
      rel="stylesheet"
    />
    <title>Поиск машин — Avito монитор</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Проверить сборку**

Run: `cd frontend && npm run build`
Expected: сборка завершается без ошибок, появляется `frontend/dist/`

- [ ] **Step 6: Commit**

```bash
git add frontend/tailwind.config.ts frontend/src/index.css frontend/index.html frontend/package.json frontend/package-lock.json
git commit -m "feat(frontend): каркас Vite+React+Tailwind+shadcn, дизайн-токены"
```

---

### Task 9: Frontend — компоненты и опрос API

**Files:**
- Create: `frontend/src/types.ts`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/components/ListingCard.tsx`
- Create: `frontend/src/components/SearchForm.tsx`
- Create: `frontend/src/components/StatusBar.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `GET/POST /api/config`, `GET /api/listings`, `GET /api/status`,
  `POST /api/rescore` (Task 7)
- Produces: React-компоненты `App`, `ListingCard`, `SearchForm`, `StatusBar`

- [ ] **Step 1: Написать `frontend/src/types.ts`**

```typescript
export interface Listing {
  id: string;
  title: string;
  price: number | null;
  url: string;
  photo: string | null;
  description: string | null;
  year: number | null;
  mileage: number | null;
  relevant: number | null;
  relevance_reason: string | null;
  agent_evaluated_at: string | null;
  first_seen: string;
  last_price: number | null;
  last_checked: string;
}

export interface SearchConfig {
  city: string;
  brand: string | null;
  price_min: number | null;
  price_max: number | null;
  mileage_min: number | null;
  mileage_max: number | null;
  year_min: number | null;
  year_max: number | null;
  query_text: string | null;
  search_url: string | null;
  updated_at: string | null;
}

export interface Status {
  last_check: string | null;
  last_count: number;
}
```

- [ ] **Step 2: Написать `frontend/src/api.ts`**

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

export function getListings(includeRejected: boolean): Promise<Listing[]> {
  const query = includeRejected ? "?include_rejected=1" : "";
  return requestJson<Listing[]>(`/api/listings${query}`);
}

export function getStatus(): Promise<Status> {
  return requestJson<Status>("/api/status");
}

export function getConfig(): Promise<SearchConfig> {
  return requestJson<SearchConfig>("/api/config");
}

export function saveConfig(cfg: Partial<SearchConfig>): Promise<SearchConfig> {
  return requestJson<SearchConfig>("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
}

export function rescore(): Promise<{ rescored: number }> {
  return requestJson<{ rescored: number }>("/api/rescore", { method: "POST" });
}
```

- [ ] **Step 3: Написать `frontend/src/components/ListingCard.tsx`**

```tsx
import { motion } from "framer-motion";
import type { Listing } from "../types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

function formatNumber(value: number | null): string {
  return value === null ? "—" : value.toLocaleString("ru-RU");
}

export function ListingCard({ listing }: { listing: Listing }) {
  const priceChanged = listing.last_price !== null && listing.price !== listing.last_price;

  return (
    <motion.a
      href={listing.url}
      target="_blank"
      rel="noreferrer"
      layout
      initial={{ opacity: 0, y: 16, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 260, damping: 20 }}
      className="block hover:scale-[1.02] transition-transform"
    >
      <Card className="rounded-xl shadow-md overflow-hidden">
        {listing.photo && (
          <img
            src={listing.photo}
            alt={listing.title}
            className="w-full h-40 object-cover"
            loading="lazy"
          />
        )}
        <CardContent className="p-4">
          <h3 className="font-sans font-semibold text-base mb-1 line-clamp-2">{listing.title}</h3>
          <p
            className={`font-mono text-xl font-medium mb-2 ${priceChanged ? "animate-price-flash rounded px-1" : ""}`}
          >
            {formatNumber(listing.price)} ₽
          </p>
          <div className="flex gap-2 mb-2">
            {listing.year && <Badge variant="secondary" className="font-mono">{listing.year}</Badge>}
            {listing.mileage !== null && (
              <Badge variant="secondary" className="font-mono">
                {formatNumber(listing.mileage)} км
              </Badge>
            )}
          </div>
          {listing.relevance_reason && (
            <p className="text-sm text-[hsl(var(--foreground))]/70">{listing.relevance_reason}</p>
          )}
        </CardContent>
      </Card>
    </motion.a>
  );
}
```

- [ ] **Step 4: Написать `frontend/src/components/StatusBar.tsx`**

```tsx
import type { Status } from "../types";

export function StatusBar({ status }: { status: Status }) {
  const stale =
    status.last_check !== null &&
    Date.now() - new Date(status.last_check).getTime() > 2 * 60 * 1000;

  return (
    <div
      className={`text-sm font-mono px-3 py-2 rounded-lg ${stale ? "bg-danger/20 text-danger" : "bg-white/5"}`}
    >
      Последняя проверка: {status.last_check ?? "ещё не было"} · Найдено: {status.last_count}
      {stale && " · фоновый поток мог остановиться"}
    </div>
  );
}
```

- [ ] **Step 5: Написать `frontend/src/components/SearchForm.tsx`**

```tsx
import { useState } from "react";
import type { SearchConfig } from "../types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  initial: Partial<SearchConfig>;
  onSave: (cfg: Partial<SearchConfig>) => Promise<void>;
}

export function SearchForm({ initial, onSave }: Props) {
  const [form, setForm] = useState<Partial<SearchConfig>>(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const field = (key: keyof SearchConfig) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setForm((prev) => ({
      ...prev,
      [key]: value === "" ? null : ["city", "brand", "query_text"].includes(key) ? value : Number(value),
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await onSave(form);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить настройки");
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="grid gap-3 rounded-xl bg-[hsl(var(--card))] p-4">
      <Input placeholder="Город (латиницей, напр. kazan)" value={form.city ?? ""} onChange={field("city")} required />
      <Input placeholder="Марка (напр. Kia)" value={form.brand ?? ""} onChange={field("brand")} />
      <div className="grid grid-cols-2 gap-3">
        <Input type="number" placeholder="Цена от" value={form.price_min ?? ""} onChange={field("price_min")} />
        <Input type="number" placeholder="Цена до" value={form.price_max ?? ""} onChange={field("price_max")} />
        <Input type="number" placeholder="Пробег от" value={form.mileage_min ?? ""} onChange={field("mileage_min")} />
        <Input type="number" placeholder="Пробег до" value={form.mileage_max ?? ""} onChange={field("mileage_max")} />
        <Input type="number" placeholder="Год от" value={form.year_min ?? ""} onChange={field("year_min")} />
        <Input type="number" placeholder="Год до" value={form.year_max ?? ""} onChange={field("year_max")} />
      </div>
      <Textarea
        placeholder="Текстовое уточнение (например: для семьи, не битая, механика)"
        value={form.query_text ?? ""}
        onChange={(e) => setForm((prev) => ({ ...prev, query_text: e.target.value }))}
      />
      {error && <p className="text-danger text-sm">{error}</p>}
      <Button type="submit" disabled={saving}>
        {saving ? "Собираю ссылку поиска…" : "Сохранить"}
      </Button>
    </form>
  );
}
```

- [ ] **Step 6: Переписать `frontend/src/App.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import { Toaster, toast } from "sonner";
import { AnimatePresence } from "framer-motion";
import { getConfig, getListings, getStatus, rescore, saveConfig } from "./api";
import type { Listing, SearchConfig, Status } from "./types";
import { ListingCard } from "./components/ListingCard";
import { SearchForm } from "./components/SearchForm";
import { StatusBar } from "./components/StatusBar";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";

const POLL_INTERVAL_MS = 60_000;

export default function App() {
  const [listings, setListings] = useState<Listing[]>([]);
  const [status, setStatus] = useState<Status>({ last_check: null, last_count: 0 });
  const [config, setConfig] = useState<Partial<SearchConfig>>({});
  const [showRejected, setShowRejected] = useState(false);
  const [rescoring, setRescoring] = useState(false);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const knownIds = new Set(listings.map((l) => l.id));
    const [freshListings, freshStatus] = await Promise.all([getListings(showRejected), getStatus()]);
    const newRelevant = freshListings.filter((l) => l.relevant === 1 && !knownIds.has(l.id));
    for (const item of newRelevant) {
      toast.success(`Новое объявление: ${item.title}`);
    }
    setListings(freshListings);
    setStatus(freshStatus);
    setLoading(false);
  }, [listings, showRejected]);

  useEffect(() => {
    getConfig().then(setConfig);
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showRejected]);

  const handleSave = async (cfg: Partial<SearchConfig>) => {
    const saved = await saveConfig(cfg);
    setConfig(saved);
    await refresh();
  };

  const handleRescore = async () => {
    setRescoring(true);
    try {
      const result = await rescore();
      toast.info(`Переоценено объявлений: ${result.rescored}`);
      await refresh();
    } finally {
      setRescoring(false);
    }
  };

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto grid gap-6">
      <Toaster richColors position="top-right" />
      <h1 className="text-2xl font-semibold">Поиск машин на Avito</h1>
      <SearchForm initial={config} onSave={handleSave} />
      <div className="flex items-center justify-between flex-wrap gap-3">
        <StatusBar status={status} />
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={showRejected} onCheckedChange={setShowRejected} />
            Показать отклонённые агентом
          </label>
          <Button variant="outline" onClick={handleRescore} disabled={rescoring}>
            {rescoring ? "Переоцениваю…" : "Переоценить"}
          </Button>
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {loading ? (
          Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-64 rounded-xl" />
          ))
        ) : (
          <AnimatePresence>
            {listings.map((listing) => (
              <ListingCard key={listing.id} listing={listing} />
            ))}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Собрать фронтенд и проверить, что сборка проходит**

Run: `cd frontend && npm run build`
Expected: сборка без ошибок TypeScript

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): форма поиска, карточки объявлений, опрос API раз в минуту"
```

---

### Task 10: README и CLAUDE.md — новые инструкции запуска

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Прочитать текущий `README.md`**

Run: посмотреть содержимое перед правкой (`cat README.md`), чтобы новый
раздел встал в существующий стиль документа.

- [ ] **Step 2: Добавить в `README.md` раздел про веб-режим**

Добавить новый раздел (после существующей инструкции CLI-режима):

```markdown
## Веб-режим: поиск машин с LLM-агентом

Дополнительно к CLI-режиму (`python main.py`) есть локальное веб-приложение
для поиска машин с фильтрацией и ранжированием через Claude.

### Установка

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# впишите в .env свой ANTHROPIC_API_KEY (https://console.anthropic.com/)

cd frontend
npm install
npm run build
cd ..
```

Требуется Node.js (LTS) и npm — только для сборки фронтенда, самому
приложению во время работы Node.js не нужен.

### Запуск

```bash
python app.py
```

Откройте `http://localhost:5000` — заполните форму поиска (город
латиницей, например `kazan`, марка, диапазоны цены/пробега/года,
текстовое уточнение) и нажмите «Сохранить». Приложение соберёт ссылку
поиска через реальную форму фильтров Avito (несколько секунд), после чего
раз в минуту будет опрашивать выдачу, оценивать релевантность через
Claude Haiku и показывать подходящие объявления.

**Внимание:** интервал в 1 минуту — сознательно ускоренный (обычно
рекомендуется не чаще раза в 5 минут, см. `CLAUDE.md`) и повышает риск
временной блокировки по IP.
```

- [ ] **Step 3: Дополнить `CLAUDE.md`**

В раздел «Структура» добавить перечисление новых файлов (`url_builder.py`,
`agent.py`, `worker.py`, `app.py`, `frontend/`), в раздел «Команды» — команды
веб-режима, аналогично Step 2 выше. В «Важные соглашения» добавить пункт:

```markdown
- Веб-режим (`app.py`) использует интервал 60 сек по прямой просьбе
  пользователя — это единственное отступление от «не уменьшать интервалы»,
  остальные задержки (`MIN_DELAY`/`MAX_DELAY`/джиттер) не меняются.
- `ANTHROPIC_API_KEY` — только через `.env`/переменную окружения, никогда
  не хардкодить в `config.py`.
```

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: инструкции по запуску веб-режима поиска машин"
```

---

### Task 11: Сквозная проверка на машине пользователя

Автоматических тестов для этой задачи нет — сети/браузера/реального
Claude API в песочнице нет (см. `CLAUDE.md`).

- [ ] **Step 1: Установить зависимости и собрать фронтенд**

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env  # вписать реальный ANTHROPIC_API_KEY
cd frontend && npm install && npm run build && cd ..
```

- [ ] **Step 2: Запустить весь набор тестов**

```bash
pytest -v
```

Expected: все тесты из Task 1–7 проходят.

- [ ] **Step 3: Запустить приложение и вручную проверить сценарий**

```bash
python app.py
```

Открыть `http://localhost:5000`, заполнить форму (реальный город/марка/
диапазоны/текст), сохранить, дождаться минимум двух циклов обновления.
Проверить: карточки появляются с фото/годом/пробегом/обоснованием агента,
цена отображается с разделителями разрядов, «Переоценить» и переключатель
«показать отклонённые» работают, статус-бар обновляет время проверки.

- [ ] **Step 4: Прислать результат**

Прислать лог консоли (`python app.py`) и, если что-то не так — скриншот
или текст ошибки, чтобы поправить конкретное место (селектор формы
фильтров, парсинг карточки и т.д.), а не считать фичу рабочей только по
исходному коду.

---

## Execution Handoff

После сохранения плана — выбор способа выполнения (см. ниже, задаётся
пользователю после публикации плана).
