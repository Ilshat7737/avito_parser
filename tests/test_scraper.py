from scraper import _extract_mileage, _extract_year


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
