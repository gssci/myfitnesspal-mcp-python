import pytest
from pydantic import ValidationError

from mfp_mcp import server


def test_server_advertises_only_essential_tools():
    names = {tool.name for tool in server.mcp._tool_manager.list_tools()}

    assert names == server.ESSENTIAL_TOOL_NAMES


def test_food_lookup_defaults_are_compact():
    assert server.SearchFoodInput(query="apple").limit == 15
    assert "limit_per_list" not in server.GetMealFoodsInput.model_json_schema()["properties"]


def test_food_lookup_result_counts_are_bounded():
    with pytest.raises(ValidationError):
        server.SearchFoodInput(query="apple", limit=51)


def test_add_food_requires_explicit_meal_amount_and_unit():
    with pytest.raises(ValidationError):
        server.AddFoodToDiaryInput(mfp_id="food-id")
