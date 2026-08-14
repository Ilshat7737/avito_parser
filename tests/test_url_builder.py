from url_builder import _filters_appear_unapplied


def test_filters_appear_unapplied_when_no_f_param_and_filters_given():
    result = "https://www.avito.ru/kazan/avtomobili?radius=200&searchRadius=200"
    assert _filters_appear_unapplied(
        result, price_min=700000, price_max=1000000,
        mileage_min=None, mileage_max=None, year_min=None, year_max=None,
    ) is True


def test_filters_appear_applied_when_f_param_present():
    result = "https://www.avito.ru/kazan/avtomobili?f=ASgCAgECAUXGmgwc&radius=200"
    assert _filters_appear_unapplied(
        result, price_min=700000, price_max=1000000,
        mileage_min=None, mileage_max=None, year_min=None, year_max=None,
    ) is False


def test_missing_f_param_is_fine_when_no_range_filters_given():
    result = "https://www.avito.ru/kazan/avtomobili?radius=200"
    assert _filters_appear_unapplied(
        result, price_min=None, price_max=None,
        mileage_min=None, mileage_max=None, year_min=None, year_max=None,
    ) is False
