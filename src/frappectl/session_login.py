"""Username/password session login against Frappe's ``/api/method/login``.

Separate from the OAuth and API-key paths: this mints a classic Frappe session
cookie (``sid``) that the :class:`~frappectl.credentials.SessionProvider` arms on
every request. It is the one auth path open to a user who is not a System
Manager and so cannot generate API keys or register an OAuth client.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .site import SiteURL

_HTTP_TIMEOUT = 30.0
_LOGIN_PATH = "/api/method/login"
_LOGOUT_PATH = "/api/method/logout"


class SessionLoginError(Exception):
    """A username/password login failure, reduced to a clean message."""


def _require_secure_transport(site: str) -> None:
    """Refuse a password login over plain HTTP to a remote host.

    The password is a long-lived credential — anyone who sees it owns the
    account. Plain HTTP is tolerated only for local development (localhost /
    ``*.localhost`` / loopback), mirroring the API-key and OAuth clients.
    """
    try:
        SiteURL.parse(site).require_secure_credentials()
    except ValueError:
        raise SessionLoginError(
            f"Refusing to log in to {site} over plain HTTP: your password would "
            "be sent in cleartext. Use an https:// URL (http is allowed only for "
            "local development)."
        )


def login(site: str, username: str, password: str) -> str:
    """Authenticate with username/password and return the session id (``sid``)."""
    site = site.rstrip("/")
    _require_secure_transport(site)
    url = f"{site}{_LOGIN_PATH}"
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.post(
                url,
                data={"usr": username, "pwd": password},
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as e:
        raise SessionLoginError(f"Could not reach {site} to log in: {e}") from e

    if resp.status_code >= 400:
        raise SessionLoginError(_login_error(resp))

    sid = resp.cookies.get("sid")
    if not sid:
        raise SessionLoginError(
            "Login succeeded but the server returned no session cookie."
        )
    return sid


def logout(site: str, sid: str) -> None:
    """Best-effort end of a session on logout. Never raises."""
    if not sid:
        return
    try:
        with httpx.Client(
            timeout=_HTTP_TIMEOUT, follow_redirects=True, cookies={"sid": sid}
        ) as client:
            client.get(f"{site.rstrip('/')}{_LOGOUT_PATH}")
    except httpx.HTTPError:
        # Logout must always succeed locally even if the server is unreachable.
        pass


def _login_error(resp: httpx.Response) -> str:
    message = _message_from_body(resp)
    if resp.status_code == 401:
        return message or "Invalid login credentials."
    return message or f"Login failed with HTTP {resp.status_code}."


def _message_from_body(resp: httpx.Response) -> str:
    try:
        body: Any = resp.json()
    except (json.JSONDecodeError, ValueError):
        return ""
    if isinstance(body, dict):
        message = body.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return ""
