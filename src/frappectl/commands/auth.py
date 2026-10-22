"""``frappectl auth`` — manage site profiles and credentials."""

from __future__ import annotations

import sys
from typing import Optional

import typer

from .. import config, oauth, session_login, vault
from ..client import FrappeClient
from ..config import PasswordCredential
from ..credentials import SessionProvider
from ..errors import FrappeError
from ..output import emit_list, err_console, fail, get_ctx
from ..site import SiteURL

app = typer.Typer(no_args_is_help=True, help="Manage site profiles and credentials.")

_AUTH_METHODS = {"oauth", "api-key", "password"}


def _default_profile_name(site: str) -> str:
    host = site.split("://", 1)[-1].split("/", 1)[0]
    return host.split(":", 1)[0]


def _choose_auth_method(use_oauth: bool, use_password: bool) -> str:
    """Return the selected auth method; an explicit flag skips the prompt.

    One of ``"oauth"``, ``"api-key"`` or ``"password"``. ``--oauth`` and
    ``--password`` are mutually exclusive.
    """
    if use_oauth and use_password:
        raise fail("Choose only one of --oauth or --password.", 2)
    if use_oauth:
        return "oauth"
    if use_password:
        return "password"
    while True:
        method: str = typer.prompt(
            "Authentication method [oauth/api-key/password]",
            default="oauth",
        ).lower()
        if method in _AUTH_METHODS:
            return method
        typer.echo("Choose 'oauth', 'api-key' or 'password'.", err=True)


def _choose_read_only(read_only: Optional[bool]) -> bool:
    """Default new profiles to read-only unless explicitly made writable."""
    if read_only is not None:
        return read_only
    return not typer.confirm("Allow writes through this profile?", default=False)


@app.command("login")
def login(
    ctx: typer.Context,
    site: str = typer.Argument(..., help="Site URL, e.g. https://erp.example.com"),
    name: Optional[str] = typer.Option(
        None, "--name", help="Profile name / shorthand (default: the site host)."
    ),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        help="Note describing the site; assistant mode uses it to pick a site.",
    ),
    set_default: bool = typer.Option(
        False,
        "--default/--no-default",
        help="Make this the default profile (the first profile is always the default).",
    ),
    read_only: Optional[bool] = typer.Option(
        None,
        "--read-only/--writable",
        help="Refuse any write (create/update/delete/method call) through this profile.",
    ),
    use_oauth: bool = typer.Option(
        False,
        "--oauth",
        help="Log in via OAuth without showing the authentication method prompt.",
    ),
    use_password: bool = typer.Option(
        False,
        "--password",
        help="Log in with a username and password (session cookie) without "
        "showing the authentication method prompt.",
    ),
    client_id: Optional[str] = typer.Option(
        None,
        "--client-id",
        help="OAuth public client id to use when the site has no dynamic "
        "registration (must already be registered with the loopback redirect URI).",
    ),
    oauth_port: int = typer.Option(
        oauth.DEFAULT_LOOPBACK_PORT,
        "--oauth-port",
        help="Fixed loopback port for the OAuth redirect (must match the "
        "registered redirect URI).",
    ),
) -> None:
    """Store credentials for a site in the OS keyring.

    Choose OAuth browser login (the default), API key/secret, or a
    username/password session login. Credentials are never accepted as flags or
    piped in, since that leaks them into shell history, the process list and CI
    logs. OAuth runs an OAuth 2.0 authorization-code flow in your browser — no
    secret is ever stored, and tokens refresh automatically. Password login is
    for users who are not a System Manager and so cannot generate API keys or
    register an OAuth client: the password lives in a root-owned vault that
    needs your fingerprint to use (see 'auth vault-setup') and is always read-only, since session writes
    need a CSRF token that is not yet supported. --oauth / --password skip the
    method prompt.

    All methods need a terminal (and OAuth needs a local browser). For headless
    / agent use, set FRAPPE_SITE / FRAPPE_API_KEY / FRAPPE_API_SECRET in the
    environment instead — those never touch the keyring.
    """
    # Refuse anything that isn't a real terminal so credentials can't be fed in
    # by pipe, heredoc or redirect (all of which end up in history or logs), and
    # because the OAuth flow needs a browser on this machine.
    if not sys.stdin.isatty():
        raise fail(
            "auth login is interactive only and needs a terminal (OAuth also "
            "needs a local browser). For headless / agent use set FRAPPE_SITE, "
            "FRAPPE_API_KEY and FRAPPE_API_SECRET in the environment instead.",
            2,
        )

    norm_site = str(SiteURL.parse(site))

    # A friendly shorthand and a description are both prompted for (with sane
    # defaults) unless supplied as flags, so a stored site is easy to pick
    # later — by a human at `auth list` or by an agent in assistant mode.
    # A password re-login reuses the site's existing profile without asking.
    existing = _password_profile_for(norm_site) if use_password else None
    if existing and name is None:
        name = existing[0]
    if existing and description is None and name == existing[0]:
        description = existing[1]
    profile = (
        name
        if name is not None
        else typer.prompt("Profile name", default=_default_profile_name(norm_site))
    )
    if description is None:
        description = typer.prompt(
            "Description (used by assistant mode; optional)", default=""
        )
    method = _choose_auth_method(use_oauth, use_password)

    # Password/session auth is read-only in this version: session writes need a
    # CSRF token we do not yet issue, so force it rather than store a profile
    # whose writes would fail confusingly at the server.
    if method == "password":
        if read_only is False:
            err_console.print(
                "[yellow]note:[/yellow] writable password profiles aren't "
                "supported yet (session writes need a CSRF token); storing "
                "read-only."
            )
        _login_password(
            profile,
            norm_site,
            set_default=set_default,
            description=description or "",
            read_only=True,
        )
        return

    # New profiles are safe by default; --writable remains an explicit opt-out.
    read_only = _choose_read_only(read_only)

    if method == "oauth":
        _login_oauth(
            profile,
            norm_site,
            client_id=client_id,
            oauth_port=oauth_port,
            set_default=set_default,
            description=description or "",
            read_only=bool(read_only),
        )
        return

    api_key = typer.prompt("API key")
    api_secret = typer.prompt("API secret", hide_input=True)

    # Verify before storing so we never persist dead credentials.
    try:
        with FrappeClient(norm_site, f"{api_key}:{api_secret}") as client:
            who = client.get_logged_user()
    except FrappeError as e:
        raise fail(f"Could not authenticate against {norm_site}: {e.message}")

    try:
        config.add_profile(
            profile,
            norm_site,
            api_key,
            api_secret,
            make_default=set_default,
            description=description or "",
            read_only=bool(read_only),
        )
    except config.ConfigError as e:
        raise fail(str(e), 2)

    _report_login(who, profile, read_only=bool(read_only))


