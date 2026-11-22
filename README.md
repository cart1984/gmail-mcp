# gmail-mcp

A small **read-only** MCP server for Gmail. It gives any MCP client (Claude Code, Claude Desktop, Codex, VS Code, Cursor, Gemini CLI, or your own agent) access to a Gmail account:

- **Search** with Gmail's own search syntax (`from:`, `subject:`, `has:attachment`, `newer_than:7d`, ...)
- **List** the newest messages in Inbox, Sent, a category or any label, optionally unread only or since a date
- **Read** a message or a whole conversation, with its attachments listed
- **Read attachments** as text: Word, Excel, PowerPoint and PDF files are converted in memory (with [MarkItDown](https://github.com/microsoft/markitdown)); a forwarded mail is shown like any other message
- **Labels** with unread and total counts

Nothing is written to disk unless you ask for it. All dates and times, shown and given, are in your time zone.

It works with personal Gmail accounts and Google Workspace accounts.

Status: early, personal project. Licensed under MIT. Its sibling for Microsoft accounts is [graph-mcp](https://github.com/cart1984/graph-mcp).

## Read-only, twice over

1. **The sign-in only grants read permission:** the one scope is `gmail.readonly`. Google refuses any send, draft, label change, archive or delete with this token, whatever the code does. It can't reach Drive, Calendar or anything else either.
2. **The code only reads:** the Gmail client has no method except GET, and every tool is marked read-only for MCP clients. (Signing in and refreshing the token talk to Google's sign-in service, not to Gmail.)

Content of mails reaches the agent as data. The server's instructions tell agents to treat it as data, never as instructions, but as with any tool that reads untrusted text, keep that in mind when you let an agent act on what it read.

## Setup

### 1. Create a Google OAuth client (once, about 15 minutes)

Both ways of running it need your own OAuth client in Google Cloud, which gives you a client ID and secret. It's free, and you don't need billing. The console's names below are as of late 2026; Google renames things now and then.

1. At [console.cloud.google.com](https://console.cloud.google.com), sign in with the Google account whose mail you want to read, and **create a project** (e.g. `gmail-mcp`).
2. **APIs & Services → Library:** find **Gmail API** and **Enable** it.
3. **Google Auth Platform** (also reached as *APIs & Services → OAuth consent screen*) **→ Get started:** app name `gmail-mcp`, your address as support and contact email, audience **External**. For a Google Workspace account you can choose **Internal** instead and skip step 6.
4. **Data Access → Add or remove scopes:** add `.../auth/gmail.readonly` (filter for "gmail.readonly"). Save.
5. **Clients → Create client:** application type **Desktop app**, any name. Copy the **client ID** and **client secret**. For a desktop app, Google doesn't treat the secret as confidential (it's in every installed copy of such apps), but keep it out of public places anyway.
6. **Audience → Publish app**, to move it from *Testing* to *In production*. While an app is in Testing, Google expires its sign-ins after 7 days, so you'd have to sign in every week. Publishing doesn't make it public, and Google doesn't review it unless you ask: it stays an unverified app that only you use. When you sign in, Google warns *"Google hasn't verified this app"*: that's you, so choose **Advanced → Go to gmail-mcp**.
   - If you'd rather stay in Testing: add your address under **Audience → Test users**, and expect to sign in again every 7 days. (`gmail-mcp status` and the tools tell you when it has lapsed.)

### 2. Choose how to run it

| | **A. On this machine** | **B. In Docker** |
|---|---|---|
| For | Agents on the same computer | A home server that agents on several machines use |
| Agents talk to it over | stdio (each agent starts `gmail-mcp`) | HTTP with a Bearer token |
| The sign-in lives in | Your OS keychain | A Docker volume |
| Steps | A1–A3 below | B1–B5 below |

You can use both; each keeps its own sign-in.

### Option A: on this machine

**A1. Install** with [uv](https://docs.astral.sh/uv/), as a command on your `PATH`:

```bash
uv tool install git+https://github.com/cart1984/gmail-mcp
```

Or from a local checkout (edits take effect right away): `uv tool install --editable ~/projects/gmail-mcp`.

**A2. Sign in** (once per machine):

```bash
GMAIL_MCP_CLIENT_ID=<client id> GMAIL_MCP_CLIENT_SECRET=<client secret> gmail-mcp login
```

Your browser opens Google's sign-in page. Choose the account, allow reading your mail (tick the box if Google shows one), and the command finishes by itself: Google sends the answer back to `http://localhost` on a free port. If no browser opens, open the URL it prints.

`gmail-mcp status` shows who is signed in. If the sign-in stops working (you removed the app at [myaccount.google.com/connections](https://myaccount.google.com/connections), changed your password, or the app is still in Testing), tools answer with "run `gmail-mcp login`". All agents on the machine share this one sign-in.

**A3. Add it to your agents.** Every client starts the same command, `gmail-mcp`, with the client ID and secret set.

Claude Code:

```bash
claude mcp add --scope user gmail -e GMAIL_MCP_CLIENT_ID=<id> -e GMAIL_MCP_CLIENT_SECRET=<secret> -- gmail-mcp
```

Claude Desktop, Cursor, Gemini CLI (`claude_desktop_config.json`, `~/.cursor/mcp.json`, `~/.gemini/settings.json`):

```json
{
  "mcpServers": {
    "gmail": {
      "command": "gmail-mcp",
      "env": { "GMAIL_MCP_CLIENT_ID": "<id>", "GMAIL_MCP_CLIENT_SECRET": "<secret>" }
    }
  }
}
```

GUI apps on macOS may not see your shell's `PATH`; if the server doesn't start, use the full path from `which gmail-mcp` (usually `~/.local/bin/gmail-mcp`).

VS Code (user or workspace `mcp.json`):

```json
{
  "servers": {
    "gmail": {
      "type": "stdio",
      "command": "gmail-mcp",
      "env": { "GMAIL_MCP_CLIENT_ID": "<id>", "GMAIL_MCP_CLIENT_SECRET": "<secret>" }
    }
  }
}
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.gmail]
command = "gmail-mcp"
env = { GMAIL_MCP_CLIENT_ID = "<id>", GMAIL_MCP_CLIENT_SECRET = "<secret>" }
```

### Option B: in Docker

`compose.yaml` runs the HTTP server in a locked-down container: read-only file system, no capabilities, a non-root user, the port on one address only, and the sign-in in a named volume. Attachments are converted in memory and never kept.

**B1. Get the code and write the settings** on the machine that will run it:

```bash
git clone https://github.com/cart1984/gmail-mcp && cd gmail-mcp
echo "GMAIL_MCP_CLIENT_ID=<client id>" > .env
echo "GMAIL_MCP_CLIENT_SECRET=<client secret>" >> .env
echo "GMAIL_MCP_TOKEN=$(openssl rand -hex 32)" >> .env
echo "GMAIL_MCP_BIND=<this machine's address on your clients' network>" >> .env
echo "TZ=America/New_York" >> .env   # your time zone
chmod 600 .env
```

`GMAIL_MCP_TOKEN` is what agents must send; anyone who has it can read your mail. `GMAIL_MCP_BIND` is required: the port is published on that address only, never on every interface.

**B2. Build** (the image is about 1 GB, mostly the document converters' dependencies):

```bash
docker compose build
```

**B3. Sign in** (once). The container listens for Google's answer on port 8780, published on this machine's loopback only:

```bash
docker compose run --rm -p 127.0.0.1:8780:8780 gmail login --no-browser --port 8780 --bind 0.0.0.0
```

It prints a URL. Open it in a browser **on the same machine**, sign in, and the command finishes by itself. On a server without a browser, forward the port from your computer first, and open the URL there:

```bash
ssh -L 8780:127.0.0.1:8780 <server>   # in a second terminal on your computer, while the login runs
```

Or sign in on a computer that has gmail-mcp installed (step A1), into a file instead of the keychain, and copy that file into the volume:

```bash
# on the computer with the browser:
GMAIL_MCP_CLIENT_ID=<id> GMAIL_MCP_CLIENT_SECRET=<secret> GMAIL_MCP_TOKEN_CACHE=file gmail-mcp login
# writes ~/.config/gmail-mcp/token-cache.json; put it into the gmail-mcp folder on the server, then there:
docker compose run --rm -T --entrypoint sh gmail -c 'umask 077 && cat > /data/token-cache.json' < token-cache.json
rm token-cache.json   # and the one in ~/.config/gmail-mcp/ on the other computer: keep no copies around
```

**B4. Start it and check:**

```bash
docker compose up -d
docker compose run --rm gmail status   # "Signed in as ...; token is valid."
docker compose ps                      # "healthy" once it's up
```

The container counts as healthy only while it has a sign-in (`/healthz` answers 503 "not signed in" otherwise), so `docker compose ps` also tells you when the sign-in is gone.

**B5. Add it to your agents.** They connect to `http://<GMAIL_MCP_BIND>:8767/mcp` with the header `Authorization: Bearer <GMAIL_MCP_TOKEN>`. Claude Code:

```bash
claude mcp add --scope user --transport http gmail http://<host>:8767/mcp --header "Authorization: Bearer <token>"
```

Other clients that support remote (Streamable HTTP) servers take the same URL and header in their config; for a client that only speaks stdio, bridge it with [`mcp-remote`](https://www.npmjs.com/package/mcp-remote).

**This is plain HTTP: the token and everything you read travel unencrypted.** That's fine on a network you trust and inside a VPN. Anywhere else, put a TLS proxy in front (Caddy, for example) or don't expose it.

**Updating:** `git pull && docker compose up -d --build`. The sign-in stays in the volume.

## Other ways to run

**HTTP without Docker**, e.g. on a server without containers:

```bash
GMAIL_MCP_CLIENT_ID=<id> GMAIL_MCP_CLIENT_SECRET=<secret> GMAIL_MCP_TOKEN=$(openssl rand -hex 32) \
  gmail-mcp serve --transport http --host <address>
```

The same URL, header and plain-HTTP warning apply as in B5. A token is required except on loopback, where `--no-auth` is allowed.

**The sign-in as a file.** Where there's no keychain (containers, headless Linux), the sign-in is kept in a file, `~/.config/gmail-mcp/token-cache.json`, mode 0600. It holds the refresh token and your address, isn't encrypted, and with the client secret it's enough to read your mail: keep it (or the Docker volume) where only you can. Access tokens are never saved; they're refreshed in memory about once an hour.

**Signing out.** `gmail-mcp logout` deletes the sign-in here. To revoke it at Google as well, remove gmail-mcp at [myaccount.google.com/connections](https://myaccount.google.com/connections).

**Keeping the originals.** By default, attachments are only converted to text. Set `GMAIL_MCP_DOWNLOADS` to a folder to also keep the originals there, for an agent on the same machine that needs the file itself (for example a long document whose text was cut off).

## Settings

All settings are environment variables:

| Variable | Default | What |
|---|---|---|
| `GMAIL_MCP_CLIENT_ID` | (required) | Client ID of your Google OAuth client (Desktop app) |
| `GMAIL_MCP_CLIENT_SECRET` | (required) | Its client secret |
| `GMAIL_MCP_TOKEN_CACHE` | `auto` | `auto` (keychain if there is one, else a file), `keyring` or `file` |
| `GMAIL_MCP_CACHE_FILE` | `~/.config/gmail-mcp/token-cache.json` | The sign-in file, when a file is used |
| `GMAIL_MCP_DOWNLOADS` | (none: keep nothing) | Folder to keep the originals of attachments in |
| `GMAIL_MCP_TIMEZONE` | `TZ`, else the system's | IANA time zone (e.g. `America/New_York`) for every date shown and given; an unknown name stops the server |
| `GMAIL_MCP_TOKEN` | (none) | Bearer token HTTP clients must send; at least 32 characters |
| `GMAIL_MCP_PORT` | `8767` | HTTP port |
| `GMAIL_MCP_BIND` | (required in Docker) | The address `compose.yaml` publishes the port on |

## Tools

| Tool | What it does |
|---|---|
| `whoami` | Which account is signed in, with message and thread counts |
| `mail_labels` | Labels with unread and total counts: system labels first, then categories and your own |
| `mail_search` | Search all mail, Gmail syntax (`from:amazon`, `subject:"tax"`, `has:attachment`, `newer_than:30d`, `in:anywhere`) |
| `mail_list` | Newest messages with a label (by name or id), optionally unread only or since a date |
| `mail_get` | One message in full: headers, labels, text, numbered attachments |
| `mail_thread` | A whole conversation, oldest message first. Replies leave out the earlier mails they quote, since those are shown above them |
| `mail_attachment` | One attachment as text, by its number |

How a message is read: from its original bytes (Gmail's `raw` format), with Python's email package. The body is the plain-text version if there is one, else the HTML version as Markdown; text that sits between inline pictures is kept. Everything with a file name is an attachment, including inline pictures and calendar invites. A forwarded mail attached whole counts as one attachment.

Dates in Gmail's own search operators (`after:2026/11/01`) are read by Gmail as Pacific time. `mail_list`'s `since` is in your time zone, to the minute.

## Development

```bash
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
uv run pyright
```

Tests run against a fake Gmail and never touch the network; the fake fails any test whose code sends anything but GET. Message reading is tested against real-world mail shapes in `tests/mails/`. CI also builds the Docker image, starts the compose stack and checks its lockdown.
