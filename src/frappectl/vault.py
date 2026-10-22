"""Client for the root-owned, fingerprint-gated password vault.

Password profiles keep their password in ``frappectl-vault`` (see
``vault_helper/frappectl_vault.py``), not in the user's keyring, so no process
running as the user can read it. Every call goes through ``pkexec`` (Linux) or
``sudo -k`` (macOS), which asks for a fingerprint / Touch ID.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .vault_helper import frappectl_vault as helper

HELPER_PATH = "/usr/local/libexec/frappectl-vault"
POLICY_PATH = "/usr/share/polkit-1/actions/io.frappe.frappectl.vault.policy"
_SOURCE_DIR = Path(__file__).parent / "vault_helper"

# pkexec: 126 = the auth dialog was dismissed, 127 = not authorized.
# sudo: 1 = authentication failed (the helper itself never exits 1).
_CANCELLED_CODES = {"linux": {126, 127}, "darwin": {1}}


class VaultError(Exception):
    """The vault could not complete the request."""


class VaultNotInstalled(VaultError):
    pass


class VaultCancelled(VaultError):
    pass


class VaultBadCredentials(VaultError):
    pass


class VaultEntryMissing(VaultError):
    pass


@dataclass(frozen=True)
class VaultLogin:
    sid: str
    site: str
    username: str


def is_installed() -> bool:
    return os.access(HELPER_PATH, os.X_OK)


def has_password(profile: str) -> bool:
    """Whether a password is stored; a stat, so no fingerprint is needed."""
    try:
        path = helper.entry_path(helper.vault_root(), os.getuid(), profile)
    except helper.VaultError as e:
        raise VaultError(str(e)) from e
    return Path(path).exists()


def store(profile: str, site: str, username: str, password: str) -> None:
    record = json.dumps({"site": site, "username": username, "password": password})
    _run("store", profile, stdin=record)


def login(profile: str) -> VaultLogin:
    out = json.loads(_run("login", profile))
    return VaultLogin(out["sid"], out["site"], out["username"])


def delete(profile: str) -> None:
    _run("delete", profile)


def _elevate() -> list[str]:
    if sys.platform == "darwin":
        return ["sudo", "-k"]
    return ["pkexec"]


def _run(command: str, profile: str, stdin: str = "") -> str:
    if not is_installed():
        raise VaultNotInstalled(
            "The password vault is not installed. Run 'frappectl auth vault-setup'."
        )
    proc = subprocess.run(
        [*_elevate(), HELPER_PATH, command, profile],
        input=stdin,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return proc.stdout
    message = proc.stderr.strip()
    if proc.returncode in _CANCELLED_CODES.get(sys.platform, set()):
        raise VaultCancelled("Fingerprint check was cancelled or failed.")
    if proc.returncode == helper.EXIT_BAD_CREDENTIALS:
        raise VaultBadCredentials(message or "Invalid login credentials.")
    if proc.returncode == helper.EXIT_NOT_FOUND:
        raise VaultEntryMissing(message or "No stored password for this profile.")
    raise VaultError(message or f"frappectl-vault exited {proc.returncode}.")


def setup_commands() -> list[str]:
    """Shell commands that install the helper; the user runs them, as root."""
    source = _SOURCE_DIR / "frappectl_vault.py"
    if sys.platform == "darwin":
        return [
            f"sudo install -d -o root -g wheel -m 0755 {Path(HELPER_PATH).parent}",
            f"sudo install -o root -g wheel -m 0755 {source} {HELPER_PATH}",
            "# Touch ID for sudo (skip if /etc/pam.d/sudo_local already has pam_tid):",
            "sed -e 's/^#auth/auth/' /etc/pam.d/sudo_local.template"
            " | sudo tee /etc/pam.d/sudo_local",
        ]
    return [
        f"sudo install -D -o root -g root -m 0755 {source} {HELPER_PATH}",
        f"sudo install -o root -g root -m 0644 "
        f"{_SOURCE_DIR / 'io.frappe.frappectl.vault.policy'} {POLICY_PATH}",
    ]
