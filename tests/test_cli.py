import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from frappectl.cli import _hoist_globals, app
from frappectl.client import FrappeClient
from frappectl.commands import auth
from frappectl.commands import guide as guide_cmd
from frappectl.errors import FrappeError

BASE = "http://localhost"
runner = CliRunner()


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["doc", "list", "ToDo", "--json"], ["--json", "doc", "list", "ToDo"]),
        (["-s", "raven", "doc", "list", "X"], ["--site", "raven", "doc", "list", "X"]),
        (["doc", "list", "X", "-s", "raven"], ["--site", "raven", "doc", "list", "X"]),
        (["--site=acme", "doc", "list", "X"], ["--site", "acme", "doc", "list", "X"]),
        (["api", "p", "--", "--json"], ["api", "p", "--", "--json"]),
        (
            ["doc", "list", "X", "--filters-json", "[]"],
            ["doc", "list", "X", "--filters-json", "[]"],
        ),
    ],
)
def test_hoist_globals(argv, expected):
    assert _hoist_globals(argv) == expected


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("FRAPPE_SITE", BASE)
    monkeypatch.setenv("FRAPPE_API_KEY", "k")
    monkeypatch.setenv("FRAPPE_API_SECRET", "s")


@respx.mock
def test_doc_get_json(env):
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X", "status": "Open"}})
    )
    result = runner.invoke(app, ["--json", "doc", "get", "ToDo", "X"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"name": "X", "status": "Open"}


@respx.mock
def test_doc_list_meta_driven_fields(env):
    respx.get(f"{BASE}/api/v2/doctype/ToDo/meta").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "title_field": "description",
                    "fields": [
                        {
                            "fieldname": "status",
                            "fieldtype": "Select",
                            "in_list_view": 1,
                        }
                    ],
                }
            },
        )
    )
    list_route = respx.get(f"{BASE}/api/v2/document/ToDo").mock(
        return_value=httpx.Response(
            200, json={"data": [{"name": "a"}], "has_next_page": False}
        )
    )
    result = runner.invoke(app, ["--json", "doc", "list", "ToDo"])
    assert result.exit_code == 0
    assert "fields" in list_route.calls.last.request.url.params
    fields = list_route.calls.last.request.url.params["fields"]
    assert "description" in fields and "status" in fields
    assert list_route.calls.last.request.url.params["order_by"] == "creation desc"


@respx.mock
def test_delete_runs_without_confirmation(env):
    route = respx.delete(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    result = runner.invoke(app, ["--json", "doc", "delete", "ToDo", "X"])
    assert result.exit_code == 0
    assert route.called
    assert json.loads(result.stdout)["deleted"] == "X"


@respx.mock
def test_update_threads_modified(env):
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(
            200, json={"data": {"name": "X", "modified": "2026-01-01 00:00:00"}}
        )
    )
    patch_route = respx.patch(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X"}})
    )
    result = runner.invoke(
        app, ["--json", "doc", "update", "ToDo", "X", "--set", "status=Closed"]
    )
    assert result.exit_code == 0
    import json

    sent = json.loads(patch_route.calls.last.request.content)
    assert sent["modified"] == "2026-01-01 00:00:00"
    assert sent["status"] == "Closed"


@respx.mock
def test_update_force_skips_modified(env):
    patch_route = respx.patch(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(200, json={"data": {"name": "X"}})
    )
    result = runner.invoke(
        app,
        ["--json", "doc", "update", "ToDo", "X", "--set", "status=Closed", "--force"],
    )
    assert result.exit_code == 0
    import json

    sent = json.loads(patch_route.calls.last.request.content)
    assert "modified" not in sent


@respx.mock
def test_conflict_message(env):
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(
            200, json={"data": {"name": "X", "modified": "2026-01-01 00:00:00"}}
        )
    )
    respx.patch(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(
            409,
            json={
                "errors": [
                    {"message": "Document has been modified after you have opened it"}
                ]
            },
        )
    )
    result = runner.invoke(
        app, ["--json", "doc", "update", "ToDo", "X", "--set", "status=Closed"]
    )
    assert result.exit_code == 1
    assert "modified since you read it" in result.stderr


def test_guide_runs_without_auth():
    result = runner.invoke(app, ["guide"])
    assert result.exit_code == 0
    assert "frappectl doctype show" in result.stdout
    assert "frappectl api" in result.stdout


