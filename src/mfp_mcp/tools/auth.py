"""MCP utility for refreshing browser cookies."""

import sys

from ..app import mcp
from ..auth import _verify_cookies_and_format
from ..browser_cookies import (
    _CHROMIUM_BROWSER_ALIASES,
    _try_extract_from_chromium_browser,
    try_chromium_browsers_for_session_cookies,
)


@mcp.tool()
def refresh_browser_cookies(browser: str = "auto") -> str:
    """
    Extract and save session cookies from your web browser.

    Use this tool when authentication fails and you need to refresh your
    MyFitnessPal session. You must be logged into myfitnesspal.com in the
    target browser.

    Args:
        browser: Source to extract cookies from. Options:
                 - 'auto' (default): scan every installed Chromium-based
                   browser on macOS (Arc, Chrome, Edge, Brave, Vivaldi,
                   Opera, ...) and use the first one with a valid session.
                 - 'arc', 'chrome', 'chromium', 'edge', 'brave', 'vivaldi',
                   'opera': force a specific Chromium browser (macOS).
                 - 'firefox': use browser_cookie3 (Firefox is not Chromium).

    Returns:
        Success message or error description.
    """
    browser_key = browser.lower().strip()

    # 'auto' — discover every Chromium browser via keychain Safe Storage
    if browser_key == "auto":
        result = try_chromium_browsers_for_session_cookies()
        if not result:
            return (
                "Auto-discovery did not find a Chromium browser with a "
                "valid MyFitnessPal session. Log into myfitnesspal.com in "
                "Arc, Chrome, Edge, Brave, Vivaldi, or Opera, then retry. "
                "(macOS only — on Linux/Windows, pass 'chrome' or "
                "'firefox' instead.)"
            )
        browser_name, cookies = result
        return _verify_cookies_and_format(cookies, browser_name)

    # Explicit Chromium browser
    if browser_key in _CHROMIUM_BROWSER_ALIASES:
        canonical = _CHROMIUM_BROWSER_ALIASES[browser_key]
        if sys.platform == "darwin":
            service_name = f"{canonical} Safe Storage"
            cookies = _try_extract_from_chromium_browser(service_name)
            if cookies is None:
                return (
                    f"Could not read cookies from {canonical}. Make sure "
                    "the browser is installed and you have logged in at "
                    "least once."
                )
            return _verify_cookies_and_format(cookies, canonical)
        # Non-macOS: keychain-based path doesn't apply. browser_cookie3
        # handles chrome/chromium on Linux/Windows via their default
        # profile paths; other Chromium browsers aren't supported there.
        if browser_key in ("chrome", "chromium"):
            try:
                import browser_cookie3

                cj = browser_cookie3.chrome(domain_name=".myfitnesspal.com")
                cookies = {c.name: c.value for c in cj}
            except Exception as e:
                return f"Error extracting cookies from {browser_key}: {e}"
            return _verify_cookies_and_format(cookies, browser_key)
        return (
            f"{canonical} cookie extraction requires macOS (keychain-backed "
            f"Safe Storage). On this platform, use 'chrome' or 'firefox'."
        )

    # Firefox via browser_cookie3 (it has its own format, not Chromium)
    if browser_key == "firefox":
        try:
            import browser_cookie3

            cj = browser_cookie3.firefox(domain_name=".myfitnesspal.com")
            cookies = {c.name: c.value for c in cj}
        except Exception as e:
            return f"Error extracting cookies from firefox: {e}"
        return _verify_cookies_and_format(cookies, "firefox")

    valid_options = sorted({*_CHROMIUM_BROWSER_ALIASES, "firefox", "auto"})
    return (
        f"Unsupported browser: {browser!r}. Use 'auto' to scan all installed "
        f"Chromium browsers, or one of: {', '.join(valid_options)}."
    )
