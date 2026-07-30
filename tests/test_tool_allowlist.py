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


def test_string_id_coercion_from_integers():
    # Verify integer IDs passed by LLMs are coerced to strings across all input models
    add_input = server.AddFoodToDiaryInput(
        mfp_id=94315228400869, meal="Lunch", amount=500, unit="g"
    )
    assert add_input.mfp_id == "94315228400869"

    details_input = server.GetFoodDetailsInput(mfp_id=138297602770277)
    assert details_input.mfp_id == "138297602770277"

    resolve_input = server.ResolveMealFoodInput(history_id=264730669198397, meal=1)
    assert resolve_input.history_id == "264730669198397"

    delete_input = server.DeleteCustomFoodInput(food_id=999888777)
    assert delete_input.food_id == "999888777"

    remove_input = server.RemoveFoodFromDiaryInput(entry_id=555444333)
    assert remove_input.entry_id == "555444333"
