import json
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest

from frappectl import config, session, vault
from frappectl.vault_helper import frappectl_vault as helper

ENV = {"PKEXEC_UID": "1000", "SUDO_UID": "1000"}


@pytest.fixture
def site():
    """A local Frappe-ish login endpoint: alice/right gets a sid, else 401."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = parse_qs(
                self.rfile.read(int(self.headers["Content-Length"])).decode()
            )
            if body == {"usr": ["alice"], "pwd": ["right"]}:
                self.send_response(200)
                self.send_header("Set-Cookie", "sid=SID42; Path=/")
                payload = {"message": "Logged In"}
            else:
                self.send_response(401)
                payload = {"message": "Invalid login credentials"}
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _run(root, *argv, stdin=""):
    return helper.run(list(argv), ENV, stdin, str(root))


def test_store_then_login_returns_sid_not_password(tmp_path, site):
    record = {"site": site, "username": "alice", "password": "right"}
    _run(tmp_path, "store", "acme", stdin=json.dumps(record))

    out = json.loads(_run(tmp_path, "login", "acme"))

    assert out == {"sid": "SID42", "site": site, "username": "alice"}
    assert "right" not in json.dumps(out)


def test_store_writes_private_file_in_listable_dir(tmp_path, site):
    record = {"site": site, "username": "alice", "password": "right"}
    _run(tmp_path, "store", "acme", stdin=json.dumps(record))

    entry = tmp_path / "1000" / "acme.json"
    assert stat.S_IMODE(entry.stat().st_mode) == 0o600
    assert stat.S_IMODE(entry.parent.stat().st_mode) == 0o711


def test_login_with_wrong_password_is_bad_credentials(tmp_path, site):
    record = {"site": site, "username": "alice", "password": "wrong"}
    _run(tmp_path, "store", "acme", stdin=json.dumps(record))

    with pytest.raises(helper.VaultError) as exc:
        _run(tmp_path, "login", "acme")
    assert exc.value.code == helper.EXIT_BAD_CREDENTIALS
    assert "Invalid login credentials" in str(exc.value)


def test_login_and_delete_report_missing_entry(tmp_path):
    for command in ("login", "delete"):
        with pytest.raises(helper.VaultError) as exc:
            _run(tmp_path, command, "acme")
        assert exc.value.code == helper.EXIT_NOT_FOUND


def test_delete_removes_entry(tmp_path, site):
    record = {"site": site, "username": "alice", "password": "right"}
    _run(tmp_path, "store", "acme", stdin=json.dumps(record))
    _run(tmp_path, "delete", "acme")
    assert not (tmp_path / "1000" / "acme.json").exists()


@pytest.mark.parametrize("profile", ["../x", "a/b", "..", "", "a" * 65, "a b"])
def test_rejects_unsafe_profile_names(tmp_path, profile):
    with pytest.raises(helper.VaultError) as exc:
        _run(tmp_path, "delete", profile)
    assert exc.value.code == helper.EXIT_USAGE


@pytest.mark.parametrize("url", ["http://evil.example", "ftp://x", "", "https://"])
def test_refuses_insecure_sites(tmp_path, url):
    record = {"site": url, "username": "alice", "password": "pw"}
    with pytest.raises(helper.VaultError):
        _run(tmp_path, "store", "acme", stdin=json.dumps(record))
    assert not (tmp_path / "1000").exists()


def test_requires_elevation_uid(tmp_path):
    with pytest.raises(helper.VaultError):
        helper.run(["login", "acme"], {}, "", str(tmp_path))


def test_rejects_unknown_command(tmp_path):
    with pytest.raises(helper.VaultError) as exc:
        _run(tmp_path, "read", "acme")
    assert exc.value.code == helper.EXIT_USAGE


@pytest.fixture
def fake_helper(monkeypatch):
    calls = []

    def install(returncode, stdout="", stderr="", platform="linux"):
        monkeypatch.setattr(vault, "is_installed", lambda: True)
        monkeypatch.setattr(vault.sys, "platform", platform)

        def run(argv, **kwargs):
            calls.append((argv, kwargs.get("input")))
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

        monkeypatch.setattr(vault.subprocess, "run", run)
        return calls

    return install


def test_client_login_goes_through_pkexec(fake_helper):
    calls = fake_helper(
        0, json.dumps({"sid": "S", "site": "https://a", "username": "u"})
    )
    assert vault.login("acme") == vault.VaultLogin("S", "https://a", "u")
    assert calls[0][0] == ["pkexec", vault.HELPER_PATH, "login", "acme"]


def test_client_store_pipes_record_through_sudo_on_mac(fake_helper):
    calls = fake_helper(0, platform="darwin")
    vault.store("acme", "https://a", "u", "pw")
    argv, stdin = calls[0]
    assert argv == ["sudo", "-k", vault.HELPER_PATH, "store", "acme"]
    assert json.loads(stdin) == {"site": "https://a", "username": "u", "password": "pw"}


@pytest.mark.parametrize(
    "platform,code,error",
    [
        ("linux", 126, vault.VaultCancelled),
        ("linux", 127, vault.VaultCancelled),
        ("darwin", 1, vault.VaultCancelled),
        ("linux", helper.EXIT_BAD_CREDENTIALS, vault.VaultBadCredentials),
        ("linux", helper.EXIT_NOT_FOUND, vault.VaultEntryMissing),
        ("linux", helper.EXIT_ERROR, vault.VaultError),
    ],
)
def test_client_maps_exit_codes(fake_helper, platform, code, error):
    fake_helper(code, stderr="boom", platform=platform)
    with pytest.raises(error):
        vault.login("acme")


def test_client_refuses_when_not_installed(monkeypatch):
    monkeypatch.setattr(vault, "is_installed", lambda: False)
    with pytest.raises(vault.VaultNotInstalled):
        vault.login("acme")


def test_relogin_through_vault_rejects_other_site(monkeypatch):
    monkeypatch.setattr(
        vault, "login", lambda p: vault.VaultLogin("S", "https://other.example", "u")
    )
    assert session._login_vault("acme", "https://other.example/", "u", "") == "S"
    with pytest.raises(vault.VaultError):
        session._login_vault("acme", "https://acme.example", "u", "")


def test_vault_profile_resolves_without_a_keyring_password(fake_config):
    fake_config.add_password_profile(
        "acme", "https://acme.example", "alice", "", sid="S"
    )
    assert "password" not in fake_config._read_json_blob("acme")
    creds = config.ProfileResolver().resolve("acme", interactive=True)
    assert creds.credential == config.PasswordCredential("alice", "", "S")
