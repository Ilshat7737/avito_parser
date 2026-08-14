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
    assert resp.get_json() == {
        "last_check": None, "last_count": 0, "paused": False,
        "pause_reason": None, "last_error": None, "scanning": False,
    }


def test_api_worker_pause_and_resume(client, conn):
    resp = client.post("/api/worker/pause")
    assert resp.status_code == 200
    assert resp.get_json()["paused"] is True
    assert resp.get_json()["pause_reason"] == "manual"
    assert db.get_status(conn)["paused"] is True

    resp = client.post("/api/worker/resume")
    assert resp.status_code == 200
    assert resp.get_json()["paused"] is False
    assert resp.get_json()["pause_reason"] is None
    assert db.get_status(conn)["paused"] is False


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
    db.save_search_config(conn, {"city": "kazan", "query_text": "тест"})
    monkeypatch.setattr(
        app_module.agent, "evaluate_listings",
        lambda items, query_text: {"1": {"relevant": True, "reason": "Подходит"}},
    )

    resp = client.post("/api/rescore")

    assert resp.status_code == 200
    assert resp.get_json() == {"rescored": 1}
    assert db.get_listing(conn, "1")["relevant"] == 1


def test_api_rescore_skips_agent_when_query_text_is_empty(client, conn, monkeypatch):
    db.upsert_new(conn, {"id": "1", "title": "Kia Rio", "price": 700000, "url": "u1"})

    def fail_if_called(items, query_text):
        raise AssertionError("агент не должен вызываться при пустом критерии")
    monkeypatch.setattr(app_module.agent, "evaluate_listings", fail_if_called)

    resp = client.post("/api/rescore")

    assert resp.status_code == 200
    assert resp.get_json() == {"rescored": 1}
    assert db.get_listing(conn, "1")["relevant"] == 1


def test_api_config_post_rejects_non_json_body(client):
    resp = client.post("/api/config", data="not json", content_type="text/plain")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_serve_frontend_falls_back_to_index_for_spa_routes(conn, tmp_path, monkeypatch):
    """
    Verify that SPA routes (paths that aren't real files) fall back to index.html,
    and real static assets are still served correctly.
    This test confirms that Flask's implicit static route doesn't shadow serve_frontend.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>SPA shell</html>")
    (dist / "assets" / "app.js").write_text("console.log('hi');")

    monkeypatch.setattr(app_module, "FRONTEND_DIST", str(dist))

    flask_app = app_module.create_app(conn)
    flask_app.testing = True
    client = flask_app.test_client()

    resp_root = client.get("/")
    assert resp_root.status_code == 200
    assert b"SPA shell" in resp_root.data

    resp_asset = client.get("/assets/app.js")
    assert resp_asset.status_code == 200
    assert b"console.log" in resp_asset.data

    resp_spa_route = client.get("/some/spa/route")
    assert resp_spa_route.status_code == 200
    assert b"SPA shell" in resp_spa_route.data
