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
