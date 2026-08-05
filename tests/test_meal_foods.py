"""Meal-specific recent/frequent food lookup tests."""

from datetime import date

import pytest

from mfp_mcp import server
from mfp_mcp.services import diary as diary_service
from mfp_mcp.services import food as food_service


def _history_fragment(source="recent"):
    return f"""
    <div id="{source}_page_1">
      <table><tr class="favorite">
        <td><input class="checkbox" data-food-id="303610008"
          data-food-verified="false" data-list-type="{source}" /></td>
        <td>Olio D'oliva (1 Cucchiaio)</td>
        <td>
          <input name="favorites[0][quantity]" value="0,6" />
          <select name="favorites[0][weight_id]">
            <option value="10" selected="selected">10 g</option>
            <option value="1">1 grammo</option>
            <option value="2">1 ounce</option>
          </select>
        </td>
      </tr></table>
    </div>
    """.encode()


class _Response:
    status_code = 200

    def __init__(self, content=b""):
        self.content = content


class _Session:
    def __init__(self):
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        source = "frequent" if url.endswith("load_most_used") else "recent"
        return _Response(_history_fragment(source))


class _Client:
    def __init__(self):
        self.session = _Session()


def test_parse_history_fragment_extracts_add_selection_metadata():
    result = server._parse_history_fragment(_history_fragment(), "recent")

    assert result == [
        {
            "history_id": "303610008",
            "name": "Olio D'oliva (1 Cucchiaio)",
            "source": "recent",
            "verified": False,
            "previous_quantity": 0.6,
            "previous_serving": "10 g",
            "available_servings": ["10 g", "1 grammo", "1 ounce"],
            "supports_grams": True,
            "supports_count": False,
        }
    ]


def test_get_meal_foods_loads_recent_and_frequent_for_meal():
    client = _Client()

    result = server.get_meal_foods(client, meal=1)

    assert result["recent"][0]["source"] == "recent"
    assert result["frequent"][0]["source"] == "frequent"
    assert [request[1]["data"]["meal"] for request in client.session.requests] == [1, 1]


def test_get_meal_foods_reuses_short_lived_cache():
    client = _Client()

    first = server.get_meal_foods(client, meal=2)
    second = server.get_meal_foods(client, meal=2)

    assert second == first
    assert len(client.session.requests) == 2

    server.invalidate_meal_food_cache(client)
    server.get_meal_foods(client, meal=2)
    assert len(client.session.requests) == 4


def test_assess_food_plausibility_allows_normal_olive_oil():
    food = {
        "nutritional_contents": {"energy": {"value": 884}},
        "serving_sizes": [
            {"value": 100, "unit": "g", "nutrition_multiplier": 1},
            {"value": 1, "unit": "g", "nutrition_multiplier": 0.01},
        ],
    }

    assessment = server.assess_food_plausibility(food)

    assert assessment["status"] == "plausible"
    assert assessment["calories_per_100_g_or_ml"] == 884


def test_resolve_meal_food_returns_checked_modern_id(monkeypatch):
    history = {
        "recent": server._parse_history_fragment(_history_fragment(), "recent"),
        "frequent": [],
    }
    food = {
        "id": "97065782668333",
        "description": "Olive oil",
        "brand_name": "Generic",
        "verified": True,
        "nutritional_contents": {"energy": {"value": 88.4}},
        "serving_sizes": [{"value": 10, "unit": "g", "nutrition_multiplier": 1}],
    }
    monkeypatch.setattr(food_service, "get_meal_foods", lambda *args, **kwargs: history)
    monkeypatch.setattr(
        food_service,
        "_resolve_history_id_from_search",
        lambda *args, **kwargs: "97065782668333",
    )
    monkeypatch.setattr(food_service, "get_food_v2", lambda *args: food)

    result = food_service.resolve_meal_food(object(), "303610008", 1)

    assert result["resolved"] is True
    assert result["mfp_id"] == "97065782668333"
    assert result["units"] == ["10 g"]
    assert "implausible" not in result


def test_add_food_refuses_physically_impossible_entry(monkeypatch):
    bad_food = {
        "id": "bad-oil",
        "description": "Olive oil",
        "nutritional_contents": {"energy": {"value": 800}},
        "serving_sizes": [{"value": 1, "unit": "g", "nutrition_multiplier": 1}],
    }
    monkeypatch.setattr(diary_service, "get_food_v2", lambda *args: bad_food)

    with pytest.raises(RuntimeError, match="Refusing to add"):
        diary_service.add_food_to_diary(
            object(), "bad-oil", "Lunch", date(2026, 7, 29), amount=10, unit="g"
        )
