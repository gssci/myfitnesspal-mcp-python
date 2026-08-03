"""MyFitnessPal MCP server composition and compatibility exports.

The implementation is split by responsibility. Re-exports in this module keep
the historical ``mfp_mcp.server`` import surface stable for callers and tests.
"""

# ruff: noqa: F401 - this module deliberately preserves legacy re-exports.

from .app import mcp
from .auth import _verify_cookies_and_format, clear_cached_mfp_client, get_mfp_client
from .browser_cookies import (
    _CHROMIUM_BROWSER_ALIASES,
    _has_real_mfp_session,
    _try_extract_from_chromium_browser,
    try_chromium_browsers_for_session_cookies,
)
from .config import (
    CONFIG_DIR,
    COOKIES_FILE,
    MFP_API_BASE,
    MFP_CLIENT_ID,
    MFP_WEB_BASE,
    VALID_MEALS,
    load_environment,
)
from .cookie_store import dict_to_cookiejar, load_cookies, save_cookies
from .credentials import (
    authenticate_with_credentials,
    get_decrypted_credential,
    get_secret_key,
    looks_like_fernet_token,
)
from .formatting import (
    ResponseFormat,
    format_exercise,
    format_meal_entry,
    format_nutrition_dict,
    format_response,
    ordered_dict_to_dict,
    parse_date,
)
from .models import (
    AddFoodToDiaryInput,
    CreateCustomFoodInput,
    DeleteCustomFoodInput,
    GetDiaryInput,
    GetExercisesInput,
    GetFoodDetailsInput,
    GetGoalsInput,
    GetMealFoodsInput,
    GetMeasurementsInput,
    GetReportInput,
    GetWaterInput,
    ListOwnFoodsInput,
    RemoveFoodFromDiaryInput,
    ResolveMealFoodInput,
    SearchFoodInput,
    SetGoalsInput,
    SetMeasurementInput,
    SetWaterInput,
)
from .services.diary import (
    add_food_to_diary,
    list_diary_entries,
    remove_food_entry,
    resolve_food_amount,
    set_water_intake,
)
from .services.food import (
    _extract_food_search_items,
    _format_serving_size,
    _parse_history_fragment,
    _serving_sizes,
    assess_food_plausibility,
    create_custom_food,
    delete_custom_food,
    get_food_v2,
    get_foods_v2,
    get_meal_foods,
    invalidate_meal_food_cache,
    list_own_foods,
    resolve_meal_food,
    search_foods_legacy,
    search_foods_next,
    search_foods_web,
    select_serving_size,
    serving_capabilities,
)
from .services.http import (
    MfpSessionExpiredError,
    _api_error_detail,
    _get_csrf_token,
    _mfp_api_headers,
    _web_headers,
    is_logged_out_response,
    refresh_session_from_browser,
    web_session_is_live,
)
from .tools.auth import refresh_browser_cookies
from .tools.diary import (
    mfp_add_food_to_diary,
    mfp_get_diary,
    mfp_get_water,
    mfp_remove_food_from_diary,
    mfp_set_water,
)
from .tools.food import (
    mfp_create_custom_food,
    mfp_delete_custom_food,
    mfp_get_food_details,
    mfp_get_meal_foods,
    mfp_list_own_foods,
    mfp_resolve_meal_food,
    mfp_search_food,
)
from .tools.profile import (
    mfp_get_exercises,
    mfp_get_goals,
    mfp_get_measurements,
    mfp_get_report,
    mfp_set_goals,
    mfp_set_measurement,
)

ESSENTIAL_TOOL_NAMES = frozenset(
    {
        "refresh_browser_cookies",
        "mfp_get_diary",
        "mfp_add_food_to_diary",
        "mfp_remove_food_from_diary",
        "mfp_get_meal_foods",
        "mfp_resolve_meal_food",
        "mfp_search_food",
        "mfp_get_food_details",
        "mfp_get_report",
    }
)


def _keep_essential_tools() -> None:
    """Limit the advertised MCP surface while preserving Python re-exports."""
    registered = mcp._tool_manager.list_tools()
    for tool in registered:
        if tool.name not in ESSENTIAL_TOOL_NAMES:
            mcp.remove_tool(tool.name)


_keep_essential_tools()


def main() -> None:
    """Load local configuration and run the MCP server."""
    load_environment()
    mcp.run()


if __name__ == "__main__":
    main()
