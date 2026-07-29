"""Unit tests for parsing MyFitnessPal's server-rendered food search page."""

import json

from mfp_mcp import server


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

    assert results == [
        {
            "name": "Chicken. Breast",
            "brand": "Chicken breast",
            "serving": "4 oz",
            "available_servings": ["4 oz", "100 grammi"],
            "supports_grams": True,
            "calories": 110,
            "mfp_id": "88518932032557",
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
    assert result["supports_grams"] is False


def test_search_foods_web_allows_zero_results():
    results = server.search_foods_web(_Client(_search_page()), "chicken breast")
    assert results == []
