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
