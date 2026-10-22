#!/usr/bin/python3 -I
"""Root-side password vault for frappectl password profiles.

Installed root-owned at ``/usr/local/libexec/frappectl-vault`` and run only
through ``pkexec`` (Linux) or ``sudo -k`` (macOS), so every call needs the
user's fingerprint. Passwords never leave this process: ``login`` returns a
session id, never the password. Standard library only and Python 3.9
compatible, because it runs under the system interpreter as root.

Usage: frappectl-vault store|login|delete <profile>
"""

import http.cookiejar
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List

EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_BAD_CREDENTIALS = 4
EXIT_ERROR = 5

_PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_TIMEOUT = 30


class VaultError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR):
        super().__init__(message)
        self.code = code


def vault_root() -> str:
    if sys.platform == "darwin":
        return "/var/db/frappectl-vault"
    return "/var/lib/frappectl-vault"


def caller_uid(environ: Dict[str, str]) -> int:
    """The invoking user's uid, as set by the elevation tool for this platform."""
    var = "SUDO_UID" if sys.platform == "darwin" else "PKEXEC_UID"
    value = environ.get(var, "")
    if not value.isdigit():
        raise VaultError(f"Run through the elevation tool ({var} is not set).")
    return int(value)


def entry_path(root: str, uid: int, profile: str) -> str:
    if profile in {".", ".."} or not _PROFILE_RE.match(profile):
        raise VaultError(f"Invalid profile name: {profile!r}", EXIT_USAGE)
    return os.path.join(root, str(uid), f"{profile}.json")


def require_secure_site(site: str) -> str:
    parsed = urllib.parse.urlsplit(site)
    host = parsed.hostname or ""
    if parsed.scheme == "https" and host:
        return site.rstrip("/")
    if parsed.scheme == "http" and _is_local(host):
        return site.rstrip("/")
    raise VaultError(f"Refusing a non-https site: {site!r}", EXIT_USAGE)


def _is_local(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def store(path: str, record: Dict[str, str]) -> None:
    site = require_secure_site(record.get("site", ""))
    username = record.get("username", "")
    password = record.get("password", "")
    if not username or not password:
        raise VaultError("username and password are required.", EXIT_USAGE)
    _ensure_dirs(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"site": site, "username": username, "password": password}, f)


def _ensure_dirs(path: str) -> None:
    user_dir = os.path.dirname(path)
    root = os.path.dirname(user_dir)
    for directory, mode in ((root, 0o755), (user_dir, 0o711)):
        if not os.path.isdir(directory):
            os.mkdir(directory, mode)
        # 0711 lets the user check whether an entry exists without reading it.
        os.chmod(directory, mode)


def read(path: str) -> Dict[str, str]:
    try:
        with open(path) as f:
            record = json.load(f)
    except FileNotFoundError:
        raise VaultError("No stored password for this profile.", EXIT_NOT_FOUND)
    return {k: str(record.get(k, "")) for k in ("site", "username", "password")}


def delete(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        raise VaultError("No stored password for this profile.", EXIT_NOT_FOUND)


def login(record: Dict[str, str]) -> str:
    """Log in with the stored record and return the session id."""
    site = require_secure_site(record["site"])
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(jar),
    )
    body = urllib.parse.urlencode(
        {"usr": record["username"], "pwd": record["password"]}
    ).encode()
    request = urllib.request.Request(
        f"{site}/api/method/login",
        data=body,
        # Cloudflare (error 1010) blocks urllib's default User-Agent.
        headers={"Accept": "application/json", "User-Agent": "frappectl-vault"},
    )
    try:
        with opener.open(request, timeout=_TIMEOUT) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        message = _server_message(e.read()) or f"Login failed with HTTP {e.code}."
        code = EXIT_BAD_CREDENTIALS if e.code == 401 else EXIT_ERROR
        raise VaultError(message, code)
    except (urllib.error.URLError, OSError) as e:
        raise VaultError(f"Could not reach {site}: {e}")
    sid = next((c.value for c in jar if c.name == "sid" and c.value), "")
    if not sid or sid == "Guest":
        raise VaultError("Login returned no session cookie.")
    return sid


def _server_message(raw: bytes) -> str:
    try:
        body = json.loads(raw)
    except ValueError:
        return ""
    message = body.get("message") if isinstance(body, dict) else None
    return message.strip() if isinstance(message, str) else ""


def run(argv: List[str], environ: Dict[str, str], stdin: str, root: str) -> str:
    if len(argv) != 2 or argv[0] not in {"store", "login", "delete"}:
        raise VaultError(
            "Usage: frappectl-vault store|login|delete <profile>", EXIT_USAGE
        )
    command, profile = argv
    path = entry_path(root, caller_uid(environ), profile)
    if command == "store":
        try:
            record = json.loads(stdin)
        except ValueError:
            raise VaultError("Expected a JSON record on stdin.", EXIT_USAGE)
        store(path, record if isinstance(record, dict) else {})
        return ""
    if command == "delete":
        delete(path)
        return ""
    record = read(path)
    sid = login(record)
    return json.dumps(
        {"sid": sid, "site": record["site"], "username": record["username"]}
    )


def main() -> int:
    if os.geteuid() != 0:
        print("frappectl-vault must run as root.", file=sys.stderr)
        return EXIT_USAGE
    os.umask(0o077)
    try:
        stdin = sys.stdin.read() if sys.argv[1:2] == ["store"] else ""
        out = run(sys.argv[1:], dict(os.environ), stdin, vault_root())
    except VaultError as e:
        print(str(e), file=sys.stderr)
        return e.code
    except Exception as e:  # never exit 1: sudo uses it for a failed auth
        print(f"frappectl-vault: {type(e).__name__}", file=sys.stderr)
        return EXIT_ERROR
    if out:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
