"""
Уведомления о новых объявлениях и изменении цены.
По умолчанию — просто вывод в консоль. Telegram включается в config.py.
"""

from datetime import datetime

import config

try:
    import requests
except ImportError:
    requests = None


def console_log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _send_telegram(text: str):
    if not (config.TELEGRAM_ENABLED and config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    if requests is None:
        console_log("Для Telegram-уведомлений установите пакет requests")
        return
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as e:
        console_log(f"Не удалось отправить в Telegram: {e}")


def notify_new_item(item: dict, session_id: int | None = None):
    price = f"{item['price']} ₽" if item["price"] else "цена не указана"
    prefix = f"[Сессия {session_id}] " if session_id is not None else ""
    text = f"{prefix}🆕 Новое объявление: {item['title']} — {price}\n{item['url']}"
    console_log(text)
    _send_telegram(text)


def notify_price_change(old_item: dict, new_item: dict, session_id: int | None = None):
    prefix = f"[Сессия {session_id}] " if session_id is not None else ""
    text = (
        f"{prefix}💰 Изменилась цена: {new_item['title']}\n"
        f"{old_item['price']} ₽ → {new_item['price']} ₽\n"
        f"{new_item['url']}"
    )
    console_log(text)
    _send_telegram(text)
