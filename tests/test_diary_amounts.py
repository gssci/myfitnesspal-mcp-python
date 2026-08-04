"""Regression tests for converting user amounts to MFP serving counts."""

import asyncio
import json
from datetime import date

import pytest

from mfp_mcp import server, units
from mfp_mcp.services import diary as diary_service
from mfp_mcp.tools import diary as diary_tools


@pytest.fixture
def ice_cream():
    return {
        "id": "27769042718141",
        "description": "gelato alla vaniglia",
        "serving_sizes": [
            {
                "value": 60.0,
                "unit": "g",
                "nutrition_multiplier": 0.6,
                "gram_weight": 60.0,
            }
        ],
    }


def test_250_grams_against_60_gram_serving(ice_cream):
    serving, count = server.resolve_food_amount(ice_cream, 250, "g")
    assert serving == {"value": 60.0, "unit": "g", "nutrition_multiplier": 0.6}
    assert count == pytest.approx(250 / 60)


def test_explicit_gram_value_wins_over_unreliable_gram_weight(ice_cream):
    ice_cream["serving_sizes"][0]["gram_weight"] = 1.0
    _, count = server.resolve_food_amount(ice_cream, 250, "g")
    assert count == pytest.approx(250 / 60)


def test_italian_grams_alias(ice_cream):
    _, count = server.resolve_food_amount(ice_cream, 250, "grammi")
    assert count == pytest.approx(250 / 60)


@pytest.mark.parametrize("alias", ["gr", "gr.", "g.", "grams", "grammes"])
def test_other_gram_aliases(ice_cream, alias):
    _, count = server.resolve_food_amount(ice_cream, 250, alias)
    assert count == pytest.approx(250 / 60)


def test_serving_count_is_explicit(ice_cream):
    _, count = server.resolve_food_amount(ice_cream, 2.5, "serving")
    assert count == 2.5


def test_unknown_unit_fails_instead_of_falling_back(ice_cream):
    with pytest.raises(RuntimeError, match="not available"):
        server.resolve_food_amount(ice_cream, 250, "furlong")


def test_named_serving_uses_requested_physical_count():
    food = {
        "id": "1",
        "serving_sizes": [{"value": 0.5, "unit": "cup", "nutrition_multiplier": 1.0}],
    }
    _, count = server.resolve_food_amount(food, 2, "cup")
    assert count == 4


def test_unreliable_one_gram_portion_metadata_fails_closed():
    oats = {
        "id": "oats",
        "serving_sizes": [
            {
                "value": 1,
                "unit": "portion",
                "nutrition_multiplier": 1,
                "gram_weight": 1,
            }
        ],
    }

    with pytest.raises(RuntimeError, match="not available"):
        server.resolve_food_amount(oats, 50, "g")


def test_count_selects_a_foods_discrete_unit():
    kiwi = {
        "id": "kiwi",
        "serving_sizes": [
            {
                "value": 1,
                "unit": "fruit",
                "nutrition_multiplier": 1,
                "gram_weight": 75,
            }
        ],
    }

    serving, count = server.resolve_food_amount(kiwi, 2, "count")

    assert serving["unit"] == "fruit"
    assert count == 2


def test_count_converts_against_multi_item_database_serving():
    kiwi = {
        "id": "kiwi",
        "serving_sizes": [
            {"value": 2, "unit": "kiwi", "nutrition_multiplier": 1}
        ],
    }

    serving, count = server.resolve_food_amount(kiwi, 2, "count")

    assert serving == {"value": 2, "unit": "kiwi", "nutrition_multiplier": 1}
    assert count == 1


def test_count_skips_physical_measures_and_selects_named_item():
    food = {
        "id": "egg",
        "serving_sizes": [
            {"value": 1, "unit": "cup", "nutrition_multiplier": 2},
            {"value": 1, "unit": "large", "nutrition_multiplier": 1},
        ],
    }

    serving, count = server.resolve_food_amount(food, 2, "count")

    assert serving["unit"] == "large"
    assert count == 2


