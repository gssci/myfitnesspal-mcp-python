"""One-call logging: the candidate choice the model used to make in context.

These pin the decisions that moved out of the prompt and into Python — prefer
this meal's own history, never offer a food that cannot take the requested
unit, and say why when nothing can.
"""

import asyncio
import json
from datetime import date

import pytest

from mfp_mcp import server
from mfp_mcp.services import quick_log
from mfp_mcp.tools import diary as diary_tools


def _food(food_id, description, servings, calories=100, brand=None):
    return {
        "id": food_id,
        "description": description,
        "brand_name": brand,
        "version": "1",
        "serving_sizes": [
            {"value": value, "unit": unit, "nutrition_multiplier": multiplier}
            for value, unit, multiplier in servings
        ],
        "nutritional_contents": {"energy": {"value": calories}, "protein": 10},
    }


def _history_row(history_id, name):
    return {
        "history_id": history_id,
        "name": name,
        "source": "recent",
        "verified": False,
        "previous_quantity": 1.0,
        "previous_serving": "100 g",
        "available_servings": ["100 g"],
        "supports_grams": True,
        "supports_count": False,
    }


@pytest.fixture
def wiring(monkeypatch):
    """Stub every network edge; each test fills in only what it needs."""
    state = {
        "history": {"recent": [], "frequent": []},
        "resolved": {},
        "records": {},
        "search": [],
        "searched": [],
        "added": [],
    }

    monkeypatch.setattr(quick_log, "get_meal_foods", lambda client, meal: state["history"])
    monkeypatch.setattr(
        quick_log,
        "_resolve_history_id_from_search",
        lambda client, history_id, name, meal: state["resolved"].get(history_id),
    )
    monkeypatch.setattr(
        quick_log,
        "get_foods_v2",
        lambda client, ids: {i: state["records"][i] for i in ids if i in state["records"]},
    )

    def fake_search(client, query, limit):
        state["searched"].append(query)
        return state["search"][:limit]

    monkeypatch.setattr(quick_log, "search_food_records", fake_search)

    def fake_add(*, client, mfp_id, meal, target_date, amount, unit):
        state["added"].append((mfp_id, meal, amount, unit))
        return {
            "entry_id": "entry-1",
            "food_name": "logged food",
            "requested_amount": float(amount),
            "requested_unit": unit,
            "nutrition": {"calories": 100.0},
        }

    monkeypatch.setattr(quick_log, "add_food_to_diary", fake_add)
    monkeypatch.setattr(
        quick_log,
        "get_diary_totals",
        lambda client, target_date, meal: {"meal": {"calories": 100.0}, "day": {"calories": 900.0}},
    )
    return state


def _log(**kwargs):
    defaults = {
        "food": "yogurt greco",
        "amount": 250,
        "unit": "g",
        "meal": "Snacks",
        "target_date": date(2026, 8, 5),
    }
    return quick_log.log_food(object(), **{**defaults, **kwargs})


def test_a_history_match_wins_and_skips_the_search(wiring):
    wiring["history"]["recent"] = [_history_row("h1", "Milbona - yogurt greco zero")]
    wiring["resolved"]["h1"] = "111"
    wiring["records"]["111"] = _food("111", "yogurt greco zero", [(100, "g", 1)])

    result = _log()

    assert result["success"] is True
    assert result["matched_from"] == "history"
    assert wiring["added"] == [("111", "Snacks", 250, "g")]
    # A food the user already logs into this meal beats the global database,
    # and not searching saves a request as well as the ranking.
    assert wiring["searched"] == []


def test_a_partial_history_match_does_not_hijack_the_search(wiring):
    """"pollo" appearing in "brodo di pollo" is not a match for "petto di pollo"."""
    wiring["history"]["frequent"] = [_history_row("h1", "brodo di pollo")]
    wiring["search"] = [_food("222", "Petto di pollo", [(100, "g", 1)])]

    result = _log(food="petto di pollo")

    assert result["matched_from"] == "search"
    assert wiring["added"] == [("222", "Snacks", 250, "g")]


