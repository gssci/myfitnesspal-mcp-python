"""MCP tools for food search, details, and custom foods."""

from ..app import mcp
from ..auth import get_mfp_client
from ..formatting import format_response
from ..models import (
    CreateCustomFoodInput,
    DeleteCustomFoodInput,
    GetFoodDetailsInput,
    ListOwnFoodsInput,
    SearchFoodInput,
)
from ..services.food import (
    create_custom_food,
    delete_custom_food,
    list_own_foods,
    search_foods_web,
)


@mcp.tool(
    name="mfp_search_food",
    annotations={
        "title": "Search Food Database",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mfp_search_food(params: SearchFoodInput) -> str:
    """
    Search the MyFitnessPal food database for food items.

    Returns a list of matching foods with their name, brand, serving size,
    calories, and MFP ID (which can be used with mfp_get_food_details).

    Args:
        params: SearchFoodInput containing:
            - query (str): Search query (e.g., 'chicken breast')
            - limit (int): Maximum results to return (default 10)
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: List of matching food items with basic nutrition info
    """
    try:
        client = get_mfp_client()
        results = search_foods_web(client, params.query, params.limit)
        data = {"query": params.query, "count": len(results), "results": results}

        return format_response(
            data, params.response_format, f"Food Search Results for '{params.query}'"
        )

    except Exception as e:
        return f"Error searching foods: {e!s}"


@mcp.tool(
    name="mfp_get_food_details",
    annotations={
        "title": "Get Food Item Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mfp_get_food_details(params: GetFoodDetailsInput) -> str:
    """
    Get detailed nutritional information for a specific food item by its MFP ID.

    Returns complete nutrition breakdown including calories, macros (protein, carbs, fat),
    fiber, sugar, sodium, cholesterol, vitamins, minerals, and available serving sizes.

    Args:
        params: GetFoodDetailsInput containing:
            - mfp_id (str): MyFitnessPal food item ID from search results
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Complete nutritional information for the food item
    """
    try:
        client = get_mfp_client()
        item = client.get_food_item_details(params.mfp_id)

        data = {
            "mfp_id": params.mfp_id,
            "description": getattr(item, "description", "N/A"),
            "brand_name": getattr(item, "brand_name", None),
            "verified": getattr(item, "verified", False),
            "calories": getattr(item, "calories", None),
            "nutrition": {
                "protein": getattr(item, "protein", None),
                "carbohydrates": getattr(item, "carbohydrates", None),
                "fat": getattr(item, "fat", None),
                "fiber": getattr(item, "fiber", None),
                "sugar": getattr(item, "sugar", None),
                "sodium": getattr(item, "sodium", None),
                "cholesterol": getattr(item, "cholesterol", None),
                "saturated_fat": getattr(item, "saturated_fat", None),
                "polyunsaturated_fat": getattr(item, "polyunsaturated_fat", None),
                "monounsaturated_fat": getattr(item, "monounsaturated_fat", None),
                "trans_fat": getattr(item, "trans_fat", None),
                "potassium": getattr(item, "potassium", None),
                "vitamin_a": getattr(item, "vitamin_a", None),
                "vitamin_c": getattr(item, "vitamin_c", None),
                "calcium": getattr(item, "calcium", None),
                "iron": getattr(item, "iron", None),
            },
            "servings": [],
        }

        # Get serving sizes if available
        if hasattr(item, "servings"):
            for serving in item.servings:
                data["servings"].append(str(serving))

        return format_response(data, params.response_format, "Food Item Details")

    except Exception as e:
        return f"Error getting food details: {e!s}"


@mcp.tool(
    name="mfp_create_custom_food",
    annotations={
        "title": "Create Custom Food",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def mfp_create_custom_food(params: CreateCustomFoodInput) -> str:
    """
    Create a private custom food in the user's MyFitnessPal account.

    Fills the full nutrition panel MFP supports (macros, fats breakdown,
    cholesterol, sodium, potassium, fiber, sugars, and the four %DV micros).
    Uses the cookie-authenticated web endpoint, so no browser needs to be
    running. Returns the new food's id, which mfp_add_food_to_diary accepts.

    CARBS ARE NET (with the default country_code="NL"): pass net carbs in
    `carbs`; MFP stores net_carbs as given and reports total = carbs + fiber.
    Never pre-add fiber. Verified: carbs=42/fiber=8 stores 50/42 under "NL" but
    42/34 with country_code omitted, so the field is load-bearing, not cosmetic.

    MFP has no update endpoint. To correct a food, create the corrected version
    then mfp_delete_custom_food the old one.

    Args:
        params: CreateCustomFoodInput (description, brand_name, serving_amount,
            serving_unit, calories + optional nutrients, public, response_format)

    Returns:
        str: The created food's id, description and HTTP status
    """
    try:
        client = get_mfp_client()
        result = create_custom_food(client, params.model_dump())
        return format_response(result, params.response_format, "Custom Food Created")
    except Exception as e:
        return f"Error creating custom food: {e!s}"


@mcp.tool(
    name="mfp_list_own_foods",
    annotations={
        "title": "List Own Custom Foods",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mfp_list_own_foods(params: ListOwnFoodsInput) -> str:
    """
    List the user's own custom foods, newest first.

    Private custom foods do not reliably surface in mfp_search_food, so this is
    the way to find the id of something previously created.

    Args:
        params: ListOwnFoodsInput (search, limit, response_format)

    Returns:
        str: Matching custom foods with id, description, brand and calories
    """
    try:
        client = get_mfp_client()
        foods = list_own_foods(client, params.search)[: params.limit]
        data = {
            "count": len(foods),
            "foods": [
                {
                    "id": f.get("id"),
                    "description": f.get("description"),
                    "brand_name": f.get("brand_name"),
                    "calories": (f.get("nutritional_contents", {}).get("energy", {}) or {}).get(
                        "value"
                    ),
                    "serving": (f.get("serving_sizes") or [{}])[0].get("unit"),
                    "public": f.get("public"),
                }
                for f in foods
            ],
        }
        return format_response(data, params.response_format, "My Custom Foods")
    except Exception as e:
        return f"Error listing own foods: {e!s}"


@mcp.tool(
    name="mfp_delete_custom_food",
    annotations={
        "title": "Delete Custom Food",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mfp_delete_custom_food(params: DeleteCustomFoodInput) -> str:
    """
    Delete one of the user's custom foods by id.

    Destructive and not recoverable. A food actively referenced by a logged
    diary entry may be refused by MyFitnessPal.

    Args:
        params: DeleteCustomFoodInput (food_id)

    Returns:
        str: Confirmation with the HTTP status
    """
    try:
        client = get_mfp_client()
        status = delete_custom_food(client, params.food_id)
        data = {"food_id": params.food_id, "deleted": True, "status": status}
        return format_response(data, params.response_format, "Custom Food Deleted")
    except Exception as e:
        return f"Error deleting custom food: {e!s}"
