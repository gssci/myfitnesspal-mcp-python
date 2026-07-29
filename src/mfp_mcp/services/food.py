"""Food lookup, search, and custom-food services."""

import json
import logging
from typing import Any
from urllib.parse import quote

from lxml import html as lxml_html

from ..browser_cookies import try_chromium_browsers_for_session_cookies
from ..config import MFP_API_BASE, MFP_BROWSER_USER_AGENT, MFP_FOOD_SEARCH_PAGE, MFP_WEB_BASE
from ..cookie_store import dict_to_cookiejar, save_cookies
from ..units import is_gram_unit
from .http import _api_error_detail, _get_csrf_token, _mfp_api_headers, _web_headers

logger = logging.getLogger("mfp_mcp")

_NUTRIENT_KEYS = {
    "fat": "fat",
    "saturated_fat": "saturated_fat",
    "polyunsaturated_fat": "polyunsaturated_fat",
    "monounsaturated_fat": "monounsaturated_fat",
    "trans_fat": "trans_fat",
    "cholesterol": "cholesterol",
    "sodium": "sodium",
    "potassium": "potassium",
    "carbs": "carbohydrates",
    "fiber": "fiber",
    "sugar": "sugar",
    "protein": "protein",
    "vitamin_a": "vitamin_a",
    "vitamin_c": "vitamin_c",
    "calcium": "calcium",
    "iron": "iron",
}


def get_food_v2(client, mfp_id: str) -> dict[str, Any]:
    """
    Fetch a food's full v2 record, including its version and serving sizes.

    The diary API rejects entries whose food version does not match the
    current stored version, so this must be read fresh rather than cached.

    Args:
        client: Authenticated myfitnesspal.Client instance
        mfp_id: MyFitnessPal food item ID

    Returns:
        The food object as returned by the v2 API

    Raises:
        RuntimeError: If the food cannot be retrieved
    """
    response = client.session.get(
        f"{MFP_API_BASE}/v2/foods",
        params={"ids": str(mfp_id)},
        headers=_mfp_api_headers(client),
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Could not look up food {mfp_id}: HTTP {response.status_code}")

    items = response.json().get("items") or []
    if not items:
        raise RuntimeError(f"No food found with ID {mfp_id}")
    return items[0]


def select_serving_size(food: dict[str, Any], unit: str | None = None) -> dict[str, Any]:
    """
    Choose which of a food's serving sizes to log against.

    Args:
        food: Food object from get_food_v2
        unit: Optional unit to match (e.g. "oz", "medium breast"). Matching is
            case-insensitive and accepts a substring. Falls back to the food's
            default (first) serving size when omitted or unmatched.

    Returns:
        The serving size dict, trimmed to the fields the diary API permits

    Raises:
        RuntimeError: If the food declares no serving sizes
    """
    serving_sizes = food.get("serving_sizes") or []
    if not serving_sizes:
        raise RuntimeError(f"Food {food.get('id')} has no serving sizes")

    chosen = serving_sizes[0]
    if unit:
        wanted = unit.strip().lower()
        for size in serving_sizes:
            size_unit = str(size.get("unit", "")).lower()
            if size_unit == wanted or wanted in size_unit:
                chosen = size
                break
        else:
            logger.warning(
                f"Unit {unit!r} not found for food {food.get('id')}; "
                f"using default serving {chosen.get('unit')!r}"
            )

    # The diary endpoint rejects any serving_size field beyond these three.
    return {
        "value": chosen["value"],
        "unit": chosen["unit"],
        "nutrition_multiplier": chosen["nutrition_multiplier"],
    }


def _extract_food_search_items(content: bytes, query: str) -> list[dict[str, Any]]:
    """Extract food results from the search page's server-rendered state."""
    document = lxml_html.fromstring(content)
    scripts = document.xpath("//script[@id='__NEXT_DATA__']/text()")
    if not scripts:
        raise RuntimeError("MyFitnessPal search page contained no Next.js state")

    try:
        page_data = json.loads(scripts[0])
        queries = page_data["props"]["pageProps"]["dehydratedState"]["queries"]
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        raise RuntimeError("MyFitnessPal search page state had an unexpected format") from e

    wanted = query.casefold()
    for cached_query in queries:
        query_key = cached_query.get("queryKey") or []
        if (
            len(query_key) >= 2
            and query_key[0] == "food"
            and str(query_key[1]).casefold() == wanted
        ):
            data = cached_query.get("state", {}).get("data", {})
            items = data.get("items") if isinstance(data, dict) else None
            return items if isinstance(items, list) else []

    raise RuntimeError(f"MyFitnessPal search page contained no results state for {query!r}")


def _format_serving_size(serving: dict[str, Any]) -> str:
    """Format the primary serving the same way the website displays it."""
    value = serving.get("value")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(part) for part in (value, serving.get("unit")) if part is not None)


