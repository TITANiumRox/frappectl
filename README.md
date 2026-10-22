# frappectl

[![CI](https://github.com/frappe/frappectl/actions/workflows/ci.yml/badge.svg)](https://github.com/frappe/frappectl/actions/workflows/ci.yml)
[![PyPI Version](https://badge.fury.io/py/frappectl.svg)](https://pypi.org/project/frappectl)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](#license)

frappectl is a command-line client for the REST API v2 of Frappe v16 and later. People
and AI agents use the same commands.

> This is a fork of [frappe/frappectl](https://github.com/frappe/frappectl). It adds
> username and password login, with the password kept in a vault that only opens with
> your fingerprint. See [Username and password](#username-and-password).

If you are an agent, run `frappectl guide` first. It shows site selection, the main
commands, filters and the raw API on one screen.

```sh
frappectl auth login https://erp.example.com --oauth  # log in through the browser
frappectl -s raven doc list "Raven Channel" --json     # -s picks a saved site

frappectl doc list "Sales Invoice" -f status=Overdue -f 'grand_total>1000' \
  --fields name,customer,grand_total --all --json
frappectl doc get "Sales Invoice" SINV-0001
frappectl doc create ToDo --set description="Follow up" --set priority=High
cat invoice.json | frappectl doc create "Sales Invoice"
frappectl doc submit "Sales Invoice" SINV-0001
frappectl doc delete ToDo abc123

frappectl doctype show "Sales Invoice" --json          # read the schema first
frappectl report run "Accounts Receivable" -f company="Frappe" --json
frappectl -s raven query 'select count(*) as users from tabUser'
frappectl file upload ./contract.pdf --doctype "Sales Invoice" --name SINV-0001 --private
frappectl api method/frappe.client.get_count -F doctype=User
frappectl api method/gameplan.api.get_unread_count     # raw API call
```

## Install

```sh
uv tool install git+https://github.com/raizasafeel/frappectl@feat/password-login
```

The upstream release installs with `uv tool install frappectl` or
`pip install frappectl`, but it does not have the fingerprint vault.

You get two commands, `frappectl` and the short alias `fr`.

## Logging in

There are four ways to give frappectl your credentials. Pick one per site.

### Environment variables

Use these for scripts, CI and agents. They override every saved profile and frappectl
never writes them to the keyring.

```sh
export FRAPPE_SITE=https://erp.example.com
export FRAPPE_API_KEY=xxxxxxxx
export FRAPPE_API_SECRET=yyyyyyyy
```

### API key and secret

Create a key and secret in Frappe under User, Settings, API Access. Then log in:

```sh
frappectl auth login https://erp.example.com
frappectl auth login https://raven.example.com --name raven
frappectl auth login https://raven.example.com --name raven --default
frappectl auth list
frappectl auth default raven                    # change the default site
frappectl -s raven doc list "Raven Channel"     # pick a site for one command
frappectl auth whoami
```

The site URL goes in `~/.config/frappe/config.json`. The secret goes in your OS
keyring and nowhere else. If your machine has no keyring, use environment variables.

### OAuth

This is the default when you log in interactively. frappectl opens your browser, you
approve, and it stores short-lived tokens that refresh on their own. No secret is saved.

```sh
frappectl auth login https://erp.example.com
frappectl auth login https://erp.example.com --client-id <public-client-id>
```

Frappe v15 and later register the OAuth client for you. Other sites need a public
client registered ahead of time, passed with `--client-id`.

OAuth needs a terminal and a browser on the same machine. Add `--oauth` to skip the
question about which login method to use.

### Username and password

Use this if you are not a System Manager. Without that role you can't create an API
key or an OAuth client, but you can still log in with the username and password you
use on the website.

frappectl logs in through `/api/method/login` and keeps the session cookie. Your
password goes into a small vault owned by root. The vault only opens after a
fingerprint check (Linux) or Touch ID (macOS). Programs running under your user
account, AI agents included, can't read it.

**1. Install the vault.** You do this once per machine. Print the commands:

```sh
frappectl auth vault-setup
```

Read `frappectl/vault_helper/frappectl_vault.py` before you run them. That file runs
as root. On Linux it uses polkit and fprintd. On macOS it uses `sudo` and Touch ID,
and needs the Xcode Command Line Tools for `/usr/bin/python3`.

**2. Log in.**

```sh
frappectl auth login https://erp.example.com --password
```

frappectl asks for a profile name and a description. Then:

- If the vault has a password for this profile, frappectl asks for your fingerprint
  and logs in.
- If that login fails, it says so and asks whether you want to update the password.
- If the vault has no password yet, it asks whether you want to add one. You type the
  password at a hidden prompt. It is never a command-line flag.

```
Profile name [erp.example.com]: erp
Description (used by assistant mode; optional):
No stored password for 'erp'. Add one? [Y/n]:
Username: you@example.com
Password:
logged in as you@example.com — profile 'erp' [password] (default) [read-only]
```

**3. Use it** like any other profile:

```sh
frappectl auth list                       # the auth column says "password"
frappectl -s erp doc list "ToDo" --limit 5
frappectl auth default erp
frappectl auth whoami
```

**4. Log out** when you are done. This ends the session on the server and deletes the
password from the vault:

```sh
frappectl auth logout erp
```

A few things to know:

- Commands don't prompt while the session is valid. When the session expires, the next
  command asks for your fingerprint and logs in again.
- The vault gives frappectl a session cookie, never the password. If a fingerprint
  prompt shows up that you didn't expect, decline it.
- Password profiles are always read-only. Writes through a session need a CSRF token,
  and frappectl doesn't send one yet. If you pass `--writable`, frappectl prints a note
  and saves the profile as read-only anyway.
- Logging in needs a terminal. For automation, use environment variables.

### Read-only profiles

New profiles are read-only unless you say otherwise. frappectl refuses every write on
your machine, before it sends anything, so a stray command can't change a production
site. Pass `--writable` at login to allow writes.

```sh
frappectl auth login https://prod.example.com --name prod
frappectl auth login https://dev.example.com --name dev --writable
frappectl auth configure prod --read-only       # lock an existing profile
frappectl auth configure prod --writable        # allow writes again
```

Read-only blocks `doc create`, `doc update`, `doc delete`, `doc submit`, `file upload`
and method calls through `api` or `method`. Reads like `doc list`, `doc get`,
`doctype show` and `report run` still work.

With environment variables, set `FRAPPE_READ_ONLY=1`. To call a whitelisted method
that only reads, send it as a GET: `frappectl api method/… -X GET`.

## Output and scripting

- In a terminal you get tables and colour.
- In a pipe, or with `--json`, you get plain JSON on stdout. Logs and errors go to
  stderr, so piping into `jq` is safe.
- frappectl never asks "are you sure?" before a write. Whoever runs it, you or an agent,
  decides that. Use a read-only profile on sites you care about.
- Exit codes are `0` for success, `1` for failure and `2` for a usage error.
- `--debug` prints the method, URL and headers to stderr with credentials hidden. For
  `doc list` and other endpoints that return it, it also prints the SQL the server ran.

Server errors arrive as plain text with the HTML removed. `--debug` never writes to
stdout, so it doesn't break `--json` output.

## Commands

| Command | What it does |
|---|---|
| `frappectl doc list <DocType>` | List documents. Takes `-f`, `--fields`, `--limit` and `--all`. |
| `frappectl doc get <DocType> <name>` | Fetch one document. |
| `frappectl doc create <DocType>` | Create a document from `--set` values, piped JSON or `--input`. |
| `frappectl doc update <DocType> <name>` | Update a document. Fails if someone changed it first, unless you pass `--force`. |
| `frappectl doc delete <DocType> <name>` | Delete a document. |
| `frappectl doc submit\|cancel\|amend` | Submit, cancel or amend a document. |
| `frappectl doctype list` / `frappectl doctype show <name>` | List doctypes and show a schema. |
| `frappectl report run <name>` | Run a report. Filters work like `doc list`. |
| `frappectl query <sql>` | Run read-only SQL through System Console. Needs System Manager or Administrator. |
| `frappectl method search\|list\|show` | Find whitelisted methods, both RPC paths and doctype methods. |
| `frappectl method call <path>` | Call an RPC method. Add `--doctype` and `--name` to call a doctype method. |
| `frappectl file upload\|download` | Upload or download files. Upload takes `--doctype`, `--name` and `--private`. |
| `frappectl api <path>` | Call any v2 API, for example `frappectl api method/<path> -F key=value`. |
| `frappectl guide` | Print the agent primer. Needs no site or login. |
| `frappectl assistant [pi\|claude\|codex\|flow]` | Start a coding agent set up as a Frappe assistant. |
| `frappectl auth vault-setup` | Print the root commands that install the password vault. |
| `frappectl update` | Upgrade frappectl with `uv tool upgrade`. |

### Filtering

Use `-f` for simple filters. Repeat it to add more:

```sh
frappectl doc list ToDo -f status=Open -f priority=High
frappectl doc list "Sales Invoice" -f 'grand_total>1000' -f 'customer like %Inc%'
```

For `in`, `between`, child-table filters, or anything awkward to type in a shell, pass
Frappe filter JSON:

```sh
frappectl doc list "Sales Invoice" --filters-json '[["status","in",["Paid","Overdue"]]]'
```

## License

MIT
