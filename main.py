"""
Точка входа: запускает цикл мониторинга объявлений Avito.

Запуск:
    python main.py            # непрерывный мониторинг (Ctrl+C для остановки)
    python main.py --once     # одна проверка и выход (удобно для cron)
"""

import random
import sys
import time

import config
import db
from scraper import run_once
from notify import notify_new_item, notify_price_change, console_log


def process_items(conn, items):
    for item in items:
        existing = db.get_listing(conn, item["id"])
        if existing is None:
            db.upsert_new(conn, item)
            notify_new_item(item)
        elif item["price"] is not None and existing["price"] != item["price"]:
            notify_price_change(existing, item)
            db.update_price(conn, item["id"], item["price"])
        else:
            db.touch(conn, item["id"])


def main_loop(once: bool = False):
    conn = db.get_conn(config.DB_PATH)
    console_log(f"Мониторинг запущен: {config.SEARCH_URL}")

    while True:
        try:
            items = run_once(config.SEARCH_URL, fetch_descriptions=config.FETCH_FULL_DESCRIPTION)
            console_log(f"Проверено объявлений: {len(items)}")
            process_items(conn, items)
        except Exception as e:
            console_log(f"Ошибка при проверке: {e}")

        if once:
            break

        delay = config.CHECK_INTERVAL_SEC + random.uniform(0, config.INTERVAL_JITTER_SEC)
        console_log(f"Следующая проверка через {int(delay)} сек.")
        time.sleep(delay)


if __name__ == "__main__":
    main_loop(once="--once" in sys.argv)
