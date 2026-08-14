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


def test_process_cycle_skips_agent_when_query_text_is_blank(conn, monkeypatch):
    def fail_if_called(items, query_text):
        raise AssertionError("агент не должен вызываться при пустом критерии")
    monkeypatch.setattr(worker.agent, "evaluate_listings", fail_if_called)

    items = [{"id": "1", "title": "Kia Rio", "price": 700000, "url": "u1"}]
    worker.process_cycle(conn, items, "   ", fake_fetch_description)

    stored = db.get_listing(conn, "1")
    assert stored is not None
    assert stored["relevant"] == 1


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
