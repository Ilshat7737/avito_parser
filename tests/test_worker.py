import sqlite3
import threading

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
    c.execute(db.CROSS_SESSION_CLAIMS_SCHEMA)
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

    worker.process_cycle(conn, 1, items, "надёжная машина", fake_fetch_description)

    stored = db.get_listing(conn, 1, "1")
    assert stored is not None
    assert stored["relevant"] == 1
    assert stored["description"] == "Тестовое описание"
    assert db.is_seen(conn, 1, "1") is True


def test_process_cycle_skips_items_evicted_but_still_seen(conn, monkeypatch):
    db.mark_seen(conn, 1, "2")  # вытеснено ранее, но встречено снова в выдаче
    called = {"agent": False}

    def spy_evaluate(items, query_text):
        called["agent"] = True
        return {}

    monkeypatch.setattr(worker.agent, "evaluate_listings", spy_evaluate)
    items = [{"id": "2", "title": "Старое объявление", "price": 100000, "url": "u2"}]

    worker.process_cycle(conn, 1, items, "текст", fake_fetch_description)

    assert db.get_listing(conn, 1, "2") is None  # не появилось заново в listings
    assert called["agent"] is False  # агент не звался для уже виденных


def test_process_cycle_updates_price_for_known_listing(conn, monkeypatch):
    db.upsert_new(conn, 1, {"id": "3", "title": "Известное", "price": 500000, "url": "u3"})

    def spy_evaluate(items, query_text):
        raise AssertionError("агент не должен звать для уже известных карточек")

    monkeypatch.setattr(worker.agent, "evaluate_listings", spy_evaluate)
    items = [{"id": "3", "title": "Известное", "price": 480000, "url": "u3"}]

    worker.process_cycle(conn, 1, items, "текст", fake_fetch_description)

    stored = db.get_listing(conn, 1, "3")
    assert stored["price"] == 480000


def test_process_cycle_leaves_item_unstored_when_agent_call_fails(conn, monkeypatch):
    monkeypatch.setattr(worker.agent, "evaluate_listings", lambda items, query_text: {})
    items = [{"id": "4", "title": "Новое", "price": 300000, "url": "u4"}]

    worker.process_cycle(conn, 1, items, "текст", fake_fetch_description)

    assert db.get_listing(conn, 1, "4") is None
    assert db.is_seen(conn, 1, "4") is False  # попробуем снова в следующем цикле


def test_process_cycle_skips_agent_when_query_text_is_blank(conn, monkeypatch):
    def fail_if_called(items, query_text):
        raise AssertionError("агент не должен вызываться при пустом критерии")
    monkeypatch.setattr(worker.agent, "evaluate_listings", fail_if_called)

    items = [{"id": "1", "title": "Kia Rio", "price": 700000, "url": "u1"}]
    worker.process_cycle(conn, 1, items, "   ", fake_fetch_description)

    stored = db.get_listing(conn, 1, "1")
    assert stored is not None
    assert stored["relevant"] == 1


def test_process_cycle_evicts_after_inserting_new_items(conn, monkeypatch):
    for i in range(3):
        db.upsert_new(conn, 1, {"id": str(i), "title": f"Item {i}", "price": 1000, "url": f"u{i}"})
        db.mark_seen(conn, 1, str(i))

    monkeypatch.setattr(
        worker.agent, "evaluate_listings",
        lambda items, query_text: {"new": {"relevant": True, "reason": "ok"}},
    )
    items = [{"id": "new", "title": "Новое", "price": 999999, "url": "u_new"}]

    worker.process_cycle(conn, 1, items, "текст", fake_fetch_description, keep=3)

    cur = conn.execute("SELECT id FROM listings WHERE session_id = 1")
    remaining_ids = {row[0] for row in cur.fetchall()}
    assert len(remaining_ids) == 3
    assert "new" in remaining_ids
    assert "0" not in remaining_ids  # самая старая запись вытеснена


def test_process_cycle_writes_results_incrementally_per_chunk(conn, monkeypatch):
    evaluate_calls = []

    def fake_evaluate(items, query_text):
        evaluate_calls.append([i["id"] for i in items])
        return {item["id"]: {"relevant": True, "reason": "ok"} for item in items}

    monkeypatch.setattr(worker.agent, "evaluate_listings", fake_evaluate)

    items = [
        {"id": str(i), "title": f"Item {i}", "price": 1000, "url": f"u{i}"}
        for i in range(worker.CHUNK_SIZE + 5)  # больше одного чанка
    ]

    worker.process_cycle(conn, 1, items, "текст", fake_fetch_description)

    # ровно два вызова агента (по чанкам), а не один на весь список
    assert len(evaluate_calls) == 2
    assert len(evaluate_calls[0]) == worker.CHUNK_SIZE
    assert len(evaluate_calls[1]) == 5

    # все объявления из обоих чанков в итоге сохранены
    assert db.get_listing(conn, 1, "0") is not None
    assert db.get_listing(conn, 1, str(worker.CHUNK_SIZE - 1)) is not None
    assert db.get_listing(conn, 1, str(worker.CHUNK_SIZE + 4)) is not None


