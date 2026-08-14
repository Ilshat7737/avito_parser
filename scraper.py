"""
Сбор данных со страницы поиска Avito через Playwright.
Используются атрибуты data-marker — они меняются реже, чем CSS-классы.
"""

import re
import random
import time

from playwright.sync_api import sync_playwright

import config

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _sleep_human():
    time.sleep(random.uniform(config.MIN_DELAY, config.MAX_DELAY))


# Эвристика: точные data-marker страницы капчи/блокировки Avito не собраны
# (нужен реальный HTML/скриншот при следующей блокировке, как и для любого
# другого нового селектора в проекте) — пока определяем по совокупности
# признаков, а не по точному селектору.
BLOCK_INDICATORS = (
    "капч",
    "не робот",
    "доступ ограничен",
    "подозрительн",
    "unusual traffic",
)


def is_blocked_or_captcha(page) -> bool:
    """True, если страница похожа на капчу/блокировку Avito, а не на
    обычную выдачу: карточек объявлений нет, а текст страницы содержит
    один из BLOCK_INDICATORS."""
    if page.query_selector('[data-marker="item"]') is not None:
        return False
    try:
        page_text = page.inner_text("body").lower()
    except Exception:
        return False
    return any(indicator in page_text for indicator in BLOCK_INDICATORS)


def _extract_id(url: str) -> str:
    """Достаёт числовой ID объявления из ссылки вида .../item_1234567890"""
    match = re.search(r"_(\d+)(?:[/?]|$)", url)
    return match.group(1) if match else url


def _extract_mileage(params_text: str) -> int | None:
    """Пробег — первый сегмент data-marker="item-specific-params",
    например "42 313 км, 1.6 MT (106 л.с.), универсал, передний, бензин"."""
    first_segment = params_text.split(",")[0]
    digits = re.sub(r"\D", "", first_segment)
    return int(digits) if digits else None


def _extract_year(title: str) -> int | None:
    """Год выпуска на карточке отдельным маркером не отдаётся — он есть
    только в заголовке, например "..., 2019, 42 313 км"."""
    match = re.search(r"\b(19|20)\d{2}\b", title)
    return int(match.group(0)) if match else None


def _parse_card(card) -> dict | None:
    title_el = card.query_selector('[data-marker="item-title"]')
    if not title_el:
        return None

    title = title_el.inner_text().strip()
    href = title_el.get_attribute("href")
    if not href:
        return None
    url = href if href.startswith("http") else f"https://www.avito.ru{href}"

    price_el = card.query_selector('[data-marker="item-price"]')
    price_text = price_el.inner_text() if price_el else ""
    price_digits = re.sub(r"\D", "", price_text)
    price = int(price_digits) if price_digits else None

    img_el = card.query_selector("img")
    photo = img_el.get_attribute("src") if img_el else None

    params_el = card.query_selector('[data-marker="item-specific-params"]')
    mileage = _extract_mileage(params_el.inner_text()) if params_el else None
    year = _extract_year(title)

    return {
        "id": _extract_id(url),
        "title": title,
        "price": price,
        "url": url,
        "photo": photo,
        "mileage": mileage,
        "year": year,
    }


def fetch_search_results(page, search_url: str) -> list[dict]:
    page.goto(search_url, wait_until="domcontentloaded")
    _sleep_human()
    page.wait_for_selector('[data-marker="item"]', timeout=15000)

    cards = page.query_selector_all('[data-marker="item"]')
    items = []
    for card in cards:
        parsed = _parse_card(card)
        if parsed:
            items.append(parsed)
    return items


def fetch_description(page, item_url: str) -> str:
    page.goto(item_url, wait_until="domcontentloaded")
    _sleep_human()
    desc_el = page.query_selector('[data-marker="item-view/item-description"]')
    return desc_el.inner_text().strip() if desc_el else ""


def run_once(search_url: str, fetch_descriptions: bool = True) -> list[dict]:
    """Один полный проход: открыть браузер -> собрать карточки -> закрыть браузер."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        context = browser.new_context(user_agent=USER_AGENT, locale="ru-RU")
        page = context.new_page()

        items = fetch_search_results(page, search_url)

        if fetch_descriptions:
            for item in items:
                try:
                    item["description"] = fetch_description(page, item["url"])
                except Exception:
                    item["description"] = ""

        browser.close()
        return items