@pytest.mark.parametrize("unit", ["fruit", "piece", "each", "unità", "pezzi"])
def test_count_aliases_select_a_discrete_unit(unit):
    food = {
        "id": "item",
        "serving_sizes": [
            {"value": 1, "unit": "piece", "nutrition_multiplier": 1}
        ],
    }

    _, count = server.resolve_food_amount(food, 2, unit)

    assert count == 2


def test_large_explicit_serving_count_is_rejected(ice_cream):
    with pytest.raises(RuntimeError, match="Refusing 50 database servings"):
        server.resolve_food_amount(ice_cream, 50, "serving")


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


def test_50_grams_of_oats_posts_half_of_a_100g_serving(monkeypatch):
    oats = {
        "id": "oats",
        "version": "1",
        "description": "Oat flakes",
        "nutritional_contents": {"energy": {"value": 456}},
        "serving_sizes": [
            {"value": 1, "unit": "portion", "nutrition_multiplier": 1},
            {"value": 100, "unit": "g", "nutrition_multiplier": 1},
        ],
    }
    client = _Client()
    monkeypatch.setattr(diary_service, "get_food_v2", lambda *args: oats)

    result = diary_service.add_food_to_diary(
        client, "oats", "Breakfast", date(2026, 7, 30), amount=50, unit="g"
    )

    payload = json.loads(client.session.posts[0][1]["data"])
    entry = payload["items"][0]
    assert entry["serving_size"]["unit"] == "g"
    assert entry["servings"] == pytest.approx(0.5)
    assert result["requested_amount"] == 50
    assert result["requested_unit"] == "g"
    assert result["estimated_calories"] == 228


def test_two_kiwis_post_as_two_fruit_units(monkeypatch):
    kiwi = {
        "id": "kiwi",
        "version": "1",
        "description": "Kiwi fruit",
        "nutritional_contents": {"energy": {"value": 46}},
        "serving_sizes": [
            {
                "value": 1,
                "unit": "fruit",
                "nutrition_multiplier": 1,
                "gram_weight": 75,
            }
        ],
    }
    client = _Client()
    monkeypatch.setattr(diary_service, "get_food_v2", lambda *args: kiwi)

    result = diary_service.add_food_to_diary(
        client, "kiwi", "Snacks", date(2026, 7, 30), amount=2, unit="count"
    )

    payload = json.loads(client.session.posts[0][1]["data"])
    entry = payload["items"][0]
    assert entry["serving_size"]["unit"] == "fruit"
    assert entry["servings"] == 2
    assert result["estimated_calories"] == 92


def test_entry_over_calorie_safety_limit_is_not_posted(monkeypatch):
    oats = {
        "id": "oats",
        "description": "Oat flakes",
        "nutritional_contents": {"energy": {"value": 456}},
        "serving_sizes": [
            {"value": 1, "unit": "portion", "nutrition_multiplier": 1}
        ],
    }
    client = _Client()
    monkeypatch.setattr(diary_service, "get_food_v2", lambda *args: oats)

    with pytest.raises(RuntimeError, match="database servings"):
        diary_service.add_food_to_diary(
            client,
            "oats",
            "Breakfast",
            date(2026, 7, 30),
            amount=50,
            unit="serving",
        )

    assert client.session.posts == []


def test_add_tool_returns_structured_failure(monkeypatch):
    monkeypatch.setattr(diary_tools, "get_mfp_client", lambda: object())

    def fail(**kwargs):
        raise RuntimeError("Unit 'g' is not available")

    monkeypatch.setattr(diary_tools, "add_food_to_diary", fail)

    result = asyncio.run(
        diary_tools.mfp_add_food_to_diary(
            server.AddFoodToDiaryInput(
                mfp_id="food-id",
                meal="Breakfast",
                amount=50,
                unit="g",
            )
        )
    )

    assert json.loads(result) == {
        "success": False,
        "error": "Unit 'g' is not available",
    }