def _login_oauth(
    profile: str,
    norm_site: str,
    *,
    client_id: Optional[str],
    oauth_port: int,
    set_default: bool,
    description: str,
    read_only: bool,
) -> None:
    """Run the OAuth browser flow, verify identity, and persist the tokens."""
    # Reuse a client id already registered for this profile so re-logins don't
    # litter the site with new OAuth Client rows; an explicit --client-id wins.
    resolved_client_id = client_id or config.oauth_client_id(profile)

    def announce(url: str) -> None:
        err_console.print(
            "[dim]Opening your browser to authorize. If it does not open, "
            f"visit:[/dim]\n{url}"
        )

    try:
        tokens, used_client_id = oauth.login(
            norm_site,
            client_id=resolved_client_id,
            port=oauth_port,
            announce=announce,
        )
    except oauth.OAuthError as e:
        raise fail(f"OAuth login against {norm_site} failed: {e}")

    # Verify before storing so we never persist a token we cannot use.
    try:
        with FrappeClient(
            norm_site, tokens.access_token, token_type="bearer"
        ) as client:
            who = client.get_logged_user()
    except FrappeError as e:
        raise fail(f"Could not authenticate against {norm_site}: {e.message}")

    try:
        config.add_oauth_profile(
            profile,
            norm_site,
            used_client_id,
            tokens,
            make_default=set_default,
            description=description,
            read_only=read_only,
        )
    except config.ConfigError as e:
        raise fail(str(e), 2)

    _report_login(who, profile, read_only=read_only, method="oauth")


def _login_password(
    profile: str,
    norm_site: str,
    *,
    set_default: bool,
    description: str,
    read_only: bool,
) -> None:
    """Log in with the password stored in the fingerprint-gated vault.

    A stored password is used as-is; a failed login offers to update it, and a
    missing one offers to add it. The password is never kept in the keyring.
    """
    if not vault.is_installed():
        raise fail(
            "The password vault is not installed. Run 'frappectl auth vault-setup' "
            "and run the commands it prints.",
            2,
        )
    try:
        if vault.has_password(profile):
            username, sid = _login_from_vault(profile, norm_site)
        elif typer.confirm(
            f"No stored password for '{profile}'. Add one?", default=True
        ):
            username, sid = _add_to_vault(profile, norm_site)
        else:
            raise fail("No stored password to log in with.")
    except vault.VaultError as e:
        raise fail(str(e))

    # Verify before storing so we never persist a session we cannot use.
    provider = SessionProvider(
        norm_site,
        PasswordCredential(username, "", sid),
        _no_relogin,
        lambda _credential: None,
    )
    try:
        with FrappeClient(
            norm_site, "", token_type="session", credential_provider=provider
        ) as client:
            who = client.get_logged_user()
    except FrappeError as e:
        raise fail(f"Could not authenticate against {norm_site}: {e.message}")

    try:
        config.add_password_profile(
            profile,
            norm_site,
            username,
            "",
            sid=sid,
            make_default=set_default,
            description=description,
            read_only=read_only,
        )
    except config.ConfigError as e:
        raise fail(str(e), 2)

    _report_login(who, profile, read_only=read_only, method="password")


