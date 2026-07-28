"""Authenticated MyFitnessPal client orchestration."""

import logging
from datetime import date

from .browser_cookies import (
    _has_real_mfp_session,
    try_chromium_browsers_for_session_cookies,
)
from .cookie_store import dict_to_cookiejar, load_cookies, save_cookies
from .credentials import (
    authenticate_with_credentials,
    get_decrypted_credential,
)

logger = logging.getLogger("mfp_mcp")


def get_mfp_client():
    """
    Get an authenticated MyFitnessPal client.

    Authentication is attempted in this order:
    1. Environment variables (MFP_USERNAME, MFP_PASSWORD)
       a. First tries previously-cached cookies for this user.
       b. Then falls back to form login (only useful on legacy accounts).
    2. Stored session cookies (~/.mfp_mcp/cookies.json)
    3. Chromium-based browser cookies (macOS): auto-discovers Arc, Chrome,
       Edge, Brave, Vivaldi, Opera, or any other installed Chromium browser
       via the keychain's "Safe Storage" entries.
    4. `browser_cookie3` default fallback (legacy Chrome/Firefox paths).

    Returns:
        myfitnesspal.Client: Authenticated client instance

    Raises:
        RuntimeError: If all authentication methods fail
    """
    import myfitnesspal

    last_error = None

    # Method 1: Try environment variable credentials
    username = get_decrypted_credential("MFP_USERNAME")
    password = get_decrypted_credential("MFP_PASSWORD")

    if username and password:
        logger.info("Attempting authentication with environment credentials")

        # First check if we have valid stored cookies from a previous credential auth
        stored_cookies = load_cookies()
        if stored_cookies:
            logger.info("Found stored session cookies, testing validity...")
            try:
                cookiejar = dict_to_cookiejar(stored_cookies)
                client = myfitnesspal.Client(cookiejar=cookiejar)
                # Test the connection
                _ = client.get_date(date.today())
                logger.info("Stored cookies are valid")
                return client
            except Exception as e:
                logger.info(f"Stored cookies invalid: {e}, re-authenticating...")

        # Authenticate with credentials and save cookies
        try:
            cookies = authenticate_with_credentials(username, password)
            save_cookies(cookies)

            # Create client with the new cookies
            cookiejar = dict_to_cookiejar(cookies)
            client = myfitnesspal.Client(cookiejar=cookiejar)
            # Test the connection
            _ = client.get_date(date.today())
            logger.info("Successfully authenticated with credentials")
            return client

        except Exception as e:
            last_error = e
            logger.warning(f"Credential authentication failed: {e}")
            # Fall through to other methods

    # Method 2: Try stored session cookies (without credential auth)
    stored_cookies = load_cookies()
    if stored_cookies:
        logger.info("Attempting authentication with stored cookies")
        try:
            cookiejar = dict_to_cookiejar(stored_cookies)
            client = myfitnesspal.Client(cookiejar=cookiejar)
            # Test the connection
            _ = client.get_date(date.today())
            logger.info("Successfully authenticated with stored cookies")
            return client
        except Exception as e:
            last_error = e
            logger.warning(f"Stored cookie authentication failed: {e}")

    # Method 3: Auto-discover Chromium-based browsers (macOS) and pull a live
    # session from whichever one is logged into MFP. This works for Arc,
    # Chrome, Edge, Brave, Vivaldi, Opera, etc. — anything that registers a
    # "<Browser> Safe Storage" entry in the macOS keychain.
    logger.info("Attempting authentication via Chromium browser auto-discovery")
    try:
        result = try_chromium_browsers_for_session_cookies()
        if result:
            browser_name, chromium_cookies = result
            cookiejar = dict_to_cookiejar(chromium_cookies)
            client = myfitnesspal.Client(cookiejar=cookiejar)
            _ = client.get_date(date.today())
            # Only persist after we've verified it works, so a transient
            # failure can't poison cookies.json.
            save_cookies(chromium_cookies)
            logger.info(f"Successfully authenticated via Chromium auto-discovery ({browser_name})")
            return client
        logger.info("No Chromium browser had a usable MFP session")
    except Exception as e:
        last_error = e
        logger.warning(f"Chromium auto-discovery authentication failed: {e}")

    # Method 4: Try browser cookies via browser_cookie3 (legacy fallback)
    logger.info("Attempting authentication with browser_cookie3 fallback")
    try:
        client = myfitnesspal.Client()
        # Test the connection
        _ = client.get_date(date.today())
        logger.info("Successfully authenticated with browser cookies")
        return client
    except Exception as e:
        last_error = e
        raise RuntimeError(
            f"All authentication methods failed. Last error: {last_error!s}\n\n"
            "Please try one of these solutions:\n"
            "1. Log into myfitnesspal.com in any Chromium-based browser "
            "(Arc, Chrome, Edge, Brave, Vivaldi, Opera, ...) — the MCP will "
            "auto-discover the session on macOS.\n"
            "2. Set MFP_USERNAME and MFP_PASSWORD in Claude Desktop config "
            "(legacy form-login flow; rarely works against the current "
            "NextAuth backend).\n"
            "3. Manually populate ~/.mfp_mcp/cookies.json with a valid "
            "session token."
        )


def _verify_cookies_and_format(cookies: dict[str, str], source: str) -> str:
    """Verify cookies via a live MFP round-trip, then persist on success.

    Persisting only after verification matches the auto-discovery path's
    anti-poisoning behavior — a stale/expired session can't clobber a
    previously good `cookies.json`.
    """
    if not _has_real_mfp_session(cookies):
        return (
            f"No MyFitnessPal session token found in {source}. "
            "Make sure you are logged into myfitnesspal.com in that browser, "
            "then try again."
        )
    try:
        import myfitnesspal

        cookiejar = dict_to_cookiejar(cookies)
        client = myfitnesspal.Client(cookiejar=cookiejar)
        _ = client.get_date(date.today())
    except Exception as e:
        return (
            f"Cookies were extracted from {source} but verification failed: "
            f"{e}. The session may have expired — log in again and retry. "
            f"(cookies.json was NOT overwritten.)"
        )
    save_cookies(cookies)
    return (
        f"Successfully extracted and verified {len(cookies)} cookies "
        f"from {source}. Authentication is now working."
    )
