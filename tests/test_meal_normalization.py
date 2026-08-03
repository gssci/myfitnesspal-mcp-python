"""Meal arguments accept either spelling.

MyFitnessPal's history endpoints address meals by number and its diary
endpoints address them by name, so both live in the tool surface. A caller
holding the wrong one used to hit a dead end: ``mfp_add_food_to_diary`` with
``meal=2`` failed with "Invalid meal '2'" and no hint of the fix.
"""

import asyncio
import json
from datetime import date

import pytest
from pydantic import ValidationError

from mfp_mcp import server
from mfp_mcp.config import normalize_meal_name, normalize_meal_number
from mfp_mcp.services import diary as diary_service


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "Breakfast"),
        (2, "Dinner"),
        ("2", "Dinner"),
        ("dinner", "Dinner"),
        ("  DINNER ", "Dinner"),
        ("Snack", "Snacks"),
        ("snacks", "Snacks"),
    ],
)
def test_normalize_meal_name(value, expected):
    assert normalize_meal_name(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Breakfast", 0),
        ("dinner", 2),
        ("Snack", 3),
        ("2", 2),
        (2, 2),
    ],
)
def test_normalize_meal_number(value, expected):
    assert normalize_meal_number(value) == expected


@pytest.mark.parametrize("normalize", [normalize_meal_name, normalize_meal_number])
def test_unknown_meals_pass_through_untouched(normalize):
    """Callers keep raising their own errors on real nonsense."""
    assert normalize("brunch") == "brunch"
    assert normalize(None) is None


def test_number_form_accepts_a_meal_name():
    assert server.GetMealFoodsInput(meal="Dinner").meal == 2
    assert server.ResolveMealFoodInput(history_id="1", meal="snack").meal == 3


def test_name_form_accepts_a_meal_number():
    entry = server.AddFoodToDiaryInput(mfp_id="1", meal=2, amount=100, unit="g")

    assert entry.meal == "Dinner"
    assert server.RemoveFoodFromDiaryInput(name_contains="rice", meal=0).meal == "Breakfast"


def test_out_of_range_meal_numbers_are_still_rejected():
    with pytest.raises(ValidationError):
        server.GetMealFoodsInput(meal=7)


def test_unknown_meal_names_are_still_rejected():
    with pytest.raises(ValidationError):
        server.GetMealFoodsInput(meal="brunch")


class _Response:
    status_code = 201

    def json(self):
        return {"items": [{"id": "entry-id"}]}


class _Session:
    def __init__(self):
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _Response()


class _Client:
    access_token = "token"
    user_id = "user"

    def __init__(self):
        self.session = _Session()


@pytest.fixture
def oats():
    return {
        "id": "oats",
        "version": "1",
        "description": "Oats",
        "nutritional_contents": {"energy": {"value": 380}},
        "serving_sizes": [{"value": 100, "unit": "g", "nutrition_multiplier": 1}],
    }


def test_add_food_service_accepts_a_meal_number(monkeypatch, oats):
    """The service is the last line of defence for direct Python callers."""
    client = _Client()
    monkeypatch.setattr(diary_service, "get_food_v2", lambda *args: oats)

    diary_service.add_food_to_diary(
        client, "oats", 2, date(2026, 8, 3), amount=50, unit="g"
    )

    payload = json.loads(client.session.posts[0][1]["data"])
    assert payload["items"][0]["meal_name"] == "Dinner"


def test_add_food_service_still_rejects_a_meal_that_is_not_a_meal(monkeypatch, oats):
    monkeypatch.setattr(diary_service, "get_food_v2", lambda *args: oats)

    with pytest.raises(RuntimeError, match="Invalid meal"):
        diary_service.add_food_to_diary(
            _Client(), "oats", "brunch", date(2026, 8, 3), amount=50, unit="g"
        )


def test_meal_number_reaches_the_diary_tool_as_a_name(monkeypatch):
    """The exact call that failed in the logs: meal=2 on a name-taking tool."""
    from mfp_mcp.tools import diary as diary_tools

    captured = {}

    monkeypatch.setattr(diary_tools, "get_mfp_client", lambda: object())
    monkeypatch.setattr(
        diary_tools,
        "add_food_to_diary",
        lambda **kwargs: captured.update(kwargs)
        or {"requested_amount": 100.0, "requested_unit": "g", "food_name": "Sgombro"},
    )

    asyncio.run(
        server.mcp._tool_manager.call_tool(
            "mfp_add_food_to_diary",
            {"mfp_id": "58995187319997", "meal": 2, "amount": 100, "unit": "g"},
        )
    )

    assert captured["meal"] == "Dinner"
