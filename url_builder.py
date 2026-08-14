"""
Сборка ссылки поиска Avito по критериям пользователя через реальную форму
фильтров (Playwright), а не через недокументированный формат параметра f=.
См. docs/superpowers/specs/2026-08-13-car-search-web-app-design.md,
раздел "Сборка ссылки поиска Avito".
"""

from playwright.sync_api import sync_playwright

import config

BASE_URL = "https://www.avito.ru/{city}/avtomobili"


def _fill_range(page, from_marker: str, to_marker: str, min_value, max_value):
    if min_value is not None:
        page.fill(f'[data-marker="{from_marker}/input"]', str(min_value))
    if max_value is not None:
        page.fill(f'[data-marker="{to_marker}/input"]', str(max_value))


def _select_brand(page, brand: str):
    search_input = '[data-marker="params[110000]/multiselect-outline-input/input"]'
    page.fill(search_input, brand)
    checkbox = page.locator(
        '[data-marker^="params[110000]/multiselect-button-checkbox"]',
        has_text=brand,
    ).first
    checkbox.wait_for(state="visible", timeout=5000)
    checkbox.click()


def build_search_url(
    city: str,
    brand: str | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    mileage_min: int | None = None,
    mileage_max: int | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
) -> str:
    """Открывает страницу поиска Avito, заполняет фильтры через реальную
    форму и возвращает получившийся URL."""
    start_url = BASE_URL.format(city=city)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        try:
            context = browser.new_context(locale="ru-RU")
            page = context.new_page()
            page.goto(start_url, wait_until="domcontentloaded")

            if brand:
                _select_brand(page, brand)

            _fill_range(page, "price-from", "price-to", price_min, price_max)
            _fill_range(page, "params[164669]-from", "params[164669]-to", year_min, year_max)
            _fill_range(page, "params[2687]-from", "params[2687]-to", mileage_min, mileage_max)

            page.keyboard.press("Tab")
            before_url = page.url
            try:
                page.wait_for_function(
                    "url => location.href !== url", arg=before_url, timeout=8000
                )
            except Exception:
                pass  # фильтры могли не менять URL (например, ничего не задано)

            return page.url
        finally:
            browser.close()