def _password_profile_for(norm_site: str) -> tuple[str, str] | None:
    """The (name, description) of a saved password profile for this site.

    A site can have several; prefer the one with a vault password, then the one
    named after the host.
    """
    profiles, _ = config.list_profiles()
    matches = [
        name
        for name, entry in profiles.items()
        if entry.get("auth") == "password"
        and str(SiteURL.parse(entry["site"])) == norm_site
    ]
    if not matches:
        return None
    host_name = _default_profile_name(norm_site)
    best = next(
        (name for name in matches if _in_vault(name)),
        host_name if host_name in matches else matches[0],
    )
    return best, profiles[best].get("description", "")


def _in_vault(profile: str) -> bool:
    try:
        return vault.has_password(profile)
    except vault.VaultError:
        return False


def _login_from_vault(profile: str, norm_site: str) -> tuple[str, str]:
    try:
        stored = vault.login(profile)
    except vault.VaultBadCredentials as e:
        err_console.print(f"[red]Login failed:[/red] {e}")
        if not typer.confirm("Update the stored password?", default=True):
            raise fail("Login failed.")
        return _add_to_vault(profile, norm_site)
    if stored.site.rstrip("/") != norm_site.rstrip("/"):
        err_console.print(
            f"[yellow]note:[/yellow] the stored password is for {stored.site}."
        )
        if not typer.confirm(f"Replace it with one for {norm_site}?", default=False):
            raise fail("Login cancelled.")
        return _add_to_vault(profile, norm_site)
    return stored.username, stored.sid


def _add_to_vault(profile: str, norm_site: str) -> tuple[str, str]:
    """Prompt for credentials, check them against the site, then store them."""
    username = typer.prompt("Username")
    password = typer.prompt("Password", hide_input=True)
    try:
        sid = session_login.login(norm_site, username, password)
    except session_login.SessionLoginError as e:
        raise fail(f"Could not log in to {norm_site}: {e}")
    vault.store(profile, norm_site, username, password)
    return username, sid


def _no_relogin(site: str, username: str, password: str) -> str:
    raise session_login.SessionLoginError("The new session was rejected.")


def _report_login(
    who: str, profile: str, *, read_only: bool, method: str = "api-key"
) -> None:
    # The profile may still be the default even without --default: the very
    # first profile always becomes the default (see config.add_profile).
    _, default = config.list_profiles()
    is_default = default == profile
    # API-key is the plain default and gets no tag; the added methods are tagged.
    tag = f" [{method}]" if method in {"oauth", "password"} else ""
    err_console.print(
        f"[green]logged in[/green] as {who} — profile '{profile}'"
        + tag
        + (" (default)" if is_default else "")
        + (" [read-only]" if read_only else "")
    )


@app.command("list")
def list_profiles(ctx: typer.Context) -> None:
    """List stored profiles; the default is marked."""
    c = get_ctx(ctx)
    try:
        profiles, default = config.list_profiles()
    except config.ConfigError as e:
        raise fail(str(e), 2)

    rows = [
        {
            "profile": name,
            "site": info.get("site", ""),
            "auth": info.get("auth", "api_key"),
            "description": info.get("description", ""),
            "read_only": bool(info.get("read_only", False)),
            "default": name == default,
        }
        for name, info in profiles.items()
    ]
    emit_list(
        c, rows, ["profile", "site", "auth", "description", "read_only", "default"]
    )
    if not rows and not c.json:
        err_console.print(
            "[dim]No profiles. Run 'frappectl auth login <url>' or use FRAPPE_SITE env vars.[/dim]"
        )


