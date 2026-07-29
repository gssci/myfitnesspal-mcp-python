"""Regression tests for converting user amounts to MFP serving counts."""

import pytest

from mfp_mcp import server


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
