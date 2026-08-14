from unittest.mock import MagicMock

import agent


def test_evaluate_listings_returns_empty_dict_for_empty_items():
    assert agent.evaluate_listings([], "любой текст") == {}


def test_evaluate_listings_parses_tool_use_response(monkeypatch):
    fake_block = MagicMock()
    fake_block.type = "tool_use"
    fake_block.name = "submit_relevance"
    fake_block.input = {
        "results": [
            {"id": "111", "relevant": True, "reason": "Подходит по цене и марке"},
        ]
    }
    fake_response = MagicMock()
    fake_response.content = [fake_block]
    monkeypatch.setattr(agent.client.messages, "create", lambda **kwargs: fake_response)

    items = [{
        "id": "111", "title": "Kia Rio", "price": 700000,
        "year": 2019, "mileage": 42313, "description": "Один хозяин",
    }]
    result = agent.evaluate_listings(items, "недорогая надёжная машина")

    assert result == {"111": {"relevant": True, "reason": "Подходит по цене и марке"}}


def test_evaluate_listings_returns_empty_dict_on_api_error(monkeypatch):
    def raise_error(**kwargs):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(agent.client.messages, "create", raise_error)

    items = [{
        "id": "111", "title": "Kia Rio", "price": 700000,
        "year": 2019, "mileage": 42313, "description": "",
    }]
    assert agent.evaluate_listings(items, "текст") == {}


def test_evaluate_listings_skips_malformed_result_entries(monkeypatch):
    fake_block = MagicMock()
    fake_block.type = "tool_use"
    fake_block.name = "submit_relevance"
    fake_block.input = {
        "results": [
            {"id": "111", "relevant": True, "reason": "Подходит"},
            {"id": "222", "relevant": False},  # отсутствует "reason" — не должно падать
        ]
    }
    fake_response = MagicMock()
    fake_response.content = [fake_block]
    monkeypatch.setattr(agent.client.messages, "create", lambda **kwargs: fake_response)

    items = [
        {"id": "111", "title": "Kia Rio", "price": 700000, "year": 2019, "mileage": 42313, "description": ""},
        {"id": "222", "title": "Lada Vesta", "price": 600000, "year": 2020, "mileage": 30000, "description": ""},
    ]
    result = agent.evaluate_listings(items, "текст")

    assert result == {"111": {"relevant": True, "reason": "Подходит"}}
