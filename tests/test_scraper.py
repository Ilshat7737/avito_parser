from unittest.mock import MagicMock

from scraper import _extract_mileage, _extract_year, is_blocked_or_captcha


def test_extract_mileage_from_specific_params():
    text = "42 313 км, 1.6 MT (106 л.с.), универсал, передний, бензин"
    assert _extract_mileage(text) == 42313


def test_extract_mileage_returns_none_when_no_digits():
    assert _extract_mileage("новый автомобиль, 1.6 MT") is None


def test_extract_year_from_title():
    title = "ВАЗ (LADA) Largus Cross 1.6 MT, 2019, 42 313 км"
    assert _extract_year(title) == 2019


def test_extract_year_returns_none_when_absent():
    assert _extract_year("Kia Rio без пробега в описании") is None


def test_is_blocked_or_captcha_false_when_items_present():
    page = MagicMock()
    page.query_selector.return_value = MagicMock()  # карточка найдена
    assert is_blocked_or_captcha(page) is False
    page.inner_text.assert_not_called()  # не тратим время на анализ текста, если карточки есть


def test_is_blocked_or_captcha_true_on_known_phrase():
    page = MagicMock()
    page.query_selector.return_value = None  # карточек нет
    page.inner_text.return_value = "Подтвердите, что вы не робот, введите капчу"
    assert is_blocked_or_captcha(page) is True


def test_is_blocked_or_captcha_false_on_ordinary_empty_page():
    page = MagicMock()
    page.query_selector.return_value = None
    page.inner_text.return_value = "По вашему запросу ничего не найдено"
    assert is_blocked_or_captcha(page) is False


def test_is_blocked_or_captcha_false_when_body_text_unavailable():
    page = MagicMock()
    page.query_selector.return_value = None
    page.inner_text.side_effect = Exception("no body")
    assert is_blocked_or_captcha(page) is False
