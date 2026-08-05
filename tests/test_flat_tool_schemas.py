"""The advertised tool schemas must survive lossy MCP clients.

The tools are written as ``def tool(params: SomeInput)``, which would publish a
single ``params`` property holding a ``$ref`` into ``$defs``. The Ollama client
validates every tool through a model whose per-property fields are only
``type``/``items``/``description``/``enum``, so a ``$ref`` is silently dropped
and the model is shown an argument with no fields at all — it then invents
names like ``meal_number`` or omits required arguments. These tests pin the
flat shape that keeps such clients working.
"""

import asyncio
import json

import pytest
from pydantic import ValidationError

from mfp_mcp import server
from mfp_mcp.flat_schema import OLLAMA_PROPERTY_KEYS
from mfp_mcp.tools import diary as diary_tools
from mfp_mcp.tools import food as food_tools


def _tools():
    return server.mcp._tool_manager.list_tools()


def _schema(name):
    return next(tool.parameters for tool in _tools() if tool.name == name)


def _find_refs(node, path="$"):
    """Yield the path of every ``$ref`` reachable in a JSON schema."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield path
            yield from _find_refs(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _find_refs(value, f"{path}[{index}]")


def _as_ollama_property(prop):
    """Apply the field filter the Ollama client applies to each property."""
    return {key: value for key, value in prop.items() if key in OLLAMA_PROPERTY_KEYS}


def test_every_tool_publishes_a_flat_argument_schema():
    for tool in _tools():
        schema = tool.parameters
        assert list(_find_refs(schema)) == [], f"{tool.name} still publishes a $ref"
        assert "$defs" not in schema, f"{tool.name} still publishes $defs"
        assert "params" not in schema.get("properties", {}), (
            f"{tool.name} still wraps its arguments in a 'params' object"
        )
        for name, prop in schema.get("properties", {}).items():
            assert prop.get("type") != "object", (
                f"{tool.name}.{name} is a nested object; clients that cannot "
                "express nesting will hide it from the model"
            )


def test_required_arguments_survive_the_ollama_property_filter():
    for tool in _tools():
        schema = tool.parameters
        for name in schema.get("required", []):
            surviving = _as_ollama_property(schema["properties"][name])
            assert surviving.get("type"), (
                f"{tool.name}.{name} loses its type when serialized by Ollama"
            )
            assert surviving.get("description"), (
                f"{tool.name}.{name} loses its description when serialized by Ollama"
            )


def test_meal_foods_advertises_the_meal_argument():
    schema = _schema("mfp_get_meal_foods")

    assert schema["required"] == ["meal"]
    assert schema["properties"]["meal"]["type"] == "integer"
    assert "0=Breakfast" in schema["properties"]["meal"]["description"]


def test_add_food_advertises_every_required_argument():
    schema = _schema("mfp_add_food_to_diary")

    assert set(schema["required"]) == {"mfp_id", "meal", "amount", "unit"}
    assert schema["properties"]["meal"]["type"] == "string"


def test_response_format_is_not_advertised_to_models():
    """It exists for Python callers, and cost ~355 tokens of schema to publish."""
    for tool in _tools():
        assert "response_format" not in tool.parameters.get("properties", {}), (
            f"{tool.name} still advertises response_format"
        )
        assert "response_format" not in tool.parameters.get("required", [])

    # Still accepted and still defaulted, for the tests and callers that pass it.
    assert server.SearchFoodInput(query="apple", response_format="json").response_format == "json"
    assert server.SearchFoodInput(query="apple").response_format == "markdown"


def test_published_schemas_carry_no_generated_titles():
    """Pydantic labels every property; models ignore them and pay for them."""
    for tool in _tools():
        assert "title" not in tool.parameters, f"{tool.name} publishes a schema title"
        for name, prop in tool.parameters.get("properties", {}).items():
            assert "title" not in prop, f"{tool.name}.{name} publishes a title"


def test_flat_arguments_reach_the_tool(monkeypatch):
    """A call in the flat shape an LLM now sees must run the original tool."""
    captured = {}

    def fake_get_meal_foods(client, meal):
        captured["meal"] = meal
        return {"recent": [], "frequent": []}

    monkeypatch.setattr(food_tools, "get_mfp_client", lambda: object())
    monkeypatch.setattr(food_tools, "get_meal_foods", fake_get_meal_foods)

    result = asyncio.run(
        server.mcp._tool_manager.call_tool("mfp_get_meal_foods", {"meal": 2})
    )

    assert captured["meal"] == 2
    assert "Error getting meal foods" not in str(result)


def test_flat_arguments_still_coerce_integer_ids(monkeypatch):
    """LLMs send bare numbers for ID fields; flattening must not break that."""
    captured = {}

    monkeypatch.setattr(diary_tools, "get_mfp_client", lambda: object())
    monkeypatch.setattr(
        diary_tools,
        "add_food_to_diary",
        lambda **kwargs: captured.update(kwargs)
        or {
            "requested_amount": 100.0,
            "requested_unit": "g",
            "food_name": "Sgombro",
        },
    )

    asyncio.run(
        server.mcp._tool_manager.call_tool(
            "mfp_add_food_to_diary",
            {"mfp_id": 58995187319997, "meal": "Dinner", "amount": 100, "unit": "g"},
        )
    )

    assert captured["mfp_id"] == "58995187319997"


def test_missing_required_argument_is_still_rejected():
    with pytest.raises(Exception) as excinfo:
        asyncio.run(server.mcp._tool_manager.call_tool("mfp_get_meal_foods", {}))

    assert "meal" in str(excinfo.value)


def test_python_callers_still_pass_the_input_model(monkeypatch):
    """flat_tool registers a wrapper but leaves the decorated function alone."""
    monkeypatch.setattr(food_tools, "get_mfp_client", lambda: object())
    monkeypatch.setattr(
        food_tools, "get_meal_foods", lambda client, meal: {"recent": [], "frequent": []}
    )

    result = asyncio.run(
        food_tools.mfp_get_meal_foods(
            server.GetMealFoodsInput(meal=1, response_format="json")
        )
    )

    assert json.loads(result)["meal"] == 1


def test_unknown_argument_names_are_rejected_loudly():
    """The failure mode that started this: a hallucinated argument name."""
    with pytest.raises(ValidationError):
        server.GetMealFoodsInput(meal_number=2)
