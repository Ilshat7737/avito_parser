"""
Сборка ссылки поиска Avito по критериям пользователя через реальную форму
фильтров (Playwright), а не через недокументированный формат параметра f=.
Взаимодействие с формой намеренно медленное и человекоподобное —
посимвольный ввод со случайными паузами, плавный скролл, наведение мышью
перед кликом. Резкие программные действия (мгновенная подстановка
значения через page.fill, клик без паузы) на практике не всегда
приводят к обновлению ссылки — форма реагирует стабильнее на ввод,
похожий на настоящий.
См. docs/superpowers/specs/2026-08-13-car-search-web-app-design.md,
раздел "Сборка ссылки поиска Avito".
"""

import random
import re
import time

from playwright.sync_api import sync_playwright

import config

BASE_URL = "https://www.avito.ru/{city}/avtomobili"
TYPE_RETRIES = 2


def _sleep_human():
    time.sleep(random.uniform(config.MIN_DELAY, config.MAX_DELAY))


def _human_scroll(page, distance: int = 300, steps: int = 5):
    step = distance / steps
    for _ in range(steps):
        page.mouse.wheel(0, step)
        time.sleep(random.uniform(0.15, 0.35))


def _type_like_human(page, locator, text: str, verify_digits: bool = True):
    """Скроллит поле в область видимости, кликает по нему с небольшой
    паузой, затем вводит текст посимвольно со случайной задержкой между
    нажатиями — вместо мгновенной подстановки значения одним вызовом.

    Поля с маской форматирования (разделители разрядов, например
    "700 000") на практике иногда портят значение при посимвольном вводе
    (курсор сбивается на переформатировании) — после ввода сверяем, что
    цифры в поле совпадают с запрошенными, и перепечатываем заново, если
    нет. Если после TYPE_RETRIES попыток значение всё ещё не совпадает —
    поднимаем исключение, а не продолжаем с неверным значением."""
    for attempt in range(TYPE_RETRIES + 1):
        locator.scroll_into_view_if_needed()
        time.sleep(random.uniform(0.3, 0.7))
        locator.click()
        time.sleep(random.uniform(0.1, 0.2))
        page.keyboard.press("Control+A")
        time.sleep(random.uniform(0.1, 0.2))
        page.keyboard.press("Backspace")
        time.sleep(random.uniform(0.1, 0.2))
        for char in text:
            page.keyboard.press(char)
            time.sleep(random.uniform(0.06, 0.22))

        if not verify_digits:
            return

        time.sleep(0.3)
        actual_digits = re.sub(r"\D", "", locator.input_value())
        if actual_digits == text:
            return

    raise RuntimeError(
        f"Не удалось корректно ввести значение {text!r} в поле формы "
        f"после {TYPE_RETRIES + 1} попыток (в поле оказалось {actual_digits!r})"
    )


def _fill_range(page, from_marker: str, to_marker: str, min_value, max_value):
    if min_value is not None:
        _type_like_human(page, page.locator(f'[data-marker="{from_marker}/input"]'), str(min_value))
        _sleep_human()
    if max_value is not None:
        _type_like_human(page, page.locator(f'[data-marker="{to_marker}/input"]'), str(max_value))
        _sleep_human()


def _select_brand(page, brand: str):
    search_input = page.locator('[data-marker="params[110000]/multiselect-outline-input/input"]')
    # Марка — не число, цифровую сверку значения тут применить нельзя
    _type_like_human(page, search_input, brand, verify_digits=False)
    _sleep_human()

    checkbox = page.locator(
        '[data-marker^="params[110000]/multiselect-button-checkbox"]',
        has_text=brand,
    ).first
    checkbox.wait_for(state="visible", timeout=5000)
    checkbox.scroll_into_view_if_needed()
    time.sleep(random.uniform(0.2, 0.5))
    checkbox.hover()
    time.sleep(random.uniform(0.1, 0.3))
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
    форму (медленно, по-человечески) и возвращает получившийся URL.
    Поднимает исключение, если после заполнения формы ссылка не
    изменилась, хотя фильтры были заданы — чтобы не сохранить и не
    скрапить по факту нефильтрованную выдачу."""
    start_url = BASE_URL.format(city=city)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        try:
            context = browser.new_context(locale="ru-RU")
            page = context.new_page()
            page.goto(start_url, wait_until="domcontentloaded")
            before_url = page.url

            _sleep_human()
            _human_scroll(page)

            if brand:
                _select_brand(page, brand)

            _fill_range(page, "price-from", "price-to", price_min, price_max)
            _fill_range(page, "params[164669]-from", "params[164669]-to", year_min, year_max)
            _fill_range(page, "params[2687]-from", "params[2687]-to", mileage_min, mileage_max)

            page.mouse.move(200, 200)
            time.sleep(random.uniform(0.3, 0.6))
            page.keyboard.press("Tab")
            try:
                page.wait_for_function(
                    "url => location.href !== url", arg=before_url, timeout=30000
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
