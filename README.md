# frappectl

[![CI](https://github.com/frappe/frappectl/actions/workflows/ci.yml/badge.svg)](https://github.com/frappe/frappectl/actions/workflows/ci.yml)
[![PyPI Version](https://badge.fury.io/py/frappectl.svg)](https://pypi.org/project/frappectl)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](#license)

A command-line REST API v2 client for Frappe v16+ — built for humans and AI agents.

Using an agent? Start with `frappectl guide`. It fits site selection, core verbs,
filtering and the raw-API escape hatch on one screen.

```sh
frappectl auth login https://erp.example.com --oauth # browser login → OS keyring
frappectl -s raven doc list "Raven Channel" --json    # -s/--site selects a profile

frappectl doc list "Sales Invoice" -f status=Overdue -f 'grand_total>1000' \
  --fields name,customer,grand_total --all --json
frappectl doc get "Sales Invoice" SINV-0001
frappectl doc create ToDo --set description="Follow up" --set priority=High
cat invoice.json | frappectl doc create "Sales Invoice"
frappectl doc submit "Sales Invoice" SINV-0001
frappectl doc delete ToDo abc123

frappectl doctype show "Sales Invoice" --json         # discover the schema first
frappectl report run "Accounts Receivable" -f company="Frappe" --json
frappectl -s raven query 'select count(*) as users from tabUser'
frappectl file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
frappectl api method/frappe.client.get_count -F doctype=User
frappectl api method/gameplan.api.get_unread_count    # raw API, when verbs aren't enough
```

## Install

```sh
uv tool install frappectl
# or: pip install frappectl
# development version: uv tool install git+https://github.com/frappe/frappectl
```

This installs two commands: `frappectl` and its short alias `fr`.

## Authentication

frappectl supports 3 authentication paths.

### Environment variables

Environment variables always win and never touch the keyring.

```sh
export FRAPPE_SITE=https://erp.example.com
export FRAPPE_API_KEY=xxxxxxxx
export FRAPPE_API_SECRET=yyyyyyyy
```

### Stored profiles

Generate an API key + secret from **User → Settings → API Access**, then log in:

```sh
frappectl auth login https://erp.example.com
frappectl auth login https://raven.example.com --name raven
frappectl auth login https://raven.example.com --name raven --default
frappectl auth list
frappectl auth default raven                          # change the default
frappectl -s raven doc list "Raven Channel"           # select per command
frappectl auth whoami
```

The site URL lives in `~/.config/frappe/config.json`; the secret lives in the **OS
keyring**. There is no plaintext fallback. If the machine has no keyring, use environment
variables.

### OAuth

Interactive login selects OAuth by default. It stores no secret; short-lived access
tokens refresh automatically.

```sh
frappectl auth login https://erp.example.com
frappectl auth login https://erp.example.com --client-id <public-client-id>
```

Sites with dynamic client registration create the client automatically. Frappe v15+
supports this by default; other sites need a pre-registered public `--client-id`.

OAuth needs a terminal and local browser, so it isn't the headless path. Use `--oauth`
to skip the authentication-method prompt.

### Username / password

For users who are **not** a System Manager — and so cannot generate an API key or
register an OAuth client — log in with the same username and password you use on the
web. frappectl calls `/api/method/login`, keeps the returned session cookie (`sid`),
and renews it automatically when it expires.

**0. Install the password vault (once per machine).** The password is kept in a
root-owned vault that needs your fingerprint (Linux, via polkit + fprintd) or Touch ID
(macOS, via `sudo` + `pam_tid`) to use, so nothing running as you can read it. Print
the install commands, read `frappectl/vault_helper/frappectl_vault.py`, then run them:

```sh
frappectl auth vault-setup
```

**1. Log in** with `--password` (skips the authentication-method prompt):

```sh
frappectl auth login https://erp.example.com --password
```

**2. Answer the prompts.** A profile name and description come first. If the vault
already holds a password for the profile, frappectl asks for your fingerprint and logs
in with it; if that login fails it offers to update the password. If there is none yet,
it offers to add one — the password is typed hidden, never passed as a flag:

```
Profile name [erp.example.com]: erp
Description (used by assistant mode; optional):
No stored password for 'erp'. Add one? [Y/n]:
Username: you@example.com
Password:
logged in as you@example.com — profile 'erp' [password] (default) [read-only]
```

**3. Use it.** The profile works like any other; select it per command with `-s`, or
make it the default:

```sh
frappectl auth list                                   # 'auth' column shows: password
frappectl -s erp doc list "ToDo" --limit 5
frappectl auth default erp                             # make it the default profile
frappectl auth whoami                                  # confirms auth: password
```

**4. Log out** when done — this ends the session on the server, not just locally:

```sh
frappectl auth logout erp
```

**Notes**

- The password lives only in the vault; the OS keyring holds the username and session
  cookie. While the session is valid nothing prompts; when it expires, the next command
  asks for your fingerprint to log in again. The vault returns a session, never the
  password. Logging out also removes the stored password.
- Password profiles are **always read-only**: session writes need a CSRF token that
  frappectl does not yet issue, so only `GET`/`HEAD`/`OPTIONS` requests are allowed.
  Passing `--writable` prints a note and stores the profile read-only anyway.
- Like the other interactive logins, this needs a terminal. It is not the headless path —
  for automation use `FRAPPE_SITE` / `FRAPPE_API_KEY` / `FRAPPE_API_SECRET`.

### Read-only profiles

A new profile is read-only by default and refuses every unsafe HTTP method **before
the request leaves your machine**. This removes the obvious production footgun: an
exploratory command can't accidentally mutate the site. Choose `--writable` during
login to opt out.

```sh
frappectl auth login https://prod.example.com --name prod
frappectl auth login https://dev.example.com --name dev --writable
frappectl auth configure prod --read-only             # lock an existing profile
frappectl auth configure prod --writable              # allow writes again
```

This blocks `doc create/update/delete/submit`, `file upload`, and method calls through
`api`/`method`. Reads such as `doc list/get`, `doctype show`, and `report run` keep
working.

For environment-variable auth, set `FRAPPE_READ_ONLY=1`. To invoke a whitelisted read
method, make the safe verb explicit: `frappectl api method/… -X GET`.

## Output and scripting

- TTY → rich tables and colours.
- Pipe or `--json` → clean JSON on stdout. Logs and errors stay on stderr, so piping to
  `jq` is safe.
- Mutations → the CLI itself never prompts. Confirmation is the job of the caller,
  whether that is you or an agent harness. Use a read-only profile to make mistakes
  impossible on sites that matter.
- Exit codes → `0` success, `1` failure, `2` usage error.
- `--debug` → traces method, URL and headers to stderr. Credentials are redacted. For
  endpoints that expose it, such as `doc list`, it also prints server-side SQL.

Server error detail is plain text; HTML is stripped. `--debug` never touches stdout, so
it won't corrupt `--json` output.

## Commands

| Command | What it does |
|---|---|
| `frappectl doc list <DocType>` | List documents. Supports `-f`, `--fields`, `--limit` and `--all`. |
| `frappectl doc get <DocType> <name>` | Fetch one document. |
| `frappectl doc create <DocType>` | Create from `--set` scalars and/or piped/`--input` JSON. |
| `frappectl doc update <DocType> <name>` | Update optimistically; use `--force` to override. |
| `frappectl doc delete <DocType> <name>` | Delete a document. |
| `frappectl doc submit\|cancel\|amend` | Run document lifecycle actions. |
| `frappectl doctype list` / `frappectl doctype show <name>` | Discover doctypes and schema. |
| `frappectl report run <name>` | Run a report with the same filter syntax as `doc list`. |
| `frappectl query <sql>` | Run read-only SQL through System Console and print a table. Requires System Manager or Administrator. |
| `frappectl method search\|list\|show` | Discover whitelisted methods (RPC paths and doctype methods). |
| `frappectl method call <path>` | Invoke an RPC method; add `--doctype/--name` to call a doctype method. |
| `frappectl file upload\|download` | Transfer files; upload supports `--doctype/--name` and `--private`. |
| `frappectl api <path>` | Call raw v2 APIs: `frappectl api method/<path> -F key=value`. |
| `frappectl guide` | Print the agent primer; no site or authentication needed. |
| `frappectl assistant [pi\|claude\|codex\|flow]` | Launch a coding agent configured as a Frappe assistant. |
| `frappectl update` | Self-upgrade via `uv tool upgrade`. |

### Filtering

Simple filters use `-f`. Repeat the flag to combine them:

```sh
frappectl doc list ToDo -f status=Open -f priority=High
frappectl doc list "Sales Invoice" -f 'grand_total>1000' -f 'customer like %Inc%'
```

For `in`, `between`, child-table filters or anything else that doesn't fit cleanly in a
shell argument, pass full Frappe filter JSON:

```sh
frappectl doc list "Sales Invoice" --filters-json '[["status","in",["Paid","Overdue"]]]'
```

## License

MIT
