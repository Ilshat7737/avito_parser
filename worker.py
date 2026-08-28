"""
Фоновый цикл опроса Avito для веб-режима: скрапинг -> dedup ->
описание для новых -> батч-оценка агентом -> запись в БД -> уведомления ->
обрезка listings до лимита. Живёт в daemon-потоке, поднятом из app.py.

Поддерживает ровно две независимые поисковые сессии (session_id ∈
{1, 2}). Чтобы не увеличивать частоту запросов к Avito, фоновый цикл не
опрашивает обе сессии одновременно — один поток, одна сессия за
итерацию, сессии чередуются. Кросс-сессионный дедуп (cross_session_claims,
db.py) не даёт одному и тому же объявлению попасть в обе сессии в
течение 24 часов: та сессия, что обработала объявление первой, "клеймит"
его, вторая тихо пропускает.
"""

import random
import threading

import agent
import config
import db
from notify import console_log, notify_new_item, notify_price_change
from scraper import USER_AGENT, fetch_description, fetch_search_results, is_blocked_or_captcha

PAUSE_POLL_SEC = 3


def process_cycle(conn, session_id: int, items: list[dict], query_text: str, fetch_description_fn, keep: int = 100):
    new_items = []
    for item in items:
        existing = db.get_listing(conn, session_id, item["id"])
        if existing is not None:
            if item["price"] is not None and existing["price"] != item["price"]:
                notify_price_change(existing, item, session_id=session_id)
                db.update_price(conn, session_id, item["id"], item["price"])
            else:
                db.touch(conn, session_id, item["id"])
            continue

        if db.is_seen(conn, session_id, item["id"]):
            continue

        if db.is_claimed_by_other_session(conn, session_id, item["id"]):
            # Соседняя сессия уже обработала это объявление менее 24ч
            # назад — не показываем его здесь и не помечаем "виденным",
            # чтобы после истечения окна эта сессия могла подхватить его
            # как новое (см. SPEC.md, сценарий 4).
            continue

        new_items.append(item)

    if not new_items:
        return

    stripped_query = query_text.strip()
    inserted_any = False

    # Каждое объявление — описание, оценка, запись — обрабатывается и
    # сохраняется по отдельности, а не пачкой: при MIN_DELAY/MAX_DELAY
    # 20-30 сек между запросами описаний пачка даже в несколько штук
    # держит новые карточки невидимыми на экране (опрос /api/listings
    # раз в 3 сек) заметное время. Цена — до одного вызова
    # agent.evaluate_listings на объявление вместо одного на пачку;
    # обычный ритм скрапинга (объявление раз в 20-30 сек за счёт самого
    # fetch_description) сам по себе держит вызовы агента дальше друг от
    # друга, чем лимит запросов/минуту бесплатного тарифа Gemini.
    for item in new_items:
        try:
            item["description"] = fetch_description_fn(item["url"])
        except Exception:
            item["description"] = ""

        if not stripped_query:
            verdict = {"relevant": True, "reason": "критерий не задан"}
        else:
            verdicts = agent.evaluate_listings([item], stripped_query)
            verdict = verdicts.get(item["id"])
            if verdict is None:
                # Агент недоступен (ошибка API/сети) или не вернул вердикт
                # (например, ответ обрезан по max_tokens) — не прячем
                # объявление молча до следующего цикла: показываем его
                # как есть, без оценки, а не теряем из-за временного
                # сбоя стороннего сервиса.
                verdict = {"relevant": True, "reason": "агент недоступен — показано без оценки"}

        item["relevant"] = 1 if verdict["relevant"] else 0
        item["relevance_reason"] = verdict["reason"]
        db.upsert_new(conn, session_id, item)
        db.mark_seen(conn, session_id, item["id"])
        if verdict["relevant"]:
            db.claim_for_session(conn, session_id, item["id"])
            notify_new_item(item, session_id=session_id)
        inserted_any = True

    if inserted_any:
        db.evict_old_listings(conn, session_id, keep=keep)


