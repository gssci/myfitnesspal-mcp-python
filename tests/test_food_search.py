"""Unit tests for parsing MyFitnessPal's server-rendered food search page."""

import asyncio
import json

from mfp_mcp import server
from mfp_mcp.tools import food as food_tools


def _search_page(query="chicken breast", items=None):
    payload = {
        "props": {
            "pageProps": {
                "dehydratedState": {
                    "queries": [
                        {
                            "queryKey": ["food", query, 1],
                            "state": {"data": {"items": items or []}},
                        }
                    ]
                }
            }
        }
    }
    return (
        "<html><body><script id='__NEXT_DATA__' type='application/json'>"
        + json.dumps(payload)
        + "</script></body></html>"
    ).encode()


class _Response:
    status_code = 200

    def __init__(self, content):
        self.content = content


class _Session:
    def __init__(self, content):
        self.content = content
        self.request = None

    def get(self, url, **kwargs):
        self.request = (url, kwargs)
        return _Response(self.content)


class _Client:
    def __init__(self, content):
        self.session = _Session(content)


def test_search_foods_web_parses_food_id_and_summary():
    items = [
        {
            "item": {
                "id": "88518932032557",
                "description": "Chicken. Breast",
                "brand_name": "Chicken breast",
                "serving_sizes": [
                    {"value": 4, "unit": "oz"},
                    {"value": 100, "unit": "grammi"},
                ],
                "nutritional_contents": {"energy": {"unit": "calories", "value": 110}},
            }
        }
    ]
    client = _Client(_search_page(items=items))

    results = server.search_foods_web(client, "chicken breast")

    # Lean on purpose: this list is read in full by the model and then carried
    # for the rest of the request. Flags appear only when they are true.
    assert results == [
        {
            "name": "Chicken. Breast",
            "brand": "Chicken breast",
            "units": ["4 oz", "100 grammi"],
            "calories": 110,
            "mfp_id": "88518932032557",
            "exact_name_match": True,
        }
    ]
    assert client.session.request[0].endswith("/chicken%20breast")


def test_search_foods_web_honors_limit():
    item = {
        "item": {
            "id": "1",
            "description": "Chicken breast",
            "serving_sizes": [],
            "nutritional_contents": {},
        }
    }
    results = server.search_foods_web(
        _Client(_search_page(items=[item, item])), "chicken breast", 1
    )
    assert len(results) == 1


def test_search_marks_food_without_gram_serving():
    item = {
        "item": {
            "id": "1",
            "description": "One apple",
            "serving_sizes": [{"value": 1, "unit": "piece"}],
            "nutritional_contents": {},
        }
    }
    result = server.search_foods_web(_Client(_search_page("apple", items=[item])), "apple")[0]
    assert result["whole_item_units"] == ["piece"]


def test_search_marks_named_single_item_as_countable():
    item = {
        "item": {
            "id": "1",
            "description": "Egg",
            "serving_sizes": [
                {"value": 1, "unit": "cup"},
                {"value": 1, "unit": "large"},
            ],
            "nutritional_contents": {},
        }
    }

    result = server.search_foods_web(_Client(_search_page("egg", items=[item])), "egg")[0]

    assert result["whole_item_units"] == ["large"]


def test_search_does_not_treat_parenthesized_weight_unit_as_countable():
    item = {
        "item": {
            "id": "1",
            "description": "Oats",
            "serving_sizes": [{"value": 1, "unit": "kg(s)"}],
            "nutritional_contents": {},
        }
    }

    result = server.search_foods_web(_Client(_search_page("oats", items=[item])), "oats")[0]

    assert "whole_item_units" not in result


def test_search_foods_web_allows_zero_results():
    results = server.search_foods_web(_Client(_search_page()), "chicken breast")
    assert results == []


def test_search_flags_impossible_calories_per_gram():
    item = {
        "item": {
            "id": "bad-oil",
            "description": "Olive oil",
            "serving_sizes": [{"value": 1, "unit": "gram", "nutrition_multiplier": 1}],
            "nutritional_contents": {"energy": {"unit": "calories", "value": 800}},
        }
    }

    result = server.search_foods_web(_Client(_search_page("olive oil", items=[item])), "olive oil")[
        0
    ]

    assert result["implausible"] is True


def test_food_details_uses_add_paths_v2_serving_metadata(monkeypatch):
    item = {
        "id": "kiwi",
        "description": "Kiwi Fruit",
        "verified": True,
        "serving_sizes": [
            {"value": 2, "unit": "kiwi", "nutrition_multiplier": 1},
            {"value": 148, "unit": "g", "nutrition_multiplier": 1},
        ],
        "nutritional_contents": {"energy": {"value": 100}},
    }
    monkeypatch.setattr(food_tools, "get_mfp_client", lambda: object())
    monkeypatch.setattr(food_tools, "get_food_v2", lambda *args: item)

    result = asyncio.run(
        food_tools.mfp_get_food_details(
            server.GetFoodDetailsInput(mfp_id="kiwi", response_format="json")
        )
    )
    payload = json.loads(result)

    assert payload["serving_options"] == [
        {"amount": 2, "unit": "kiwi"},
        {"amount": 148, "unit": "g"},
    ]
    assert payload["supports_count"] is True
    assert payload["supports_grams"] is True
