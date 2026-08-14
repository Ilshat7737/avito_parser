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


def _filters_appear_unapplied(result_url: str, **range_filters) -> bool:
    """True, если хотя бы один диапазонный фильтр (цена/пробег/год) был
    задан, а в итоговом URL нет параметра f= — по нему Avito кодирует эти
    фильтры (подтверждено вручную), значит заполнение формы, похоже, не
    сработало. Сравнение URL "было/стало" ненадёжно: собственный редирект
    Avito (например, добавление ?radius=...) может произойти независимо
    от заполнения фильтров и создать ложное впечатление, что что-то
    изменилось. Марка сюда не входит — подтверждённых данных о том, как
    она кодируется в URL, нет."""
    has_range_filters = any(v is not None for v in range_filters.values())
    return has_range_filters and "f=" not in result_url


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
    форму и возвращает получившийся URL. Поднимает исключение, если после
    заполнения формы ссылка не изменилась, хотя фильтры были заданы —
    чтобы не сохранить и не скрапить по факту нефильтрованную выдачу."""
    start_url = BASE_URL.format(city=city)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        try:
            context = browser.new_context(locale="ru-RU")
            page = context.new_page()
            page.goto(start_url, wait_until="domcontentloaded")
            before_url = page.url

            if brand:
                _select_brand(page, brand)

            _fill_range(page, "price-from", "price-to", price_min, price_max)
            _fill_range(page, "params[164669]-from", "params[164669]-to", year_min, year_max)
            _fill_range(page, "params[2687]-from", "params[2687]-to", mileage_min, mileage_max)

            page.keyboard.press("Tab")
            try:
                page.wait_for_function(
                    "url => location.href !== url", arg=before_url, timeout=15000
                )
            except Exception:
                pass  # фильтров могло не быть задано вовсе — проверим ниже

            result_url = page.url
        finally:
            browser.close()

    if _filters_appear_unapplied(
        result_url,
        price_min=price_min, price_max=price_max,
        mileage_min=mileage_min, mileage_max=mileage_max,
        year_min=year_min, year_max=year_max,
    ):
        raise RuntimeError(
            "Диапазонные фильтры (цена/пробег/год) заданы, но в итоговой "
            "ссылке нет параметра f= — похоже, заполнение формы не "
            "сработало (разметка формы могла измениться или сайт ответил "
            "медленнее обычного)"
        )

    return result_url
