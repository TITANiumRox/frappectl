import httpx
import pytest
import respx

from frappectl.session_login import SessionLoginError, login, logout

BASE = "http://localhost"


@respx.mock
def test_login_returns_sid_from_cookie():
    respx.post(f"{BASE}/api/method/login").mock(
        return_value=httpx.Response(
            200,
            headers={"Set-Cookie": "sid=abc123; Path=/"},
            json={"message": "Logged In"},
        )
    )
    assert login(BASE, "alice", "secret") == "abc123"


@respx.mock
def test_login_sends_credentials_as_form():
    route = respx.post(f"{BASE}/api/method/login").mock(
        return_value=httpx.Response(
            200,
            headers={"Set-Cookie": "sid=xyz; Path=/"},
            json={"message": "Logged In"},
        )
    )
    login(BASE, "alice", "s3cr3t")
    body = route.calls.last.request.content.decode()
    assert "usr=alice" in body
    assert "pwd=s3cr3t" in body


@respx.mock
def test_login_bad_credentials_raises_with_message():
    respx.post(f"{BASE}/api/method/login").mock(
        return_value=httpx.Response(401, json={"message": "Invalid login credentials"})
    )
    with pytest.raises(SessionLoginError) as exc:
        login(BASE, "alice", "wrong")
    assert "Invalid login credentials" in str(exc.value)


@respx.mock
def test_login_success_without_sid_raises():
    respx.post(f"{BASE}/api/method/login").mock(
        return_value=httpx.Response(200, json={"message": "Logged In"})
    )
    with pytest.raises(SessionLoginError):
        login(BASE, "alice", "secret")


def test_login_refuses_plain_http_remote():
    with pytest.raises(SessionLoginError):
        login("http://remote.example.com", "alice", "secret")


@respx.mock
def test_logout_sends_sid_cookie():
    route = respx.get(f"{BASE}/api/method/logout").mock(
        return_value=httpx.Response(200)
    )
    logout(BASE, "abc123")
    assert route.called
    assert "sid=abc123" in route.calls.last.request.headers.get("cookie", "")


@respx.mock
def test_logout_swallows_transport_errors():
    respx.get(f"{BASE}/api/method/logout").mock(side_effect=httpx.ConnectError("boom"))
    logout(BASE, "abc123")  # must not raise


def test_logout_noop_without_sid():
    logout(BASE, "")  # must not raise or make a request