@pytest.fixture
def kiwi():
    """A real MFP record: a countable serving plus generic weight servings."""
    return {
        "id": "kiwi",
        "version": "1",
        "description": "Kiwi",
        "nutritional_contents": {"energy": {"value": 51}},
        "serving_sizes": [
            {"value": 1, "unit": "fruit", "nutrition_multiplier": 1, "gram_weight": 75},
            {"value": 1, "unit": "g", "nutrition_multiplier": 0.0133},
            {"value": 1, "unit": "kg", "nutrition_multiplier": 13.3},
            {"value": 1, "unit": "mg", "nutrition_multiplier": 0.0000133},
            {"value": 1, "unit": "lb", "nutrition_multiplier": 6.04},
        ],
    }


def test_generic_weight_servings_are_not_countable_units(kiwi):
    # "1 mg" and "1 lb" read as discrete items would both mislabel the food and
    # let unit="count" resolve to milligrams.
    assert server.serving_capabilities(kiwi["serving_sizes"])["count_units"] == ["fruit"]


def test_count_resolves_to_the_item_serving_not_a_weight(kiwi):
    serving, servings = server.resolve_food_amount(kiwi, 2, "count")

    assert serving["unit"] == "fruit"
    assert servings == 2


@pytest.mark.parametrize("unit", ["count", "fruit", "fruits", "whole", "each"])
def test_every_way_of_saying_one_whole_kiwi_resolves_to_the_fruit_serving(kiwi, unit):
    # "1 fruit" is what this food calls one kiwi, so asking for a count and
    # naming the serving have to land on the same entry.
    serving, servings = server.resolve_food_amount(kiwi, 1, unit)

    assert serving["unit"] == "fruit"
    assert servings == 1


@pytest.mark.parametrize("unit", ["count", "slice", "slices"])
def test_a_named_serving_matches_in_the_plural_too(unit):
    # "2 slices" against a serving spelled "1 slice" is the same request, and
    # used to fail as an unavailable unit.
    bread = {
        "serving_sizes": [
            {"value": 1, "unit": "slice", "nutrition_multiplier": 1, "gram_weight": 30},
            {"value": 1, "unit": "g", "nutrition_multiplier": 0.033},
        ]
    }

    serving, servings = server.resolve_food_amount(bread, 2, unit)

    assert serving["unit"] == "slice"
    assert servings == 2


def test_plural_folding_leaves_short_unit_names_alone():
    assert units.units_match("g", "g")
    assert units.units_match("oz", "oz")
    assert not units.units_match("g", "oz")
    assert not units.units_match("cup", "scoop")


def test_two_kiwis_sent_as_grams_is_refused_with_the_fix(monkeypatch, kiwi):
    client = _Client()
    monkeypatch.setattr(diary_service, "get_food_v2", lambda *args: kiwi)

    with pytest.raises(RuntimeError) as excinfo:
        diary_service.add_food_to_diary(
            client, "kiwi", "Snacks", date(2026, 8, 4), amount=2, unit="g"
        )

    message = str(excinfo.value)
    assert "not a real portion" in message
    assert 'unit="count"' in message
    assert "fruit" in message
    # Nothing may reach MyFitnessPal: silently logging 1 kcal is the bug.
    assert client.session.posts == []


def test_a_real_gram_amount_of_a_countable_food_still_logs(monkeypatch, kiwi):
    client = _Client()
    monkeypatch.setattr(diary_service, "get_food_v2", lambda *args: kiwi)

    result = diary_service.add_food_to_diary(
        client, "kiwi", "Snacks", date(2026, 8, 4), amount=150, unit="g"
    )

    assert result["requested_amount"] == 150
    assert len(client.session.posts) == 1


