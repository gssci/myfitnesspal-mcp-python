"""MCP tools for food search, details, and custom foods."""

from ..auth import get_mfp_client
from ..flat_schema import flat_tool
from ..formatting import format_response
from ..models import (
    CreateCustomFoodInput,
    DeleteCustomFoodInput,
    GetFoodDetailsInput,
    GetMealFoodsInput,
    ListOwnFoodsInput,
    ResolveMealFoodInput,
    SearchFoodInput,
)
from ..services.food import (
    assess_food_plausibility,
    create_custom_food,
    delete_custom_food,
    get_food_v2,
    get_meal_foods,
    list_own_foods,
    resolve_meal_food,
    search_foods_web,
    serving_capabilities,
)


@flat_tool(
    name="mfp_get_meal_foods",
    annotations={
        "title": "Get Recent and Frequent Meal Foods",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def mfp_get_meal_foods(params: GetMealFoodsInput) -> str:
    """Get recent and frequent foods for a specific meal.

    Call once per requested meal before global search and reuse the result. Meal
    numbers: 0=Breakfast, 1=Lunch, 2=Dinner, 3=Snacks. Resolve a semantic match's
    history_id; it cannot be added directly.
    """
    try:
        client = get_mfp_client()
        lists = get_meal_foods(client, params.meal)
        # Read rather than pop: this dict is the cache entry itself.
        degraded = bool(lists.get("degraded"))
        data: dict = {
            "meal": params.meal,
            "recent_count": len(lists["recent"]),
            "frequent_count": len(lists["frequent"]),
        }
        if degraded:
            # A zero count here means "MyFitnessPal would not serve this list",
            # not "the account has no such history". Saying so keeps the caller
            # from concluding the user has never eaten these foods.
            data["note"] = (
                "MyFitnessPal refused part of this meal's history, so a list shown as "
                "empty may not really be empty. The foods listed are still usable."
            )
        elif not lists["recent"] and not lists["frequent"]:
            # An empty history is now only ever reported when the session is
            # valid, so it is a real answer: say so, rather than leaving the
            # caller to guess whether the lookup silently failed.
            data["note"] = (
                "This meal has no recent or frequent foods yet. "
                "Use mfp_search_food for every food in this meal."
            )
        data.update({key: lists[key] for key in ("recent", "frequent")})
        return format_response(data, params.response_format, "Recent and Frequent Meal Foods")
    except Exception as e:
        return f"Error getting meal foods: {e!s}"


@flat_tool(
    name="mfp_resolve_meal_food",
    annotations={
        "title": "Resolve and Validate Meal Food",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def mfp_resolve_meal_food(params: ResolveMealFoodInput) -> str:
    """Resolve a same-meal history_id to an add-ready, nutrition-checked mfp_id.

    If unresolved or implausible, use mfp_search_food instead.
    """
    try:
        client = get_mfp_client()
        result = resolve_meal_food(client, params.history_id, params.meal)
        return format_response(result, params.response_format, "Resolved Meal Food")
    except Exception as e:
        return f"Error resolving meal food: {e!s}"


@flat_tool(
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
    """Search foods with IDs, servings, calories, verification, and plausibility.

    Prefer supports_grams=true for gram requests; never use implausible results.
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


@flat_tool(
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
    """Get full nutrition and serving details for an mfp_id."""
    try:
        client = get_mfp_client()
        item = get_food_v2(client, params.mfp_id)
        serving_sizes = item.get("serving_sizes") or []
        nutrition = item.get("nutritional_contents") or {}
        energy = nutrition.get("energy") or {}

        data = {
            "mfp_id": params.mfp_id,
            "description": item.get("description", "N/A"),
            "brand_name": item.get("brand_name"),
            "verified": bool(item.get("verified")),
            "calories": energy.get("value"),
            "nutrition": nutrition,
            **serving_capabilities(serving_sizes),
            "nutrition_plausibility": assess_food_plausibility(item),
        }

        return format_response(data, params.response_format, "Food Item Details")

    except Exception as e:
        return f"Error getting food details: {e!s}"


@flat_tool(
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


@flat_tool(
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
                    "id": str(f["id"]) if f.get("id") is not None else None,
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


@flat_tool(
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
