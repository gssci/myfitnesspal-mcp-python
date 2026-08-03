"""HTTP headers and shared web-service primitives."""

import logging

from ..config import MFP_BROWSER_USER_AGENT, MFP_CLIENT_ID, MFP_WEB_BASE

logger = logging.getLogger("mfp_mcp")

# MyFitnessPal answers an unauthenticated web request with HTTP 200 after
# redirecting to one of these routes, so status codes alone never reveal an
# expired session. The final URL does.
LOGGED_OUT_URL_MARKERS = (
    "/account/logout",
    "/account/login",
    "/account/register",
    "/welcome",
)


class MfpSessionExpiredError(RuntimeError):
    """MyFitnessPal refused the stored web session.

    Raised instead of letting a login/logout page be parsed as data. The
    message deliberately names the failure as an authentication problem so
    calling agents can react by refreshing cookies rather than by assuming
    the food simply does not exist.
    """

    def __init__(self, while_doing: str) -> None:
        super().__init__(
            f"Authentication error: MyFitnessPal rejected the stored session while "
            f"{while_doing}. This is a session problem, not a missing food. Log in to "
            "myfitnesspal.com in a Chromium-based browser, then call "
            "refresh_browser_cookies and retry."
        )


def is_logged_out_response(response) -> bool:
    """True when a request was bounced to MyFitnessPal's logged-out routes."""
    url = str(getattr(response, "url", "") or "")
    return any(marker in url for marker in LOGGED_OUT_URL_MARKERS)


def web_session_is_live(session) -> bool:
    """True when NextAuth still recognises this cookie jar.

    MyFitnessPal runs two independent sessions off the same cookie jar: the
    legacy Rails one (``_mfp_session``, which also mints the v2 API bearer
    token) and the NextAuth one (``__Secure-next-auth.session-token``). They
    expire independently, and the half-dead combination — legacy alive, NextAuth
    rejected — is the failure this project keeps hitting: diary writes and
    ``client.get_date()`` keep working, so every "is my session OK?" check
    passes, while every Next.js-served surface silently bounces to
    /account/logout.

    ``/api/auth/session`` answers 200 with ``{}`` for a rejected session and a
    user object for a live one, so it is the cheapest honest probe available.
    """
    try:
        response = session.get(
            f"{MFP_WEB_BASE}/api/auth/session",
            # Cloudflare binds `cf_clearance` to the user agent that earned it,
            # so a probe sent under the default client UA can be challenged and
            # report a perfectly good session as dead.
            headers={"Accept": "application/json", "User-Agent": MFP_BROWSER_USER_AGENT},
            timeout=15,
        )
        if response.status_code != 200:
            return False
        body = response.json()
    except Exception as e:
        logger.debug(f"Could not probe the NextAuth session: {e}")
        return False
    return isinstance(body, dict) and bool(body.get("userId") or body.get("user"))


def refresh_session_from_browser(client) -> dict[str, str] | None:
    """Replace the client's cookies with a live browser session, in place.

    A cookie set can still satisfy the legacy diary endpoints while NextAuth
    considers it incomplete and bounces modern pages to /account/logout, so
    every cookie-authenticated surface needs the same recovery path — not just
    food search, which was previously the only caller that had one.

    The replacement is applied to the shared session but deliberately *not*
    persisted here: the caller re-issues its request first and only writes
    cookies.json once the replacement has proven itself, so a transient
    failure cannot poison the stored session.

    The previous jar is restored when no browser yields a *live* web session.
    Overwriting unconditionally used to turn one failed search into a
    process-wide outage: the browser's own session is usually the same dead one
    already loaded, and clearing the jar threw away a legacy cookie that was
    still authenticating diary writes.

    Returns:
        The cookies now installed on the session, or None if no browser had a
        usable MyFitnessPal session.
    """
    from ..browser_cookies import try_chromium_browsers_for_session_cookies
    from ..cookie_store import dict_to_cookiejar

    previous = list(client.session.cookies)
    result = try_chromium_browsers_for_session_cookies(
        validate=lambda cookies: _cookies_have_live_web_session(cookies)
    )
    if not result:
        logger.warning("No Chromium browser held a usable MyFitnessPal session to refresh from")
        return None
    browser_name, cookies = result
    client.session.cookies.clear()
    client.session.cookies.update(dict_to_cookiejar(cookies))
    if not web_session_is_live(client.session):
        logger.warning(
            f"Cookies from {browser_name} were also rejected by MyFitnessPal; "
            "keeping the previous session"
        )
        client.session.cookies.clear()
        for cookie in previous:
            client.session.cookies.set_cookie(cookie)
        return None
    logger.info(f"Replaced the MyFitnessPal session with fresh cookies from {browser_name}")
    return cookies


def _cookies_have_live_web_session(cookies: dict[str, str]) -> bool:
    """Probe a candidate cookie set on a throwaway session."""
    import requests

    from ..cookie_store import dict_to_cookiejar

    probe = requests.Session()
    probe.cookies.update(dict_to_cookiejar(cookies))
    try:
        return web_session_is_live(probe)
    finally:
        probe.close()


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