def test_guide_teaches_agents_to_configure_access():
    result = runner.invoke(app, ["guide"])
    assert result.exit_code == 0
    assert "Do not run auth commands" not in result.stdout
    assert "frappectl auth configure" in result.stdout
    assert "frappectl auth login <url> --oauth" in result.stdout


def test_guide_lists_configured_sites(monkeypatch):
    monkeypatch.setattr(
        guide_cmd.config,
        "list_profiles",
        lambda: (
            {
                "staging": {"site": "https://staging.example.com", "read_only": True},
                "prod": {"site": "https://prod.example.com", "description": "live"},
            },
            "prod",
        ),
    )
    result = runner.invoke(app, ["guide"])
    assert result.exit_code == 0
    assert "staging: https://staging.example.com [read-only]" in result.stdout
    assert "prod: https://prod.example.com — live [default]" in result.stdout


def test_guide_without_profiles_still_renders(monkeypatch):
    monkeypatch.setattr(guide_cmd.config, "list_profiles", lambda: ({}, None))
    result = runner.invoke(app, ["guide"])
    assert result.exit_code == 0
    assert "(none configured" in result.stdout
    assert "<<SITES>>" not in result.stdout


def test_login_refuses_non_interactive():
    result = runner.invoke(
        app, ["auth", "login", "https://erp.example.com"], input="key\nsecret\n"
    )
    assert result.exit_code == 2
    assert "interactive only" in result.stderr
    assert "FRAPPE_API_SECRET" in result.stderr


def test_login_defaults_authentication_choice_to_oauth(monkeypatch):
    prompted = {}

    def prompt(message, *, default):
        prompted.update(message=message, default=default)
        return default

    monkeypatch.setattr(auth.typer, "prompt", prompt)

    assert auth._choose_auth_method(False, False) == "oauth"
    assert prompted == {
        "message": "Authentication method [oauth/api-key/password]",
        "default": "oauth",
    }


def test_login_can_select_api_key_authentication(monkeypatch):
    monkeypatch.setattr(auth.typer, "prompt", lambda *args, **kwargs: "api-key")

    assert auth._choose_auth_method(False, False) == "api-key"


def test_login_can_select_password_authentication(monkeypatch):
    monkeypatch.setattr(auth.typer, "prompt", lambda *args, **kwargs: "password")

    assert auth._choose_auth_method(False, False) == "password"


def _unexpected_prompt(*args, **kwargs):
    raise AssertionError("an auth-method flag should skip the prompt")


def test_oauth_flag_skips_authentication_choice(monkeypatch):
    monkeypatch.setattr(auth.typer, "prompt", _unexpected_prompt)

    assert auth._choose_auth_method(True, False) == "oauth"


def test_password_flag_skips_authentication_choice(monkeypatch):
    monkeypatch.setattr(auth.typer, "prompt", _unexpected_prompt)

    assert auth._choose_auth_method(False, True) == "password"


def test_oauth_and_password_flags_conflict():
    import typer

    with pytest.raises(typer.Exit):
        auth._choose_auth_method(True, True)


@respx.mock
def test_password_login_stores_profile_and_caches_sid(fake_config, monkeypatch):
    prompts = iter(["alice", "s3cr3t"])
    monkeypatch.setattr(auth.typer, "prompt", lambda *a, **k: next(prompts))
    monkeypatch.setattr(auth.session_login, "login", lambda site, u, p: "SID123")
    respx.get(f"{BASE}/api/v2/method/frappe.auth.get_logged_user").mock(
        return_value=httpx.Response(200, json={"data": "alice@example.com"})
    )

    auth._login_password("acme", BASE, set_default=True, description="", read_only=True)

    profiles, default = fake_config.list_profiles()
    assert profiles["acme"]["auth"] == "password"
    assert profiles["acme"]["read_only"] is True
    assert default == "acme"
    assert fake_config.password_sid("acme") == "SID123"
    text = fake_config.config_path().read_text()
    assert "s3cr3t" not in text
    assert "SID123" not in text


def test_logout_ends_password_session(fake_config, monkeypatch):
    fake_config.add_password_profile(
        "acme", "http://acme.test", "alice", "secret", sid="SID9"
    )
    called = {}
    monkeypatch.setattr(
        auth.session_login,
        "logout",
        lambda site, sid: called.update(site=site, sid=sid),
    )
    result = runner.invoke(app, ["auth", "logout", "acme"])
    assert result.exit_code == 0
    assert called == {"site": "http://acme.test", "sid": "SID9"}
    assert "acme" not in fake_config.list_profiles()[0]


