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
    assert db.get_status(conn) == {
        "last_check": None, "last_count": 0, "paused": False,
        "pause_reason": None, "last_error": None, "scanning": False,
    }

    db.set_last_check(conn, count=12)
    status = db.get_status(conn)
    assert status["last_count"] == 12
    assert status["last_check"] is not None


def test_set_scanning_toggles_status(conn):
    assert db.get_status(conn)["scanning"] is False

    db.set_scanning(conn, True)
    assert db.get_status(conn)["scanning"] is True

    db.set_scanning(conn, False)
    assert db.get_status(conn)["scanning"] is False


def test_set_scanning_before_any_cycle_leaves_last_count_as_zero_not_null(conn):
    db.set_scanning(conn, True)
    status = db.get_status(conn)
    assert status["last_count"] == 0
    assert status["last_check"] is None


def test_set_paused_with_error_stores_message_and_resume_clears_it(conn):
    db.set_paused(conn, True, "error", "сеть недоступна")
    status = db.get_status(conn)
    assert status["paused"] is True
    assert status["pause_reason"] == "error"
    assert status["last_error"] == "сеть недоступна"

    db.set_paused(conn, False, None)
    status = db.get_status(conn)
    assert status["paused"] is False
    assert status["pause_reason"] is None
    assert status["last_error"] is None


def test_set_paused_before_any_cycle_leaves_last_count_as_zero_not_null(conn):
    # регрессия: до первого set_last_check строки worker_status ещё нет;
    # set_paused не должен создавать её с last_count=NULL
    db.set_paused(conn, True, "manual")
    status = db.get_status(conn)
    assert status["last_count"] == 0
    assert status["last_check"] is None


def test_set_paused_updates_status_and_survives_set_last_check(conn):
    db.set_paused(conn, True, "manual")
    status = db.get_status(conn)
    assert status["paused"] is True
    assert status["pause_reason"] == "manual"

    # set_last_check (обычный тик фонового цикла) не должен сбрасывать паузу
    db.set_last_check(conn, count=5)
    status = db.get_status(conn)
    assert status["paused"] is True
    assert status["pause_reason"] == "manual"

    db.set_paused(conn, False, None)
    status = db.get_status(conn)
    assert status["paused"] is False
    assert status["pause_reason"] is None


def test_get_unevaluated_listings_excludes_already_scored_items(conn):
    db.upsert_new(conn, {"id": "1", "title": "Оценено", "price": 100, "url": "u1", "relevant": 1})
    db.upsert_new(conn, {"id": "2", "title": "Не оценено", "price": 100, "url": "u2"})

    unevaluated = db.get_unevaluated_listings(conn)
    assert [item["id"] for item in unevaluated] == ["2"]


def test_update_price_preserves_previous_price_for_change_indicator(conn):
    db.upsert_new(conn, {"id": "1", "title": "A", "price": 100000, "url": "u"})
    db.update_price(conn, "1", 90000)

    stored = db.get_listing(conn, "1")
    assert stored["price"] == 90000
    assert stored["last_price"] == 100000  # предыдущая цена, не новая


def test_get_all_listings_returns_every_row_regardless_of_relevance(conn):
    db.upsert_new(conn, {"id": "1", "title": "Relevant", "price": 100, "url": "u1", "relevant": 1})
    db.upsert_new(conn, {"id": "2", "title": "Rejected", "price": 100, "url": "u2", "relevant": 0})
    db.upsert_new(conn, {"id": "3", "title": "Unevaluated", "price": 100, "url": "u3"})

    all_listings = db.get_all_listings(conn)
    assert {item["id"] for item in all_listings} == {"1", "2", "3"}


def test_listings_are_isolated_per_session(conn):
    db.upsert_new(conn, {"id": "1", "title": "Сессия 1", "price": 100, "url": "u1"}, session_id=1)
    db.upsert_new(conn, {"id": "1", "title": "Сессия 2 (тот же id)", "price": 200, "url": "u2"}, session_id=2)

    assert db.get_listing(conn, "1", session_id=1)["title"] == "Сессия 1"
    assert db.get_listing(conn, "1", session_id=2)["title"] == "Сессия 2 (тот же id)"
    assert [i["id"] for i in db.get_all_listings(conn, session_id=1)] == ["1"]
    assert [i["id"] for i in db.get_all_listings(conn, session_id=2)] == ["1"]