@app.command("logout")
def logout(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Profile to remove."),
) -> None:
    """Remove a stored profile and its credentials.

    For an OAuth profile the access token is revoked on the server first, and for
    a password profile the session is ended on the server first (both
    best-effort) so logging out actually ends the session, not just forgets it
    locally.
    """
    profiles, _ = config.list_profiles()
    auth_kind = profiles.get(name, {}).get("auth")
    if auth_kind == "oauth":
        token = config.oauth_access_token(name)
        if token:
            oauth.revoke(str(SiteURL.parse(profiles[name]["site"])), token)
    elif auth_kind == "password":
        sid = config.password_sid(name)
        if sid:
            session_login.logout(str(SiteURL.parse(profiles[name]["site"])), sid)
        _forget_vault_password(name)
    try:
        config.remove_profile(name)
    except config.ConfigError as e:
        raise fail(str(e), 2)
    err_console.print(f"[green]removed[/green] profile '{name}'")


def _forget_vault_password(name: str) -> None:
    try:
        if vault.has_password(name):
            vault.delete(name)
    except vault.VaultError as e:
        err_console.print(f"[yellow]note:[/yellow] stored password kept: {e}")


@app.command("vault-setup")
def vault_setup() -> None:
    """Print the root commands that install the fingerprint-gated password vault.

    Read the helper first, then run the commands yourself; frappectl never
    escalates on your behalf.
    """
    for line in vault.setup_commands():
        typer.echo(line)


@app.command("default")
def set_default(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Profile to make default."),
) -> None:
    """Set the default profile."""
    try:
        config.set_default(name)
    except config.ConfigError as e:
        raise fail(str(e), 2)
    err_console.print(f"[green]default[/green] is now '{name}'")


@app.command("configure")
def configure(
    ctx: typer.Context,
    profile: str = typer.Argument(..., help="Profile to reconfigure."),
    name: Optional[str] = typer.Option(
        None, "--name", help="New profile name / shorthand."
    ),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        help="New description; pass an empty string to clear it.",
    ),
    read_only: Optional[bool] = typer.Option(
        None,
        "--read-only/--writable",
        help="Make this profile read-only (refuse writes) or writable again.",
    ),
) -> None:
    """Reconfigure a stored site: rename it, change its description, or toggle
    read-only.

    With no flags on a terminal, name and description are prompted for with
    their current values as defaults (read-only is left unchanged unless the
    flag is passed). Credentials are never touched here — use 'auth login' to
    re-enter an API key/secret.
    """
    try:
        profiles, _ = config.list_profiles()
    except config.ConfigError as e:
        raise fail(str(e), 2)
    if profile not in profiles:
        raise fail(
            f"No such profile: {profile}. Run 'frappectl auth list' to see profiles.",
            2,
        )
    current_desc = profiles[profile].get("description", "")

    # Nothing on the command line: prompt interactively, or refuse when there
    # is no terminal to prompt at (a flag would then be required).
    if name is None and description is None and read_only is None:
        if not sys.stdin.isatty():
            raise fail(
                "Nothing to change. Pass --name, --description and/or "
                "--read-only/--writable (this command only prompts on a terminal).",
                2,
            )
        name = typer.prompt("Profile name", default=profile)
        description = typer.prompt("Description", default=current_desc)

    try:
        if name is not None and name != profile:
            config.rename_profile(profile, name)
            profile = name
        if description is not None:
            config.set_description(profile, description)
        if read_only is not None:
            config.set_read_only(profile, read_only)
    except config.ConfigError as e:
        raise fail(str(e), 2)

    err_console.print(f"[green]updated[/green] profile '{profile}'")


@app.command("whoami")
def whoami(ctx: typer.Context) -> None:
    """Show the resolved site and logged-in user for the active profile."""
    c = get_ctx(ctx)
    try:
        creds = config.resolve(c.profile, interactive=c.is_tty)
    except config.ConfigError as e:
        raise fail(str(e), 2)
    # Build the client through the factory so the right provider is armed —
    # a session profile authenticates with a cookie, not a wire token.
    try:
        with c.client_factory.create(
            c.profile, interactive=c.is_tty, debug=c.debug
        ) as client:
            user = client.get_logged_user()
    except FrappeError as e:
        raise fail(e.message)
    from ..output import emit_record

    auth_label = {"bearer": "oauth", "session": "password"}.get(
        creds.token_type, "api_key"
    )
    record: dict[str, object] = {
        "site": creds.site,
        "user": user,
        "source": creds.source,
        "auth": auth_label,
        "description": creds.description,
        "read_only": creds.read_only,
    }
    if creds.token_type == "bearer" and creds.expires_at:
        import datetime

        record["token_expires_at"] = datetime.datetime.fromtimestamp(
            creds.expires_at, tz=datetime.timezone.utc
        ).isoformat()
    emit_record(c, record)