def test_a_candidate_that_cannot_take_the_unit_is_skipped(wiring):
    """Identity first, then units: the kiwi sold by the fruit is the one to use."""
    wiring["search"] = [
        _food("no-count", "Kiwi", [(100, "g", 1)]),
        _food("countable", "Kiwi", [(1, "fruit", 1)]),
    ]

    result = _log(food="kiwi", amount=1, unit="fruit")

    assert result["success"] is True
    assert wiring["added"] == [("countable", "Snacks", 1, "fruit")]


def test_a_whole_item_unit_falls_back_to_the_generic_count(wiring):
    """The caller names a unit before it has seen the food's serving table.

    It can only repeat the user's word, so "slice" has to still log a food that
    spells that serving "fetta".
    """
    wiring["search"] = [_food("cheese", "Sottilette light", [(1, "fetta", 1)])]

    result = _log(food="sottilette", amount=3, unit="slice")

    assert result["success"] is True
    assert wiring["added"] == [("cheese", "Snacks", 3, "count")]


def test_a_weight_never_falls_back_to_a_count(wiring):
    """Grams silently becoming items would log a wildly wrong amount."""
    wiring["search"] = [_food("cheese", "Sottilette light", [(1, "fetta", 1)])]

    assert _log(food="sottilette", amount=30, unit="g")["success"] is False
    assert wiring["added"] == []


def test_an_implausible_record_is_never_chosen(wiring):
    wiring["search"] = [
        _food("bad", "Olio di oliva", [(1, "g", 1)], calories=800),
        _food("good", "Olio di oliva", [(100, "g", 1)], calories=884),
    ]

    assert _log(food="olio di oliva", amount=10, unit="g")["success"] is True
    assert wiring["added"] == [("good", "Snacks", 10, "g")]


def test_nothing_usable_reports_what_was_turned_down_and_why(wiring):
    wiring["search"] = [_food("only", "Sottilette light", [(1, "fetta", 1)])]

    result = _log(food="sottilette", amount=30, unit="g")

    assert result["success"] is False
    assert wiring["added"] == []
    considered = result["considered"]
    assert considered[0]["name"] == "Sottilette light"
    assert considered[0]["units"] == ["1 fetta"]
    # The reason is what lets the model fix the unit rather than retry blindly.
    assert "fetta" in considered[0]["why_not"]


def test_an_empty_database_says_so_rather_than_blaming_the_unit(wiring):
    result = _log(food="nonesuch")

    assert result["success"] is False
    assert "Nothing in the database matched" in result["error"]
    assert result["considered"] == []


def test_runner_up_matches_are_named_but_not_listed_in_full(wiring):
    wiring["search"] = [
        _food("a", "Fette biscottate integrali", [(100, "g", 1)]),
        _food("b", "Fette biscottate integrali", [(100, "g", 1)], brand="Mulino"),
    ]

    result = _log(food="fette biscottate integrali", amount=35, unit="g")

    assert result["other_matches"] == ["Fette biscottate integrali (b)"]


def test_a_dead_meal_history_still_logs_through_search(wiring, monkeypatch):
    """An unusable history must degrade to a search, not fail the whole log."""
    monkeypatch.setattr(
        quick_log,
        "get_meal_foods",
        lambda client, meal: (_ for _ in ()).throw(RuntimeError("HTTP 500")),
    )
    wiring["search"] = [_food("333", "Patate", [(100, "g", 1)])]

    result = _log(food="patate")

    assert result["success"] is True
    assert result["matched_from"] == "search"


def test_the_tool_reports_totals_without_a_second_diary_read(wiring, monkeypatch):
    monkeypatch.setattr(diary_tools, "get_mfp_client", lambda: object())
    wiring["search"] = [_food("444", "Patate", [(100, "g", 1)])]

    payload = json.loads(
        asyncio.run(
            diary_tools.mfp_log_food(
                server.LogFoodInput(food="patate", amount=250, unit="g", meal="Lunch")
            )
        )
    )

    assert payload["success"] is True
    assert payload["meal_totals"] == {"calories": 100.0}
    assert payload["day_totals"] == {"calories": 900.0}