def test_a_tiny_weight_of_an_uncountable_food_still_logs(monkeypatch):
    # 2 g of salt is a legitimate entry: no item serving means no count to
    # confuse it with, so the guard must not fire.
    salt = {
        "id": "salt",
        "version": "1",
        "description": "Salt",
        "nutritional_contents": {"energy": {"value": 0}},
        "serving_sizes": [{"value": 1, "unit": "g", "nutrition_multiplier": 1}],
    }
    client = _Client()
    monkeypatch.setattr(diary_service, "get_food_v2", lambda *args: salt)

    diary_service.add_food_to_diary(
        client, "salt", "Dinner", date(2026, 8, 4), amount=2, unit="g"
    )

    assert len(client.session.posts) == 1


def test_count_sent_as_weight_ignores_non_weight_units(kiwi):
    assert diary_service.count_sent_as_weight(kiwi, 2, "count", 1.0) is None
    assert diary_service.count_sent_as_weight(kiwi, 2, "serving", 1.0) is None


def test_entry_nutrition_scales_macros_and_skips_missing_ones():
    food = {
        "nutritional_contents": {
            "energy": {"value": 456, "unit": "calories"},
            "protein": 16.9,
            "carbohydrates": 66.3,
            # No "fat" key: reporting 0 g of fat would be a made-up number.
        }
    }
    serving = {"nutrition_multiplier": 1}

    assert diary_service.entry_nutrition(food, serving, 0.5) == {
        "calories": 228.0,
        "protein": 8.4,
        "carbohydrates": 33.1,
    }


def test_entry_nutrition_survives_a_record_with_no_usable_numbers():
    assert diary_service.entry_nutrition({}, {"nutrition_multiplier": None}, 1) == {}
    assert diary_service.entry_nutrition({}, {"nutrition_multiplier": 1}, 1) == {}


def test_add_food_reports_the_entry_macros_it_logged(monkeypatch):
    oats = {
        "id": "oats",
        "version": "1",
        "description": "Oat flakes",
        "nutritional_contents": {
            "energy": {"value": 456},
            "protein": 16.9,
            "carbohydrates": 66.3,
            "fat": 8.2,
        },
        "serving_sizes": [{"value": 100, "unit": "g", "nutrition_multiplier": 1}],
    }
    client = _Client()
    monkeypatch.setattr(diary_service, "get_food_v2", lambda *args: oats)

    result = diary_service.add_food_to_diary(
        client, "oats", "Breakfast", date(2026, 8, 4), amount=50, unit="g"
    )

    assert result["nutrition"] == {
        "calories": 228.0,
        "protein": 8.4,
        "carbohydrates": 33.1,
        "fat": 4.1,
    }


class _Meal:
    def __init__(self, name, totals):
        self.name = name
        self.totals = totals


class _Day:
    def __init__(self, meals):
        self.meals = meals
        self.totals = {
            "calories": 1487.4,
            "carbohydrates": 150.44,
            "fat": 48.9,
            "protein": 96.2,
            "sodium": 2100,
            "sugar": 61,
        }


class _DiaryClient:
    def __init__(self, meals):
        self.day = _Day(meals)
        self.fetches = 0

    def get_date(self, target_date):
        assert target_date == date(2026, 8, 4)
        self.fetches += 1
        return self.day


def _english_day():
    return _DiaryClient(
        [
            _Meal("breakfast", {"calories": 300.0, "protein": 12.0}),
            _Meal("lunch", {"calories": 600.0, "protein": 40.0}),
            _Meal("dinner", {"calories": 500.0, "protein": 39.0}),
            _Meal("snacks", {"calories": 87.4, "protein": 5.2}),
        ]
    )


def test_get_diary_totals_keeps_only_the_reported_macros():
    totals = server.get_diary_totals(_english_day(), date(2026, 8, 4), "Snacks")

    # Ordered for reading, and trimmed to what a confirmation actually shows.
    assert list(totals["day"]) == ["calories", "protein", "carbohydrates", "fat"]
    assert totals["day"] == {
        "calories": 1487.4,
        "protein": 96.2,
        "carbohydrates": 150.4,
        "fat": 48.9,
    }
    assert totals["meal"] == {"calories": 87.4, "protein": 5.2}