def search_foods_web(client, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search foods through the cookie-authenticated MyFitnessPal webpage."""
    url = f"{MFP_FOOD_SEARCH_PAGE}/{quote(query, safe='')}"

    def load_page():
        return client.session.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": MFP_BROWSER_USER_AGENT,
            },
            timeout=30,
        )

    response = load_page()

    # A cookie set can still satisfy the legacy diary endpoint while NextAuth
    # considers it incomplete and redirects modern web pages to /account/logout.
    # Refresh from the browser in that case, then persist only after the search
    # page itself proves the replacement session works.
    if "/account/logout" in str(getattr(response, "url", "")):
        browser_session = try_chromium_browsers_for_session_cookies()
        if browser_session:
            _, browser_cookies = browser_session
            client.session.cookies.clear()
            client.session.cookies.update(dict_to_cookiejar(browser_cookies))
            response = load_page()
            if response.status_code == 200 and "/account/logout" not in str(response.url):
                save_cookies(browser_cookies)

    if response.status_code != 200:
        raise RuntimeError(
            f"Could not load MyFitnessPal food search: HTTP {response.status_code}. "
            "The stored browser session may have expired."
        )

    raw_items = _extract_food_search_items(response.content, query)
    results = []
    for wrapped_item in raw_items[:limit]:
        item = wrapped_item.get("item", {}) if isinstance(wrapped_item, dict) else {}
        if not item.get("id"):
            continue
        serving_sizes = item.get("serving_sizes") or []
        energy = (item.get("nutritional_contents") or {}).get("energy") or {}
        results.append(
            {
                "name": item.get("description"),
                "brand": item.get("brand_name"),
                "serving": _format_serving_size(serving_sizes[0]) if serving_sizes else None,
                "available_servings": [
                    _format_serving_size(serving) for serving in serving_sizes[:5]
                ],
                "supports_grams": any(
                    is_gram_unit(str(serving.get("unit", ""))) for serving in serving_sizes
                ),
                "calories": energy.get("value"),
                "mfp_id": str(item["id"]),
            }
        )
    return results


def list_own_foods(client, search: str = "") -> list[dict[str, Any]]:
    """List the user's own custom foods (newest first), optionally filtered."""
    r = client.session.get(
        f"{MFP_WEB_BASE}/api/services/users/foods/mine",
        params={"search": search},
        headers=_web_headers(),
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Could not list own foods: HTTP {r.status_code}. "
            "The stored session may have expired — run refresh_browser_cookies."
        )
    return r.json() or []


def _serving_sizes(amount: float, unit: str) -> list[dict[str, Any]]:
    """Primary serving plus the container wrapper MFP's own client sends."""
    return [
        {
            "value": amount,
            "unit": unit,
            "nutrition_multiplier": 1,
            "gram_weight": 1,
            "fraction": False,
            "index": 0,
        },
        {
            "value": 1,
            "unit": f"container ({amount} {unit} ea.)",
            "nutrition_multiplier": 1,
            "gram_weight": 1,
            "fraction": False,
            "index": 1,
        },
    ]


def create_custom_food(client, spec: dict[str, Any]) -> dict[str, Any]:
    """
    Create a private custom food via the web BFF.

    Args:
        client: authenticated myfitnesspal.Client
        spec: validated CreateCustomFoodInput as a dict

    Returns:
        {"id": str|None, "status": int, "description": str}
    """
    csrf = _get_csrf_token(client)

    nutrition: dict[str, Any] = {
        "energy": {"unit": "calories", "value": spec["calories"]},
        "grams": 1,
    }
    for arg, api_key in _NUTRIENT_KEYS.items():
        value = spec.get(arg)
        if value is not None:
            nutrition[api_key] = value

    item = {
        "description": spec["description"],
        "brand_name": spec.get("brand_name") or "Generic",
        "public": bool(spec.get("public", False)),
        "type": "food",
        "nutritional_contents": nutrition,
        "serving_sizes": _serving_sizes(
            spec.get("serving_amount", 100), spec.get("serving_unit", "g")
        ),
    }
    item["country_code"] = spec.get("country_code") or "NL"
    # Ownership is assigned by MyFitnessPal from the session, so user_id is not
    # sent: posting without it returns 200 with the correct owner. Sending it
    # would also mean an extra request, and would fail for an account that has
    # no custom foods yet.

    r = client.session.post(
        f"{MFP_WEB_BASE}/api/services/foods",
        headers=_web_headers(csrf, json_body=True),
        data=json.dumps({"item": item}),
        timeout=30,
    )

    if r.status_code not in (200, 201):
        hint = "" if csrf else " (no CSRF token acquired)"
        detail = _api_error_detail(r)
        raise RuntimeError(
            f"Failed to create custom food: HTTP {r.status_code}{hint}"
            + (f" - {detail}" if detail else "")
        )

    # MFP returns a bare list of the created food object(s); older docs/clients
    # assumed {"item": {...}}. Accept both.
    new_id = None
    try:
        body = r.json()
        if isinstance(body, list) and body:
            new_id = body[0].get("id")
        elif isinstance(body, dict):
            new_id = (body.get("item") or body).get("id")
    except Exception:
        pass
    if new_id is None:
        logger.warning("Food created but MyFitnessPal returned no id")

    logger.info(f"Created custom food {new_id}: {spec['description']}")
    return {"id": new_id, "status": r.status_code, "description": spec["description"]}


def delete_custom_food(client, food_id: str) -> int:
    """Delete a custom food by id. MFP has no update endpoint — recreate + delete."""
    csrf = _get_csrf_token(client)
    r = client.session.delete(
        f"{MFP_WEB_BASE}/api/services/foods/{food_id}",
        headers=_web_headers(csrf),
        timeout=30,
    )
    if r.status_code not in (200, 204):
        detail = _api_error_detail(r)
        raise RuntimeError(
            f"Failed to delete custom food {food_id}: HTTP {r.status_code}"
            + (f" - {detail}" if detail else "")
        )
    logger.info(f"Deleted custom food {food_id}")
    return r.status_code
