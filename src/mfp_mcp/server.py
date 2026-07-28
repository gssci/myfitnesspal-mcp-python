"""MyFitnessPal MCP server composition and compatibility exports.

The implementation is split by responsibility. Re-exports in this module keep
the historical ``mfp_mcp.server`` import surface stable for callers and tests.
"""

# ruff: noqa: F401 - this module deliberately preserves legacy re-exports.

from .app import mcp
from .auth import _verify_cookies_and_format, get_mfp_client
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
    GetMeasurementsInput,
    GetReportInput,
    GetWaterInput,
    ListOwnFoodsInput,
    RemoveFoodFromDiaryInput,
    SearchFoodInput,
    SetGoalsInput,
    SetMeasurementInput,
    SetWaterInput,
)
from .services.diary import (
    add_food_to_diary,
    list_diary_entries,
    remove_food_entry,
    set_water_intake,
)
from .services.food import (
    _extract_food_search_items,
    _format_serving_size,
    _serving_sizes,
    create_custom_food,
    delete_custom_food,
    get_food_v2,
    list_own_foods,
    search_foods_web,
    select_serving_size,
)
from .services.http import (
    _api_error_detail,
    _get_csrf_token,
    _mfp_api_headers,
    _web_headers,
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
    mfp_list_own_foods,
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


def main() -> None:
    """Load local configuration and run the MCP server."""
    load_environment()
    mcp.run()


if __name__ == "__main__":
    main()