def run_search_cycle(conn, session_id: int, page, search_url: str, query_text: str) -> int | None:
    """Один проход по уже открытой странице: скрапинг -> process_cycle.
    Если после загрузки страницы похоже на капчу/блокировку — ставит
    паузу (`pause_reason="blocked"`) этой сессии и возвращает None, не
    поднимая исключение (чтобы фоновый цикл не считал это обычным сбоем
    и не повторил попытку сразу же). Любая другая ошибка поднимается
    дальше — её обрабатывает уже существующий широкий try/except в
    background_loop."""
    try:
        items = fetch_search_results(page, search_url)
    except Exception:
        if is_blocked_or_captcha(page):
            console_log(
                f"⚠️ [Сессия {session_id}] Avito показывает капчу или заблокировал "
                "доступ — парсинг этой сессии приостановлен. Решите капчу в открытом "
                "окне браузера (если оно ещё открыто) или подождите, затем нажмите "
                "«Возобновить» в интерфейсе."
            )
            db.set_paused(conn, session_id, True, "blocked")
            return None
        raise

    process_cycle(conn, session_id, items, query_text, lambda url: fetch_description(page, url))
    return len(items)


def run_cycle_with_safety(conn, session_id: int, cycle_fn):
    """Оборачивает один цикл сканирования одной сессии: выставляет
    scanning=True на время выполнения (для индикатора прогрузки в
    интерфейсе) и гарантированно сбрасывает обратно в False. При любой
    непредвиденной ошибке (капча/блокировка уже обработана отдельно
    внутри run_search_cycle и сюда не долетает) ставит паузу этой сессии
    с причиной "error" и текстом ошибки — вместо бесконечных
    автоматических повторов, по прямой просьбе пользователя: один раз не
    получилось — значит ждём ручного вмешательства, а не долбим Avito
    дальше. Пауза одной сессии не трогает статус другой."""
    db.set_scanning(conn, session_id, True)
    try:
        return cycle_fn()
    except Exception as e:
        console_log(f"[Сессия {session_id}] Ошибка фонового цикла: {e}")
        db.set_paused(conn, session_id, True, "error", str(e))
        return None
    finally:
        db.set_scanning(conn, session_id, False)


def background_loop(conn, stop_event: threading.Event):
    console_log("Фоновый поток веб-приложения запущен")
    from playwright.sync_api import sync_playwright

    current_session_id = 1
    unconfigured_warned: set[int] = set()

    while not stop_event.is_set():
        session_id = current_session_id
        current_session_id = 2 if current_session_id == 1 else 1

        status = db.get_status(conn, session_id)
        if status["paused"]:
            stop_event.wait(PAUSE_POLL_SEC)
            continue

        cfg = db.get_search_config(conn, session_id)
        if cfg and cfg.get("search_url"):
            unconfigured_warned.discard(session_id)

            def do_cycle(cfg=cfg, session_id=session_id):
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=config.HEADLESS)
                    context = browser.new_context(user_agent=USER_AGENT, locale="ru-RU")
                    page = context.new_page()
                    count = run_search_cycle(
                        conn, session_id, page, cfg["search_url"], cfg.get("query_text") or ""
                    )
                    browser.close()
                    return count

            count = run_cycle_with_safety(conn, session_id, do_cycle)
            if count is not None:
                db.set_last_check(conn, session_id, count=count)
                console_log(f"[Сессия {session_id}] Проверено объявлений: {count}")

            delay = config.CHECK_INTERVAL_SEC + random.uniform(0, config.INTERVAL_JITTER_SEC)
            stop_event.wait(delay)
        else:
            if session_id not in unconfigured_warned:
                unconfigured_warned.add(session_id)
                console_log(f"[Сессия {session_id}] Поиск ещё не настроен — жду настроек через веб-интерфейс")
            stop_event.wait(PAUSE_POLL_SEC)