def test_search_config_and_worker_status_isolated_per_session(conn):
    db.save_search_config(conn, {"city": "kazan", "label": "Окно 1"}, session_id=1)
    db.save_search_config(conn, {"city": "moskva", "label": "Окно 2"}, session_id=2)

    assert db.get_search_config(conn, session_id=1)["city"] == "kazan"
    assert db.get_search_config(conn, session_id=2)["city"] == "moskva"

    db.set_paused(conn, True, "manual", session_id=1)
    assert db.get_status(conn, session_id=1)["paused"] is True
    assert db.get_status(conn, session_id=2)["paused"] is False


def test_recently_seen_elsewhere_detects_other_session_within_window(conn):
    db.mark_seen(conn, "42", session_id=1)

    assert db.recently_seen_elsewhere(conn, "42", session_id=2, hours=24) is True
    # своя же сессия не считается "другой"
    assert db.recently_seen_elsewhere(conn, "42", session_id=1, hours=24) is False
    # id, которого никто не видел, не пересекается
    assert db.recently_seen_elsewhere(conn, "999", session_id=2, hours=24) is False


def test_recently_seen_elsewhere_ignores_entries_older_than_window(conn):
    from datetime import datetime, timedelta

    old_ts = (datetime.now() - timedelta(hours=25)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO seen_ids (id, session_id, first_seen) VALUES (?, ?, ?)",
        ("42", 1, old_ts),
    )
    conn.commit()

    assert db.recently_seen_elsewhere(conn, "42", session_id=2, hours=24) is False


def test_get_conn_migrates_old_worker_status_schema(tmp_path):
    # Симулируем файл БД от старой версии кода: только id/last_check/last_count,
    # без paused/pause_reason/last_error/scanning.
    db_path = str(tmp_path / "old.db")
    old_conn = sqlite3.connect(db_path)
    old_conn.execute(
        """CREATE TABLE worker_status (
               id INTEGER PRIMARY KEY CHECK (id = 1),
               last_check TEXT,
               last_count INTEGER
           )"""
    )
    old_conn.execute("INSERT INTO worker_status (id, last_check, last_count) VALUES (1, '2026-01-01T00:00:00', 42)")
    old_conn.commit()
    old_conn.close()

    conn = db.get_conn(db_path)
    status = db.get_status(conn)
    assert status["last_count"] == 42  # старые данные не потерялись
    assert status["paused"] is False
    assert status["pause_reason"] is None
    assert status["last_error"] is None
    assert status["scanning"] is False

    # и новые функции теперь работают без sqlite3.OperationalError
    db.set_paused(conn, True, "manual")
    assert db.get_status(conn)["paused"] is True


def test_get_conn_migrates_old_listings_and_search_config_schema(tmp_path):
    # Файл БД от версии кода до многосессионного режима: listings PK по
    # id (без session_id), search_config — одна строка id=1 (без session_id).
    db_path = str(tmp_path / "old.db")
    old_conn = sqlite3.connect(db_path)
    old_conn.execute(
        """CREATE TABLE listings (
               id TEXT PRIMARY KEY, title TEXT, price INTEGER, url TEXT,
               photo TEXT, description TEXT, year INTEGER, mileage INTEGER,
               relevant INTEGER, relevance_reason TEXT, agent_evaluated_at TEXT,
               first_seen TEXT, last_price INTEGER, last_checked TEXT
           )"""
    )
    old_conn.execute(
        "INSERT INTO listings (id, title, price, url, first_seen, last_price, last_checked) "
        "VALUES ('1', 'Старое объявление', 500000, 'u1', '2026-01-01T00:00:00', 500000, '2026-01-01T00:00:00')"
    )
    old_conn.execute(
        """CREATE TABLE search_config (
               id INTEGER PRIMARY KEY CHECK (id = 1), city TEXT, brand TEXT,
               price_min INTEGER, price_max INTEGER, mileage_min INTEGER,
               mileage_max INTEGER, year_min INTEGER, year_max INTEGER,
               query_text TEXT, search_url TEXT, updated_at TEXT
           )"""
    )
    old_conn.execute(
        "INSERT INTO search_config (id, city, search_url) VALUES (1, 'kazan', 'https://old-url')"
    )
    old_conn.commit()
    old_conn.close()

    conn = db.get_conn(db_path)

    stored = db.get_listing(conn, "1", session_id=1)
    assert stored is not None
    assert stored["title"] == "Старое объявление"

    cfg = db.get_search_config(conn, session_id=1)
    assert cfg["city"] == "kazan"
    assert cfg["search_url"] == "https://old-url"

    # вторая сессия свободна и не видит данные первой
    assert db.get_listing(conn, "1", session_id=2) is None
    assert db.get_search_config(conn, session_id=2) is None
