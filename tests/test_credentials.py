import pytest

from frappectl.config import OAuthCredential, PasswordCredential
from frappectl.credentials import (
    ApiKeyProvider,
    CredentialRefreshError,
    OAuthProvider,
    SessionProvider,
)


def credential(access="AT", refresh="RT", expires=200, client_id="client"):
    return OAuthCredential(access, refresh, expires, client_id)


def test_api_key_provider_never_refreshes():
    provider = ApiKeyProvider("key", "secret")
    assert provider.auth_headers() == {"Authorization": "token key:secret"}
    assert provider.refresh() is False


def test_oauth_provider_refreshes_before_expiry_and_persists():
    persisted = []
    provider = OAuthProvider(
        "https://site.test",
        credential(expires=100),
        lambda site, client_id, refresh: credential("AT2", "RT2", 500, client_id),
        persisted.append,
        clock=lambda: 50,
    )

    assert provider.auth_headers() == {"Authorization": "Bearer AT2"}
    assert persisted == [credential("AT2", "RT2", 500)]


def test_oauth_provider_keeps_refreshed_token_when_persistence_fails():
    def fail_persist(_credential):
        raise OSError("keyring unavailable")

    provider = OAuthProvider(
        "https://site.test",
        credential(expires=500),
        lambda site, client_id, refresh: credential("AT2", "RT2", 600, client_id),
        fail_persist,
        clock=lambda: 0,
    )

    assert provider.refresh() is True
    assert provider.auth_headers() == {"Authorization": "Bearer AT2"}


def test_oauth_provider_hides_refresh_failure_details():
    def fail_refresh(site, client_id, refresh):
        raise RuntimeError(f"server rejected {refresh}")

    provider = OAuthProvider(
        "https://site.test",
        credential(expires=10),
        fail_refresh,
        lambda value: None,
        clock=lambda: 10,
    )

    with pytest.raises(CredentialRefreshError) as exc:
        provider.auth_headers()
    assert "RT" not in str(exc.value)


def password_credential(user="alice", pwd="secret", sid="SID"):
    return PasswordCredential(user, pwd, sid)


def test_session_provider_uses_cached_sid_without_logging_in():
    def fail_login(site, user, pwd):
        raise AssertionError("must not log in when a sid is cached")

    provider = SessionProvider(
        "https://site.test",
        password_credential(sid="CACHED"),
        fail_login,
        lambda c: None,
    )
    assert provider.auth_headers() == {"Cookie": "sid=CACHED"}


def test_session_provider_logs_in_when_no_sid_and_persists():
    persisted = []
    provider = SessionProvider(
        "https://site.test",
        password_credential(sid=""),
        lambda site, user, pwd: "FRESH",
        persisted.append,
    )
    assert provider.auth_headers() == {"Cookie": "sid=FRESH"}
    assert persisted == [password_credential(sid="FRESH")]


def test_session_provider_refresh_relogins_and_persists():
    persisted = []
    provider = SessionProvider(
        "https://site.test",
        password_credential(sid="STALE"),
        lambda site, user, pwd: "RENEWED",
        persisted.append,
    )
    assert provider.refresh() is True
    assert provider.auth_headers() == {"Cookie": "sid=RENEWED"}
    assert persisted == [password_credential(sid="RENEWED")]


def test_session_provider_refresh_returns_false_on_failure():
    def fail_login(site, user, pwd):
        raise RuntimeError("invalid credentials")

    provider = SessionProvider(
        "https://site.test",
        password_credential(sid="STALE"),
        fail_login,
        lambda c: None,
    )
    assert provider.refresh() is False


def test_session_provider_initial_login_failure_hides_password():
    def fail_login(site, user, pwd):
        raise RuntimeError(f"rejected {pwd}")

    provider = SessionProvider(
        "https://site.test",
        password_credential(sid=""),
        fail_login,
        lambda c: None,
    )
    with pytest.raises(CredentialRefreshError) as exc:
        provider.auth_headers()
    assert "secret" not in str(exc.value)


def test_session_provider_keeps_sid_when_persistence_fails():
    def fail_persist(_credential):
        raise OSError("keyring unavailable")

    provider = SessionProvider(
        "https://site.test",
        password_credential(sid=""),
        lambda site, user, pwd: "FRESH",
        fail_persist,
    )
    assert provider.auth_headers() == {"Cookie": "sid=FRESH"}
