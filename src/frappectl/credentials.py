"""Credential providers consumed by the HTTP transport."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from .config import OAuthCredential, PasswordCredential

OAUTH_EXPIRY_MARGIN = 60.0


class CredentialProvider(Protocol):
    def auth_headers(self) -> dict[str, str]: ...

    def refresh(self) -> bool: ...


class CredentialRefreshError(Exception):
    """Raised when an expiring credential cannot be refreshed safely."""


class ApiKeyProvider:
    def __init__(self, api_key: str, api_secret: str):
        self._headers = {"Authorization": f"token {api_key}:{api_secret}"}

    def auth_headers(self) -> dict[str, str]:
        return dict(self._headers)

    def refresh(self) -> bool:
        return False


class OAuthProvider:
    """Own OAuth token rotation, refresh timing, and persistence."""

    def __init__(
        self,
        site: str,
        credential: OAuthCredential,
        refresh_service: Callable[[str, str, str], OAuthCredential],
        persist: Callable[[OAuthCredential], None],
        *,
        clock: Callable[[], float] = time.time,
    ):
        self._site = site
        self._credential = credential
        self._refresh_service = refresh_service
        self._persist = persist
        self._clock = clock

    def auth_headers(self) -> dict[str, str]:
        if (
            self._credential.refresh_token
            and self._credential.expires_at
            and self._credential.expires_at - OAUTH_EXPIRY_MARGIN <= self._clock()
        ):
            if not self._refresh(raise_on_failure=True):
                raise CredentialRefreshError("Could not refresh the OAuth session.")
        return {"Authorization": f"Bearer {self._credential.access_token}"}

    def refresh(self) -> bool:
        return self._refresh(raise_on_failure=False)

    def _refresh(self, *, raise_on_failure: bool) -> bool:
        if not self._credential.refresh_token:
            return False
        try:
            credential = self._refresh_service(
                self._site,
                self._credential.client_id,
                self._credential.refresh_token,
            )
        except Exception as e:
            if raise_on_failure:
                raise CredentialRefreshError(
                    "Could not refresh the OAuth session. Log in again."
                ) from e
            return False
        if not credential.refresh_token:
            credential = OAuthCredential(
                credential.access_token,
                self._credential.refresh_token,
                credential.expires_at,
                credential.client_id,
            )
        self._credential = credential
        try:
            self._persist(credential)
        except Exception:
            # The fresh token is still usable for this process. A broken
            # keyring must not turn a successful refresh into a failed request.
            pass
        return True


class SessionProvider:
    """Own username/password session auth: a ``sid`` cookie renewed by re-login.

    Unlike OAuth there is no refresh token — the stored password is replayed
    against ``/api/method/login`` to mint a new session whenever the cached
    ``sid`` is missing or the server rejects it with a 401.
    """

    def __init__(
        self,
        site: str,
        credential: PasswordCredential,
        login_service: Callable[[str, str, str], str],
        persist: Callable[[PasswordCredential], None],
    ):
        self._site = site
        self._credential = credential
        self._login_service = login_service
        self._persist = persist

    def auth_headers(self) -> dict[str, str]:
        if not self._credential.sid:
            if not self._login(raise_on_failure=True):
                raise CredentialRefreshError("Could not start a session.")
        return {"Cookie": f"sid={self._credential.sid}"}

    def refresh(self) -> bool:
        return self._login(raise_on_failure=False)

    def _login(self, *, raise_on_failure: bool) -> bool:
        try:
            sid = self._login_service(
                self._site, self._credential.username, self._credential.password
            )
        except Exception as e:
            if raise_on_failure:
                raise CredentialRefreshError(
                    "Could not log in to start a session. Log in again."
                ) from e
            return False
        self._credential = PasswordCredential(
            self._credential.username, self._credential.password, sid
        )
        try:
            self._persist(self._credential)
        except Exception:
            # The fresh sid is still usable for this process; a broken keyring
            # must not turn a successful login into a failed request.
            pass
        return True
