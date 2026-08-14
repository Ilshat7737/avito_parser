from unittest.mock import MagicMock

import agent
from agent import EvaluationResult, ListingVerdict


def test_evaluate_listings_returns_empty_dict_for_empty_items():
    assert agent.evaluate_listings([], "любой текст") == {}


def test_evaluate_listings_parses_structured_response(monkeypatch):
    fake_response = MagicMock()
    fake_response.candidates = []
    fake_response.parsed = EvaluationResult(
        results=[ListingVerdict(id="111", relevant=True, reason="Подходит по цене и марке")]
    )
    monkeypatch.setattr(agent.client.models, "generate_content", lambda **kwargs: fake_response)

    items = [{
        "id": "111", "title": "Kia Rio", "price": 700000,
        "year": 2019, "mileage": 42313, "description": "Один хозяин",
    }]
    result = agent.evaluate_listings(items, "недорогая надёжная машина")

    assert result == {"111": {"relevant": True, "reason": "Подходит по цене и марке"}}


def test_evaluate_listings_returns_empty_dict_on_api_error(monkeypatch):
    def raise_error(**kwargs):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(agent.client.models, "generate_content", raise_error)

    items = [{
        "id": "111", "title": "Kia Rio", "price": 700000,
        "year": 2019, "mileage": 42313, "description": "",
    }]
    assert agent.evaluate_listings(items, "текст") == {}


def test_evaluate_listings_returns_empty_dict_when_parsed_is_none(monkeypatch):
    fake_response = MagicMock()
    fake_response.candidates = []
    fake_response.parsed = None
    monkeypatch.setattr(agent.client.models, "generate_content", lambda **kwargs: fake_response)

    items = [{
        "id": "111", "title": "Kia Rio", "price": 700000,
        "year": 2019, "mileage": 42313, "description": "",
    }]
    assert agent.evaluate_listings(items, "текст") == {}


def test_evaluate_listings_splits_large_batches_into_multiple_calls(monkeypatch):
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)
    call_count = {"n": 0}

    def fake_generate(**kwargs):
        call_count["n"] += 1
        prompt = kwargs["contents"]
        ids_in_batch = [
            line.split("=", 1)[1] for line in prompt.splitlines() if line.startswith("id=")
        ]
        fake_response = MagicMock()
        fake_response.candidates = []
        fake_response.parsed = EvaluationResult(
            results=[ListingVerdict(id=i, relevant=True, reason="ok") for i in ids_in_batch]
        )
        return fake_response

    monkeypatch.setattr(agent.client.models, "generate_content", fake_generate)

    items = [
        {"id": str(i), "title": f"Item {i}", "price": 100000, "year": 2020, "mileage": 10000, "description": ""}
        for i in range(20)
    ]
    result = agent.evaluate_listings(items, "текст")

    assert call_count["n"] == 2  # 20 items / BATCH_SIZE=15 -> 2 батча
    assert len(result) == 20
    assert all(v["relevant"] for v in result.values())
