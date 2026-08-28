"""
Фоновый цикл опроса Avito для веб-режима: скрапинг -> dedup ->
описание для новых -> батч-оценка агентом -> запись в БД -> уведомления ->
обрезка listings до лимита. Живёт в daemon-потоке, поднятом из app.py.

Поддерживает ДВЕ параллельные поисковые сессии (session_id 1 и 2) — два
независимых окна браузера с разными фильтрами, каждое в своём daemon-
потоке (см. `background_loop` и app.py). Перед тем как считать объявление
новым, `process_cycle` проверяет `db.recently_seen_elsewhere` — не
забрала ли уже то же самое объявление СОСЕДНЯЯ сессия за последние 24
часа; если да — пропускает его, не тратя запрос описания и вызов агента
и не показывая дублирующее уведомление.
"""

import random
import threading

import agent
import config
import db
from notify import console_log, notify_new_item, notify_price_change
from scraper import USER_AGENT, fetch_description, fetch_search_results, is_blocked_or_captcha

PAUSE_POLL_SEC = 3
# Размер группы для чанкинга описание+оценка+запись внутри одного цикла —
# совпадает с agent.BATCH_SIZE, чтобы не увеличивать число вызовов агента
# (каждая группа = ровно один вызов agent.evaluate_listings). Обрабатываем
# по группам, а не всё сразу, чтобы уже готовые объявления появлялись на
# экране (опрос /api/listings раз в 3 сек) по мере готовности, а не одним
# большим куском в самом конце цикла — при MIN_DELAY/MAX_DELAY=20-30 сек
# между запросами описаний цикл с десятками новых объявлений может идти
# много минут, и ждать его целиком, прежде чем показать хоть что-то, не
# годится.
CHUNK_SIZE = agent.BATCH_SIZE

# Окно, за которое объявление, обработанное соседней сессией, считается
# "тем же самым" и не обрабатывается повторно — прямая просьба
# пользователя ("за последние 24 часа").
CROSS_SESSION_DEDUP_HOURS = 24


def process_cycle(conn, items: list[dict], query_text: str, fetch_description_fn, session_id: int = 1, keep: int = 100):
    new_items = []
    for item in items:
        existing = db.get_listing(conn, item["id"], session_id)
        if existing is not None:
            if item["price"] is not None and existing["price"] != item["price"]:
                notify_price_change(existing, item)
                db.update_price(conn, item["id"], item["price"], session_id)
            else:
                db.touch(conn, item["id"], session_id)
            continue

        if db.is_seen(conn, item["id"], session_id):
            continue

        if db.recently_seen_elsewhere(conn, item["id"], session_id, hours=CROSS_SESSION_DEDUP_HOURS):
            # Уже обработано соседним окном недавно (пересечение фильтров) —
            # не скачиваем описание, не зовём агента повторно, не шлём
            # повторное уведомление. Отмечаем виденным и в этой сессии,
            # чтобы не перепроверять на каждой итерации, пока не истекут
            # 24 часа.
            db.mark_seen(conn, item["id"], session_id)
            continue

        new_items.append(item)

    if not new_items:
        return

    stripped_query = query_text.strip()
    inserted_any = False

    for start in range(0, len(new_items), CHUNK_SIZE):
        chunk = new_items[start:start + CHUNK_SIZE]

        for item in chunk:
            try:
                item["description"] = fetch_description_fn(item["url"])
            except Exception:
                item["description"] = ""

        if not stripped_query:
            verdicts = {item["id"]: {"relevant": True, "reason": "критерий не задан"} for item in chunk}
        else:
            verdicts = agent.evaluate_listings(chunk, stripped_query)

        for item in chunk:
            verdict = verdicts.get(item["id"])
            if verdict is None:
                continue  # ошибка агента — попробуем снова в следующем цикле

            item["relevant"] = 1 if verdict["relevant"] else 0
            item["relevance_reason"] = verdict["reason"]
            db.upsert_new(conn, item, session_id)
            db.mark_seen(conn, item["id"], session_id)
            inserted_any = True
            if verdict["relevant"]:
                notify_new_item(item)

    if inserted_any:
        db.evict_old_listings(conn, session_id, keep=keep)


