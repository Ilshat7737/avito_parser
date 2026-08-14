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
    flask_app = Flask(__name__, static_folder=None)

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
