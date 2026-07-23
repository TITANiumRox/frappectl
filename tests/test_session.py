import httpx
import respx

from frappectl import session_login
from frappectl.session import ClientFactory

BASE = "http://localhost"


@respx.mock
def test_password_profile_client_sends_sid_cookie(fake_config):
    fake_config.add_password_profile("acme", BASE, "alice", "secret", sid="CACHED")
    route = respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X"}})
    )
    client = ClientFactory().create("acme", interactive=False, debug=False)
    client.get_document("ToDo", "X")
    assert "sid=CACHED" in route.calls.last.request.headers.get("cookie", "")


@respx.mock
def test_password_profile_relogins_on_401_and_persists(fake_config, monkeypatch):
    fake_config.add_password_profile("acme", BASE, "alice", "secret", sid="STALE")
    monkeypatch.setattr(session_login, "login", lambda site, user, pwd: "FRESH")
    route = respx.get(f"{BASE}/api/v2/document/ToDo/X/")
    route.side_effect = [
        httpx.Response(401, json={"errors": [{"message": "auth"}]}),
        httpx.Response(200, json={"data": {"name": "X"}}),
    ]
    client = ClientFactory().create("acme", interactive=False, debug=False)
    assert client.get_document("ToDo", "X") == {"name": "X"}
    assert "sid=FRESH" in route.calls[-1].request.headers.get("cookie", "")
    assert fake_config.password_sid("acme") == "FRESH"
