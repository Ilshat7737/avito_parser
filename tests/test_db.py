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
    c.execute(db.CROSS_SESSION_CLAIMS_SCHEMA)
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
    db.upsert_new(conn, 1, item)

    stored = db.get_listing(conn, 1, "111")
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
    db.upsert_new(conn, 1, item)

    stored = db.get_listing(conn, 1, "222")
    assert stored["year"] is None
    assert stored["mileage"] is None
    assert stored["relevant"] is None
    assert stored["agent_evaluated_at"] is None


def test_evict_old_listings_keeps_only_most_recent(conn):
    for i in range(5):
        db.upsert_new(conn, 1, {
            "id": str(i), "title": f"Item {i}", "price": 1000,
            "url": f"https://avito.ru/{i}",
        })
    db.evict_old_listings(conn, 1, keep=3)

    cur = conn.execute("SELECT id FROM listings ORDER BY first_seen")
    remaining = [row[0] for row in cur.fetchall()]
    assert len(remaining) == 3
    assert remaining == ["2", "3", "4"]


def test_seen_ids_tracks_ids_independently_of_listings(conn):
    assert db.is_seen(conn, 1, "999") is False
    db.mark_seen(conn, 1, "999")
    assert db.is_seen(conn, 1, "999") is True

    db.upsert_new(conn, 1, {"id": "1", "title": "A", "price": 100, "url": "u"})
    db.evict_old_listings(conn, 1, keep=0)
    assert db.get_listing(conn, 1, "1") is None
    assert db.is_seen(conn, 1, "999") is True  # seen_ids не чистится эвикшеном


def test_get_relevant_listings_filters_by_relevant_flag(conn):
    db.upsert_new(conn, 1, {"id": "1", "title": "Relevant", "price": 100, "url": "u1", "relevant": 1})
    db.upsert_new(conn, 1, {"id": "2", "title": "Rejected", "price": 100, "url": "u2", "relevant": 0})
    db.upsert_new(conn, 1, {"id": "3", "title": "Unevaluated", "price": 100, "url": "u3"})

    relevant_only = db.get_relevant_listings(conn, 1)
    assert [r["id"] for r in relevant_only] == ["1"]

    with_rejected = db.get_relevant_listings(conn, 1, include_rejected=True)
    assert {r["id"] for r in with_rejected} == {"1", "2"}


def test_mark_all_for_rescore_clears_evaluation_fields(conn):
    db.upsert_new(conn, 1, {"id": "1", "title": "A", "price": 100, "url": "u", "relevant": 1, "relevance_reason": "ok"})
    db.mark_all_for_rescore(conn, 1)

    stored = db.get_listing(conn, 1, "1")
    assert stored["relevant"] is None
    assert stored["relevance_reason"] is None
    assert stored["agent_evaluated_at"] is None


def test_search_config_roundtrip(conn):
    assert db.get_search_config(conn, 1) is None

    db.save_search_config(conn, 1, {
        "city": "kazan", "brand": "Kia", "price_min": 500000, "price_max": 900000,
        "mileage_min": None, "mileage_max": 100000, "year_min": 2015, "year_max": None,
        "query_text": "надёжная семейная машина",
        "search_url": "https://www.avito.ru/kazan/avtomobili?f=abc",
    })
    cfg = db.get_search_config(conn, 1)
    assert cfg["city"] == "kazan"
    assert cfg["price_max"] == 900000
    assert cfg["query_text"] == "надёжная семейная машина"


def test_status_defaults_then_updates(conn):
    assert db.get_status(conn, 1) == {
        "last_check": None, "last_count": 0, "paused": False,
        "pause_reason": None, "last_error": None, "scanning": False,
    }

    db.set_last_check(conn, 1, count=12)
    status = db.get_status(conn, 1)
    assert status["last_count"] == 12
    assert status["last_check"] is not None


def test_set_scanning_toggles_status(conn):
    assert db.get_status(conn, 1)["scanning"] is False

    db.set_scanning(conn, 1, True)
    assert db.get_status(conn, 1)["scanning"] is True

    db.set_scanning(conn, 1, False)
    assert db.get_status(conn, 1)["scanning"] is False


def test_set_scanning_before_any_cycle_leaves_last_count_as_zero_not_null(conn):
    db.set_scanning(conn, 1, True)
    status = db.get_status(conn, 1)
    assert status["last_count"] == 0
    assert status["last_check"] is None


def test_set_paused_with_error_stores_message_and_resume_clears_it(conn):
    db.set_paused(conn, 1, True, "error", "сеть недоступна")
    status = db.get_status(conn, 1)
    assert status["paused"] is True
    assert status["pause_reason"] == "error"
    assert status["last_error"] == "сеть недоступна"

    db.set_paused(conn, 1, False, None)
    status = db.get_status(conn, 1)
    assert status["paused"] is False
    assert status["pause_reason"] is None
    assert status["last_error"] is None


def test_set_paused_before_any_cycle_leaves_last_count_as_zero_not_null(conn):
    # регрессия: до первого set_last_check строки worker_status ещё нет;
    # set_paused не должен создавать её с last_count=NULL
    db.set_paused(conn, 1, True, "manual")
    status = db.get_status(conn, 1)
    assert status["last_count"] == 0
    assert status["last_check"] is None


