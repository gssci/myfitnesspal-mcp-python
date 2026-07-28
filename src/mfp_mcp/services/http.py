"""HTTP headers and shared web-service primitives."""

import logging

from ..config import MFP_CLIENT_ID, MFP_WEB_BASE

logger = logging.getLogger("mfp_mcp")


def _mfp_api_headers(client, json_body: bool = False) -> dict[str, str]:
    """
    Build auth headers for MyFitnessPal's v2 JSON API.

    The v2 API backs the current MFP web client. It requires the session's
    OAuth bearer token plus an mfp-client-id identifying the calling client.
    """
    headers = {
        "Authorization": f"Bearer {client.access_token}",
        "mfp-client-id": MFP_CLIENT_ID,
        "mfp-user-id": str(client.user_id),
        "Accept": "application/json",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _web_headers(csrf: str | None = None, json_body: bool = False) -> dict[str, str]:
    """Headers for the cookie-authenticated web BFF."""
    headers = {"Accept": "application/json"}
    if json_body:
        headers["Content-Type"] = "application/json"
    if csrf:
        headers["x-csrf-token"] = csrf
    return headers


def _api_error_detail(response) -> str:
    """Pull MFP's structured error text out of a failed response.

    Mirrors how add_food_to_diary surfaces v2 errors, so we report the API's own
    message rather than echoing an arbitrary slice of the response body.
    """
    try:
        body = response.json()
    except Exception:
        return ""
    if not isinstance(body, dict):
        return ""
    return str(
        body.get("error_description")
        or body.get("error_details", {}).get("item_error")
        or body.get("error")
        or ""
    )


def _get_csrf_token(client) -> str | None:
    """Fetch a CSRF token. Returns None if unavailable; POST will then 403."""
    try:
        r = client.session.get(f"{MFP_WEB_BASE}/api/auth/csrf", headers=_web_headers(), timeout=30)
        if r.status_code == 200:
            return r.json().get("csrfToken")
    except Exception as e:
        logger.warning(f"Could not fetch CSRF token: {e}")
    return None