def test_get_diary_totals_reads_both_scopes_from_one_fetch():
    client = _english_day()

    server.get_diary_totals(client, date(2026, 8, 4), "Lunch")

    assert client.fetches == 1


def test_get_diary_totals_accepts_a_meal_number():
    totals = server.get_diary_totals(_english_day(), date(2026, 8, 4), 1)

    assert totals["meal"] == {"calories": 600.0, "protein": 40.0}


def test_get_diary_totals_falls_back_to_meal_position_when_names_differ():
    # MyFitnessPal renders meal headings in the account's own language, so an
    # Italian diary must still resolve "Snacks" to the fourth meal.
    client = _DiaryClient(
        [
            _Meal("colazione", {"calories": 300.0}),
            _Meal("pranzo", {"calories": 600.0}),
            _Meal("cena", {"calories": 500.0}),
            _Meal("spuntini", {"calories": 87.4}),
        ]
    )

    totals = server.get_diary_totals(client, date(2026, 8, 4), "Snacks")

    assert totals["meal"] == {"calories": 87.4}


def test_get_diary_totals_reports_no_meal_when_the_diary_has_none():
    client = _DiaryClient([])

    totals = server.get_diary_totals(client, date(2026, 8, 4), "Snacks")

    assert totals["meal"] is None
    assert totals["day"]["calories"] == 1487.4


def test_add_tool_returns_both_totals_so_no_second_diary_read_is_needed(monkeypatch):
    monkeypatch.setattr(diary_tools, "get_mfp_client", lambda: object())
    monkeypatch.setattr(
        diary_tools,
        "add_food_to_diary",
        lambda **kwargs: {
            "entry_id": "entry-id",
            "food_name": "Oat flakes",
            "requested_amount": 50.0,
            "requested_unit": "g",
            "nutrition": {"calories": 228.0},
        },
    )
    asked_for = {}

    def fake_totals(client, target_date, meal):
        asked_for["meal"] = meal
        return {"meal": {"calories": 528.0}, "day": {"calories": 1487.4}}

    monkeypatch.setattr(diary_tools, "get_diary_totals", fake_totals)

    payload = json.loads(
        asyncio.run(
            diary_tools.mfp_add_food_to_diary(
                server.AddFoodToDiaryInput(
                    mfp_id="food-id", meal="Breakfast", amount=50, unit="g"
                )
            )
        )
    )

    assert payload["success"] is True
    assert payload["nutrition"] == {"calories": 228.0}
    assert payload["meal_totals"] == {"calories": 528.0}
    assert payload["day_totals"] == {"calories": 1487.4}
    assert asked_for["meal"] == "Breakfast"


def test_add_tool_still_reports_success_when_the_totals_read_fails(monkeypatch):
    monkeypatch.setattr(diary_tools, "get_mfp_client", lambda: object())
    monkeypatch.setattr(
        diary_tools,
        "add_food_to_diary",
        lambda **kwargs: {
            "food_name": "Oat flakes",
            "requested_amount": 50.0,
            "requested_unit": "g",
        },
    )

    def boom(client, target_date, meal):
        raise RuntimeError("diary page unavailable")

    monkeypatch.setattr(diary_tools, "get_diary_totals", boom)

    payload = json.loads(
        asyncio.run(
            diary_tools.mfp_add_food_to_diary(
                server.AddFoodToDiaryInput(
                    mfp_id="food-id", meal="Breakfast", amount=50, unit="g"
                )
            )
        )
    )

    # The food is already logged, so a failed totals read must not report a
    # failed write.
    assert payload["success"] is True
    assert payload["meal_totals"] is None
    assert payload["day_totals"] is None
    assert payload["day_totals_error"] == "diary page unavailable"