def test_set_paused_updates_status_and_survives_set_last_check(conn):
    db.set_paused(conn, 1, True, "manual")
    status = db.get_status(conn, 1)
    assert status["paused"] is True
    assert status["pause_reason"] == "manual"

    # set_last_check (обычный тик фонового цикла) не должен сбрасывать паузу
    db.set_last_check(conn, 1, count=5)
    status = db.get_status(conn, 1)
    assert status["paused"] is True
    assert status["pause_reason"] == "manual"

    db.set_paused(conn, 1, False, None)
    status = db.get_status(conn, 1)
    assert status["paused"] is False
    assert status["pause_reason"] is None


def test_get_unevaluated_listings_excludes_already_scored_items(conn):
    db.upsert_new(conn, 1, {"id": "1", "title": "Оценено", "price": 100, "url": "u1", "relevant": 1})
    db.upsert_new(conn, 1, {"id": "2", "title": "Не оценено", "price": 100, "url": "u2"})

    unevaluated = db.get_unevaluated_listings(conn, 1)
    assert [item["id"] for item in unevaluated] == ["2"]


def test_update_price_preserves_previous_price_for_change_indicator(conn):
    db.upsert_new(conn, 1, {"id": "1", "title": "A", "price": 100000, "url": "u"})
    db.update_price(conn, 1, "1", 90000)

    stored = db.get_listing(conn, 1, "1")
    assert stored["price"] == 90000
    assert stored["last_price"] == 100000  # предыдущая цена, не новая


def test_get_all_listings_returns_every_row_regardless_of_relevance(conn):
    db.upsert_new(conn, 1, {"id": "1", "title": "Relevant", "price": 100, "url": "u1", "relevant": 1})
    db.upsert_new(conn, 1, {"id": "2", "title": "Rejected", "price": 100, "url": "u2", "relevant": 0})
    db.upsert_new(conn, 1, {"id": "3", "title": "Unevaluated", "price": 100, "url": "u3"})

    all_listings = db.get_all_listings(conn, 1)
    assert {item["id"] for item in all_listings} == {"1", "2", "3"}


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
    status = db.get_status(conn, 1)
    assert status["last_count"] == 42  # старые данные не потерялись
    assert status["paused"] is False
    assert status["pause_reason"] is None
    assert status["last_error"] is None
    assert status["scanning"] is False

    # и новые функции теперь работают без sqlite3.OperationalError
    db.set_paused(conn, 1, True, "manual")
    assert db.get_status(conn, 1)["paused"] is True


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
        "INSERT INTO listings (id, title, price, url, photo, description, year, mileage, relevant, relevance_reason, agent_evaluated_at, first_seen, last_price, last_checked) "
        "VALUES ('legacy-1', 'Старое объявление', 100000, 'u', NULL, NULL, NULL, NULL, NULL, NULL, NULL, '2026-01-01T00:00:00', 100000, '2026-01-01T00:00:00')"
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


def test_migrate_to_sessions_atomic_on_crash_mid_migration(tmp_path, monkeypatch):
    """Регрессия на находку ревью: _migrate_to_sessions должна быть
    атомарной. Симулируем падение процесса ПОСЛЕ того, как для listings
    уже выполнились RENAME/CREATE(с session_id)/INSERT, но ДО DROP TABLE
    listings_old и до финального COMMIT — раньше в этой точке RENAME/
    CREATE успевали закоммититься по отдельности (неявный коммит sqlite3
    перед каждым DDL-стейтментом), и guard-чек ("session_id" in listings)
    при следующем запуске считал бы миграцию завершённой, хотя реальные
    данные остались бы недостижимы в listings_old."""
    db_path = str(tmp_path / "legacy_crash.db")
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

    # sqlite3.Connection — неизменяемый C-тип, его методы нельзя
    # подменить через monkeypatch.setattr на классе напрямую. Вместо
    # этого подсовываем db.get_conn() (через sqlite3.connect(...,
    # factory=...)) подкласс Connection, у которого execute() падает на
    # конкретном DDL-стейтменте — имитация обрыва процесса в этой точке.
    class CrashingConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str) and "DROP TABLE listings_old" in sql:
                raise RuntimeError("симулированное падение процесса посреди миграции")
            return super().execute(sql, *args, **kwargs)

    original_connect = sqlite3.connect

    def connect_with_crashing_factory(*args, **kwargs):
        kwargs["factory"] = CrashingConnection
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect_with_crashing_factory)

    with pytest.raises(RuntimeError):
        db.get_conn(db_path)

    monkeypatch.undo()  # дальше открываем БД уже настоящим execute, без подмены

    # "Перезапуск процесса" — свежее соединение с тем же файлом.
    conn2 = db.get_conn(db_path)

    # Ключевой инвариант: ничего не потеряно и не осиротело — миграция
    # либо полностью откатилась и корректно повторилась при переоткрытии,
    # либо не сломала данные. Проверяем итоговое видимое состояние.
    assert db.get_listing(conn2, 1, "legacy-1")["title"] == "Старое объявление"
    assert db.is_seen(conn2, 1, "legacy-1") is True
    assert db.get_search_config(conn2, 1)["city"] == "kazan"
    assert db.get_status(conn2, 1)["last_count"] == 7

    tables = {row[0] for row in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(t.endswith("_old") for t in tables), f"осиротевшие таблицы: {tables}"

    # миграция реально применилась (не осталась висеть в недомигрированном
    # состоянии) — сессия 2 доступна и независима от сессии 1
    assert db.get_all_listings(conn2, 2) == []
    db.save_search_config(conn2, 2, {"city": "moskva"})
    assert db.get_search_config(conn2, 2)["city"] == "moskva"