@respx.mock
def test_whoami_reports_password_auth(fake_config):
    import json

    fake_config.add_password_profile("acme", BASE, "alice", "secret", sid="SIDW")
    respx.get(f"{BASE}/api/v2/method/frappe.auth.get_logged_user").mock(
        return_value=httpx.Response(200, json={"data": "alice@example.com"})
    )
    result = runner.invoke(app, ["--json", "auth", "whoami"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["auth"] == "password"
    assert data["user"] == "alice@example.com"


@respx.mock
def test_password_login_reports_bad_credentials(fake_config, monkeypatch):
    import typer

    prompts = iter(["alice", "wrong"])
    monkeypatch.setattr(auth.typer, "prompt", lambda *a, **k: next(prompts))

    def bad_login(site, u, p):
        raise auth.session_login.SessionLoginError("Invalid login credentials")

    monkeypatch.setattr(auth.session_login, "login", bad_login)

    with pytest.raises(typer.Exit):
        auth._login_password(
            "acme", BASE, set_default=True, description="", read_only=True
        )
    assert "acme" not in fake_config.list_profiles()[0]


def test_login_defaults_new_profile_to_read_only(monkeypatch):
    prompted = {}

    def confirm(message, *, default):
        prompted.update(message=message, default=default)
        return default

    monkeypatch.setattr(auth.typer, "confirm", confirm)

    assert auth._choose_read_only(None) is True
    assert prompted == {
        "message": "Allow writes through this profile?",
        "default": False,
    }


def test_login_writable_flag_overrides_read_only_default(monkeypatch):
    def unexpected_confirm(*args, **kwargs):
        raise AssertionError("--writable should skip the read-only prompt")

    monkeypatch.setattr(auth.typer, "confirm", unexpected_confirm)

    assert auth._choose_read_only(False) is False


@respx.mock
def test_get_logged_user_only_trusts_non_guest_data():
    route = respx.get(f"{BASE}/api/v2/method/frappe.auth.get_logged_user")
    with FrappeClient(BASE, "k:s") as client:
        route.mock(return_value=httpx.Response(200, json={"data": "user@example.com"}))
        assert client.get_logged_user() == "user@example.com"

        route.mock(return_value=httpx.Response(200, json={"data": "Guest"}))
        with pytest.raises(FrappeError):
            client.get_logged_user()

        route.mock(return_value=httpx.Response(200, text="<html>login</html>"))
        with pytest.raises(FrappeError):
            client.get_logged_user()


def test_configure_renames_and_describes(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    result = runner.invoke(
        app,
        ["auth", "configure", "acme", "--name", "prod", "--description", "billing box"],
    )
    assert result.exit_code == 0
    profiles, default = fake_config.list_profiles()
    assert "acme" not in profiles
    assert profiles["prod"]["description"] == "billing box"
    assert default == "prod"


def test_configure_unknown_profile_errors(fake_config):
    result = runner.invoke(app, ["auth", "configure", "ghost", "--name", "x"])
    assert result.exit_code == 2
    assert "No such profile" in result.stderr


def test_configure_no_flags_non_interactive_errors(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s")
    result = runner.invoke(app, ["auth", "configure", "acme"])
    assert result.exit_code == 2
    assert "Nothing to change" in result.stderr


def test_configure_clears_description(fake_config):
    fake_config.add_profile("acme", "http://acme.test", "k", "s", description="old")
    result = runner.invoke(app, ["auth", "configure", "acme", "--description", ""])
    assert result.exit_code == 0
    assert "description" not in fake_config.list_profiles()[0]["acme"]


def test_list_shows_description(fake_config):
    fake_config.add_profile(
        "acme", "http://acme.test", "k", "s", description="prod erp"
    )
    result = runner.invoke(app, ["--json", "auth", "list"])
    assert result.exit_code == 0
    assert "prod erp" in result.stdout


@respx.mock
def test_error_includes_hint(env):
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(
            404,
            json={
                "errors": [{"type": "DoesNotExistError", "message": "ToDo X not found"}]
            },
        )
    )
    result = runner.invoke(app, ["--json", "doc", "get", "ToDo", "X"])
    assert result.exit_code == 1
    assert "not found" in result.stderr
    assert "tip:" in result.stderr
    assert "frappectl doctype list" in result.stderr


@respx.mock
def test_server_exception_nudges_debug(env):
    respx.get(f"{BASE}/api/v2/method/x.y").mock(
        return_value=httpx.Response(
            500,
            json={"errors": [{"exception": "Traceback...\nKeyError: 'z'"}]},
        )
    )
    result = runner.invoke(app, ["--json", "method", "call", "x.y", "-X", "GET"])
    assert result.exit_code == 1
    assert "--debug" in result.stderr
    assert "Traceback" not in result.stderr


@respx.mock
def test_no_debug_nudge_under_debug(env):
    respx.get(f"{BASE}/api/v2/method/x.y").mock(
        return_value=httpx.Response(
            500,
            json={"errors": [{"exception": "Traceback...\nKeyError: 'z'"}]},
        )
    )
    result = runner.invoke(
        app, ["--json", "--debug", "method", "call", "x.y", "-X", "GET"]
    )
    assert result.exit_code == 1
    assert "[server traceback]" in result.stderr
    assert "re-run with --debug" not in result.stderr


@respx.mock
def test_no_debug_nudge_for_plain_error(env):
    respx.get(f"{BASE}/api/v2/document/ToDo/X/").mock(
        return_value=httpx.Response(
            404,
            json={
                "errors": [{"type": "DoesNotExistError", "message": "ToDo X not found"}]
            },
        )
    )
    result = runner.invoke(app, ["--json", "doc", "get", "ToDo", "X"])
    assert result.exit_code == 1
    assert "--debug" not in result.stderr


@respx.mock
def test_api_method_get(env):
    respx.get(f"{BASE}/api/v2/method/frappe.client.get_count").mock(
        return_value=httpx.Response(200, json={"data": 5})
    )
    result = runner.invoke(
        app, ["api", "method/frappe.client.get_count", "-F", "doctype=User"]
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "5"


@respx.mock
def test_api_get_structured_field_json_encodes_query(env):
    route = respx.get(f"{BASE}/api/v2/method/frappe.client.get_list").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    result = runner.invoke(
        app,
        [
            "api",
            "method/frappe.client.get_list",
            "-F",
            "doctype=User",
            "-F",
            'filters:={"enabled":1}',
            "-F",
            'or_filters:=[["a","=","b"]]',
        ],
    )
    assert result.exit_code == 0, result.stderr
    q = route.calls.last.request.url.params
    # Scalars go through plainly; containers are JSON-encoded on the wire.
    assert q["doctype"] == "User"
    assert q["filters"] == '{"enabled": 1}'
    assert q["or_filters"] == '[["a", "=", "b"]]'


@respx.mock
def test_api_post_structured_field_native_json_body(env):
    route = respx.post(f"{BASE}/api/v2/method/some.method").mock(
        return_value=httpx.Response(200, json={"data": "ok"})
    )
    result = runner.invoke(
        app,
        [
            "api",
            "method/some.method",
            "-X",
            "POST",
            "-F",
            'emails:=["a@example.com","b@example.com"]',
            "-F",
            "limit=10",
        ],
    )
    assert result.exit_code == 0, result.stderr
    import json as _json

    body = _json.loads(route.calls.last.request.content)
    # Same -F, but in a body the structure is native JSON, not a string.
    assert body == {"emails": ["a@example.com", "b@example.com"], "limit": 10}


@respx.mock
def test_api_get_with_input_sends_body_keys_as_query(env):
    route = respx.get(f"{BASE}/api/v2/method/frappe.client.get_count").mock(
        return_value=httpx.Response(200, json={"data": 2})
    )
    result = runner.invoke(
        app,
        ["api", "method/frappe.client.get_count", "-X", "GET", "--input", "-"],
        input='{"doctype": "User", "filters": {"enabled": 1}}',
    )
    assert result.exit_code == 0, result.stderr
    assert route.called
    q = route.calls.last.request.url.params
    assert q["doctype"] == "User"
    assert q["filters"] == '{"enabled": 1}'


@respx.mock
def test_api_get_with_non_object_input_errors(env):
    result = runner.invoke(
        app,
        ["api", "method/frappe.ping", "-X", "GET", "--input", "-"],
        input="[1, 2, 3]",
    )
    assert result.exit_code == 2
    assert "must be a JSON object" in result.stderr
