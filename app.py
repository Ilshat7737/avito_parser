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