def run_search_cycle(conn, page, search_url: str, query_text: str, session_id: int = 1) -> int | None:
    """Один проход по уже открытой странице: скрапинг -> process_cycle.
    Если после загрузки страницы похоже на капчу/блокировку — ставит
    паузу (`pause_reason="blocked"`) и возвращает None, не поднимая
    исключение (чтобы фоновый цикл не считал это обычным сбоем и не
    повторил попытку сразу же). Любая другая ошибка поднимается дальше —
    её обрабатывает уже существующий широкий try/except в background_loop."""
    try:
        items = fetch_search_results(page, search_url)
    except Exception:
        if is_blocked_or_captcha(page):
            console_log(
                f"⚠️ [сессия {session_id}] Avito показывает капчу или заблокировал "
                "доступ — парсинг приостановлен. Решите капчу в открытом окне "
                "браузера (если оно ещё открыто) или подождите, затем "
                "нажмите «Возобновить» в интерфейсе."
            )
            db.set_paused(conn, True, "blocked", session_id=session_id)
            return None
        raise

    process_cycle(conn, items, query_text, lambda url: fetch_description(page, url), session_id=session_id)
    return len(items)


def run_cycle_with_safety(conn, cycle_fn, session_id: int = 1):
    """Оборачивает один цикл сканирования: выставляет scanning=True на
    время выполнения (для индикатора прогрузки в интерфейсе) и
    гарантированно сбрасывает обратно в False. При любой непредвиденной
    ошибке (капча/блокировка уже обработана отдельно внутри
    run_search_cycle и сюда не долетает) ставит паузу с причиной "error"
    и текстом ошибки — вместо бесконечных автоматических повторов, по
    прямой просьбе пользователя: один раз не получилось — значит ждём
    ручного вмешательства, а не долбим Avito дальше. session_id — только
    для того, чтобы пауза/scanning относились к нужной сессии; каждая
    сессия ставится на паузу независимо от другой."""
    db.set_scanning(conn, True, session_id=session_id)
    try:
        return cycle_fn()
    except Exception as e:
        console_log(f"Ошибка фонового цикла [сессия {session_id}]: {e}")
        db.set_paused(conn, True, "error", str(e), session_id=session_id)
        return None
    finally:
        db.set_scanning(conn, False, session_id=session_id)


def background_loop(conn, stop_event: threading.Event, session_id: int = 1):
    """Фоновый цикл ОДНОЙ поисковой сессии. app.py поднимает два таких
    цикла в отдельных daemon-потоках (session_id=1 и session_id=2), у
    каждого — свой Playwright/Chromium (см. do_cycle ниже: новый браузер
    открывается на каждой итерации) => два реальных окна браузера
    работают одновременно, с собственными фильтрами и собственным
    статусом паузы/ошибки. Общий файл БД — конкурентный доступ обеспечен
    WAL-режимом SQLite (db.get_conn)."""
    console_log(f"Фоновый поток веб-приложения запущен (сессия {session_id})")
    from playwright.sync_api import sync_playwright

    while not stop_event.is_set():
        status = db.get_status(conn, session_id)
        if status["paused"]:
            stop_event.wait(PAUSE_POLL_SEC)
            continue

        cfg = db.get_search_config(conn, session_id)
        if cfg and cfg.get("search_url"):
            def do_cycle(cfg=cfg):
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=config.HEADLESS)
                    context = browser.new_context(user_agent=USER_AGENT, locale="ru-RU")
                    page = context.new_page()
                    count = run_search_cycle(
                        conn, page, cfg["search_url"], cfg.get("query_text") or "", session_id=session_id
                    )
                    browser.close()
                    return count

            count = run_cycle_with_safety(conn, do_cycle, session_id=session_id)
            if count is not None:
                db.set_last_check(conn, count=count, session_id=session_id)
                console_log(f"[сессия {session_id}] Проверено объявлений: {count}")
        else:
            console_log(f"[сессия {session_id}] Поиск ещё не настроен — жду настроек через веб-интерфейс")

        delay = config.CHECK_INTERVAL_SEC + random.uniform(0, config.INTERVAL_JITTER_SEC)
        stop_event.wait(delay)
