"""
Flask-приложение: отдаёт фронтенд (frontend/dist) и JSON API, поднимает
ДВА фоновых потока опроса Avito при старте — по одному на каждую
поисковую сессию (session_id 1 и 2, см. db.py/worker.py). Обе сессии
работают параллельно, каждая в своём окне Chromium и со своими
фильтрами; см. `main()` ниже.

Все эндпоинты, работающие с данными конкретной сессии, принимают
query-параметр `?session=1|2` (по умолчанию 1 — так старые запросы без
параметра, включая существующие тесты, продолжают работать как раньше).
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


def _session_id_from_request() -> int:
    raw = request.args.get("session", "1")
    try:
        session_id = int(raw)
    except (TypeError, ValueError):
        session_id = 1
    return session_id if session_id in db.SESSION_IDS else 1


def create_app(conn) -> Flask:
    flask_app = Flask(__name__, static_folder=None)

    @flask_app.route("/api/listings")
    def api_listings():
        session_id = _session_id_from_request()
        include_rejected = request.args.get("include_rejected") == "1"
        return jsonify(db.get_relevant_listings(conn, session_id, include_rejected))

    @flask_app.route("/api/status")
    def api_status():
        session_id = _session_id_from_request()
        return jsonify(db.get_status(conn, session_id))

    @flask_app.route("/api/sessions")
    def api_sessions():
        """Сводка по обеим сессиям сразу — чтобы переключатель окон во
        фронтенде мог показать индикатор (сканирует/пауза/ошибка) для
        обеих сессий, не переключаясь на них."""
        return jsonify([
            {
                "session_id": sid,
                "config": db.get_search_config(conn, sid),
                "status": db.get_status(conn, sid),
            }
            for sid in db.SESSION_IDS
        ])

    @flask_app.route("/api/config", methods=["GET"])
    def api_config_get():
        session_id = _session_id_from_request()
        return jsonify(db.get_search_config(conn, session_id) or {})

    @flask_app.route("/api/config", methods=["POST"])
    def api_config_post():
        session_id = _session_id_from_request()
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "Ожидается тело запроса в формате JSON"}), 400
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
        db.save_search_config(conn, payload, session_id)
        return jsonify(db.get_search_config(conn, session_id))

    @flask_app.route("/api/rescore", methods=["POST"])
    def api_rescore():
        session_id = _session_id_from_request()
        cfg = db.get_search_config(conn, session_id)
        query_text = ((cfg or {}).get("query_text") or "").strip()
        items = db.get_all_listings(conn, session_id)

        if not query_text:
            for item in items:
                db.update_relevance(conn, item["id"], 1, "критерий не задан", session_id)
            return jsonify({"rescored": len(items)})

        verdicts = agent.evaluate_listings(items, query_text)
        for item in items:
            verdict = verdicts.get(item["id"])
            if verdict is None:
                continue
            db.update_relevance(conn, item["id"], 1 if verdict["relevant"] else 0, verdict["reason"], session_id)
        return jsonify({"rescored": len(verdicts)})

    @flask_app.route("/api/worker/pause", methods=["POST"])
    def api_worker_pause():
        session_id = _session_id_from_request()
        db.set_paused(conn, True, "manual", session_id=session_id)
        return jsonify(db.get_status(conn, session_id))

    @flask_app.route("/api/worker/resume", methods=["POST"])
    def api_worker_resume():
        session_id = _session_id_from_request()
        db.set_paused(conn, False, None, session_id=session_id)
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
    # навсегда, потому что db.set_scanning(conn, False) в worker.py
    # выполняется в finally и не срабатывает при жёстком убийстве
    # процесса. При старте ничего ещё не сканируется — сбрасываем явно
    # для ОБЕИХ сессий.
    for session_id in db.SESSION_IDS:
        db.set_scanning(conn, False, session_id=session_id)

    # Две независимые сессии — два daemon-потока, каждый со своим
    # Chromium-окном (открывается заново на каждой итерации цикла внутри
    # background_loop) и своим соединением с БД. Отдельное соединение на
    # поток (а не общий `conn`) — SQLite в WAL-режиме (db.get_conn)
    # рассчитан на несколько соединений к одному файлу из разных потоков;
    # общий объект-соединение на все три потока (2 воркера + Flask)
    # заметно чаще упирался бы в "database is locked" под конкурентной
    # записью.
    stop_event = threading.Event()
    worker_threads = []
    for session_id in db.SESSION_IDS:
        worker_conn = db.get_conn(config.WEB_DB_PATH)
        thread = threading.Thread(
            target=background_loop, args=(worker_conn, stop_event, session_id),
            daemon=True, name=f"worker-session-{session_id}",
        )
        thread.start()
        worker_threads.append(thread)

    flask_app = create_app(conn)
    flask_app.run(host="127.0.0.1", port=config.WEB_PORT, debug=False)


if __name__ == "__main__":
    main()
