"""Application configuration and environment loading."""

from pathlib import Path
from typing import Any

from dotenv import load_dotenv

CONFIG_DIR = Path.home() / ".mfp_mcp"
COOKIES_FILE = CONFIG_DIR / "cookies.json"

MFP_WEB_BASE = "https://www.myfitnesspal.com"
MFP_API_BASE = "https://api.myfitnesspal.com"
MFP_CLIENT_ID = "mfp-main-js"
MFP_FOOD_SEARCH_PAGE = f"{MFP_WEB_BASE}/food/calorie-chart-nutrition-facts"
MFP_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
VALID_MEALS = ("Breakfast", "Lunch", "Dinner", "Snacks")
# MyFitnessPal's own ordering: the classic web endpoints address meals by this
# index, while the diary write endpoints address them by name.
MEAL_NAME_BY_NUMBER = dict(enumerate(VALID_MEALS))
MEAL_NUMBER_BY_NAME = {name.lower(): number for number, name in MEAL_NAME_BY_NUMBER.items()}
# "Snack" is what people (and models) say; MyFitnessPal calls it "Snacks".
_MEAL_NAME_ALIASES = {"snack": "snacks"}


def _canonical_meal_key(value: str) -> str:
    key = value.strip().lower()
    return _MEAL_NAME_ALIASES.get(key, key)


def normalize_meal_name(value: Any) -> Any:
    """Return a meal as a MyFitnessPal meal name, accepting a number or a name.

    Both meal spellings reach these tools: a language model that has just read
    a meal-number tool description will happily send ``2`` to a tool that wants
    ``"Dinner"``. Anything unrecognized is returned untouched so the caller
    still raises its own error.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return MEAL_NAME_BY_NUMBER.get(value, value)
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("+-").isdigit():
            return MEAL_NAME_BY_NUMBER.get(int(text), value)
        key = _canonical_meal_key(text)
        if key in MEAL_NUMBER_BY_NAME:
            return MEAL_NAME_BY_NUMBER[MEAL_NUMBER_BY_NAME[key]]
    return value


def normalize_meal_number(value: Any) -> Any:
    """Return a meal as its 0-3 number, accepting a number or a name.

    The mirror of ``normalize_meal_name``; unrecognized values pass through.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("+-").isdigit():
            return int(text)
        key = _canonical_meal_key(text)
        if key in MEAL_NUMBER_BY_NAME:
            return MEAL_NUMBER_BY_NAME[key]
    return value


def load_environment() -> None:
    """Load local credentials without overriding explicitly supplied values."""
    project_env = Path(__file__).resolve().parents[2] / ".env"
    cwd_env = Path.cwd() / ".env"
    load_dotenv(project_env, override=False)
    if cwd_env != project_env:
        load_dotenv(cwd_env, override=False)
