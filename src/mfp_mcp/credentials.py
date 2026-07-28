"""Credential decryption and legacy form authentication."""

import logging
import os

import httpx
import keyring
from cryptography.fernet import Fernet

from .browser_cookies import _has_real_mfp_session

logger = logging.getLogger("mfp_mcp")
KEYRING_SERVICE = "mfp-mcp"
KEYRING_SECRET_KEY_ACCOUNT = "MFP_SECRET_KEY"


def looks_like_fernet_token(value: str) -> bool:
    """Return True if the value appears to be a Fernet token."""
    if not value:
        return False
    # Fernet tokens are URL-safe base64-encoded and typically begin with "gAAAAA".
    # Use a lightweight prefix check so plaintext credentials continue to work
    # even when MFP_SECRET_KEY is configured.
    #
    # Edge case: a genuine plaintext password that starts with "gAAAAA" is
    # misclassified as a ciphertext, fails Fernet decryption, and
    # `get_decrypted_credential` returns None — credential auth is silently
    # skipped and the caller falls through to cookie-based auth. Extremely
    # unlikely for a real password, and the fallback is graceful, so we
    # accept this heuristic rather than adding a length/base64 check.
    return value.startswith("gAAAAA")


def get_secret_key() -> str | None:
    """Resolves MFP_SECRET_KEY from, in order:
    1. The MFP_SECRET_KEY environment variable.
    2. The OS keychain (service: 'mfp-mcp', account: 'MFP_SECRET_KEY').

    Returns the key string, or None if not found in either location.
    """
    key = os.environ.get("MFP_SECRET_KEY")
    if key:
        logger.info("MFP_SECRET_KEY loaded from environment variable.")
        return key

    try:
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_SECRET_KEY_ACCOUNT)
        if key:
            logger.info("MFP_SECRET_KEY loaded from OS keychain.")
            return key
    except Exception as e:
        logger.warning(f"Keychain lookup failed: {e}")

    return None


def get_decrypted_credential(env_var_name: str) -> str | None:
    """Retrieves credentials from environment variables, decrypting Fernet tokens when needed.

    The decryption key (MFP_SECRET_KEY) is resolved from the environment variable first,
    then from the OS keychain as a fallback.

    Returns the decrypted string on success, the raw value when no decryption is needed,
    or None if the env var is missing, decryption fails, or the value looks encrypted but
    no key is available (to avoid passing ciphertext to the auth flow).
    """
    encrypted_value = os.environ.get(env_var_name)

    if not encrypted_value:
        logger.warning(f"Missing {env_var_name}.")
        return None

    if not looks_like_fernet_token(encrypted_value):
        # Plain-text credential — no key lookup needed.
        return encrypted_value

    # Value looks like a Fernet token; resolve the secret key only now.
    secret_key = get_secret_key()

    if not secret_key:
        logger.warning(
            f"{env_var_name} appears to be encrypted but MFP_SECRET_KEY is not set. "
            "Credential auth will be skipped to avoid passing ciphertext to the auth flow."
        )
        return None

    try:
        f = Fernet(secret_key.encode())
        return f.decrypt(encrypted_value.encode()).decode()
    except Exception as e:
        logger.error(f"Decryption failed for {env_var_name}: {e}")
        return None


def authenticate_with_credentials(username: str, password: str) -> dict[str, str]:
    """
    Authenticate with MyFitnessPal using username/password.

    Args:
        username: MyFitnessPal username or email
        password: MyFitnessPal password

    Returns:
        Dictionary of session cookies

    Raises:
        RuntimeError: If authentication fails
    """
    # Log authentication attempt without exposing the username
    logger.info("Authenticating with credentials")

    # MyFitnessPal login URL and endpoints
    LOGIN_URL = "https://www.myfitnesspal.com/account/login"

    try:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            # First, get the login page to obtain CSRF token
            response = client.get(LOGIN_URL)
            response.raise_for_status()

            # Attempt login
            login_data = {
                "username": username,
                "password": password,
            }

            # Try the standard form login
            client.post(
                LOGIN_URL,
                data=login_data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": LOGIN_URL,
                },
            )

            # MyFitnessPal moved to a NextAuth backend, so the legacy form
            # POST flow this function uses no longer actually logs the user
            # in — the endpoint just returns HTTP 200 with a fresh CSRF
            # cookie. The old success check matched any cookie containing
            # 'auth' (which `__Host-next-auth.csrf-token` does), reporting
            # success and overwriting cookies.json with useless pre-auth
            # cookies. Require a real session token before claiming success.
            all_cookies = dict(client.cookies)
            if _has_real_mfp_session(all_cookies):
                logger.info("Successfully authenticated with credentials")
                return all_cookies
            raise RuntimeError(
                "Login appeared to fail — response contained no session token. "
                "MyFitnessPal's form login flow does not work against the "
                "current NextAuth backend. Log into myfitnesspal.com in any "
                "Chromium-based browser (Arc, Chrome, Edge, Brave, ...) and "
                "the MCP will pick up the session automatically."
            )

    except httpx.HTTPError as e:
        raise RuntimeError(f"HTTP error during authentication: {e}")
    except Exception as e:
        raise RuntimeError(f"Authentication failed: {e}")