def test_process_cycle_writes_first_chunk_before_fetching_second_chunk(conn, monkeypatch):
    order_log = []

    def fake_evaluate(items, query_text):
        order_log.append(("evaluate", [i["id"] for i in items]))
        return {item["id"]: {"relevant": True, "reason": "ok"} for item in items}

    def logging_fetch_description(url):
        order_log.append(("fetch", url))
        return "описание"

    monkeypatch.setattr(worker.agent, "evaluate_listings", fake_evaluate)

    items = [
        {"id": str(i), "title": f"Item {i}", "price": 1000, "url": f"u{i}"}
        for i in range(worker.CHUNK_SIZE + 2)
    ]

    worker.process_cycle(conn, 1, items, "текст", logging_fetch_description)

    # первый чанк должен быть оценён (evaluate) до того, как начнётся
    # выборка описаний для второго чанка — иначе объявления первого
    # чанка не появились бы в БД быстрее, чем весь цикл целиком
    first_evaluate_index = next(i for i, c in enumerate(order_log) if c[0] == "evaluate")
    second_chunk_fetch_index = next(
        i for i, c in enumerate(order_log)
        if c[0] == "fetch" and c[1] == f"u{worker.CHUNK_SIZE}"
    )
    assert first_evaluate_index < second_chunk_fetch_index


def test_run_search_cycle_pauses_on_detected_block(conn, monkeypatch):
    def raise_timeout(page, url):
        raise Exception('Timeout waiting for [data-marker="item"]')

    monkeypatch.setattr(worker, "fetch_search_results", raise_timeout)
    monkeypatch.setattr(worker, "is_blocked_or_captcha", lambda page: True)

    result = worker.run_search_cycle(conn, 1, object(), "https://example.com", "текст")

    assert result is None
    status = db.get_status(conn, 1)
    assert status["paused"] is True
    assert status["pause_reason"] == "blocked"


def test_run_search_cycle_reraises_non_block_errors(conn, monkeypatch):
    def raise_network_error(page, url):
        raise RuntimeError("сеть недоступна")

    monkeypatch.setattr(worker, "fetch_search_results", raise_network_error)
    monkeypatch.setattr(worker, "is_blocked_or_captcha", lambda page: False)

    with pytest.raises(RuntimeError, match="сеть недоступна"):
        worker.run_search_cycle(conn, 1, object(), "https://example.com", "текст")

    status = db.get_status(conn, 1)
    assert status["paused"] is False


def test_run_search_cycle_returns_item_count_on_success(conn, monkeypatch):
    monkeypatch.setattr(
        worker, "fetch_search_results",
        lambda page, url: [{"id": "1", "title": "Kia Rio", "price": 700000, "url": "u1"}],
    )
    monkeypatch.setattr(worker, "fetch_description", lambda page, url: "тестовое описание")
    monkeypatch.setattr(
        worker.agent, "evaluate_listings",
        lambda items, query_text: {"1": {"relevant": True, "reason": "ok"}},
    )

    result = worker.run_search_cycle(conn, 1, object(), "https://example.com", "текст")

    assert result == 1
    assert db.get_listing(conn, 1, "1") is not None


def test_run_cycle_with_safety_sets_scanning_during_execution_and_clears_after(conn):
    scanning_during_call = {}

    def fake_cycle():
        scanning_during_call["value"] = db.get_status(conn, 1)["scanning"]
        return 7

    result = worker.run_cycle_with_safety(conn, 1, fake_cycle)

    assert result == 7
    assert scanning_during_call["value"] is True
    status = db.get_status(conn, 1)
    assert status["scanning"] is False
    assert status["paused"] is False


def test_run_cycle_with_safety_pauses_with_error_on_exception_and_clears_scanning(conn):
    def failing_cycle():
        raise RuntimeError("что-то сломалось")

    result = worker.run_cycle_with_safety(conn, 1, failing_cycle)

    assert result is None
    status = db.get_status(conn, 1)
    assert status["scanning"] is False
    assert status["paused"] is True
    assert status["pause_reason"] == "error"
    assert "что-то сломалось" in status["last_error"]


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
