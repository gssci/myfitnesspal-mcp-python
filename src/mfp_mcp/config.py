"""Application configuration and environment loading."""

from pathlib import Path

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


def load_environment() -> None:
    """Load local credentials without overriding explicitly supplied values."""
    project_env = Path(__file__).resolve().parents[2] / ".env"
    cwd_env = Path.cwd() / ".env"
    load_dotenv(project_env, override=False)
    if cwd_env != project_env:
        load_dotenv(cwd_env, override=False)
