# seb

Personal AI assistant daemon running on Ubuntu 24.04 (Hetzner). Text it via Signal; it responds using Claude. Telegram support coming.

Modeled after [svenflow/dispatch](https://github.com/svenflow/dispatch) — same architecture, adapted for Linux.

---

## Status Dashboard

**http://204.168.135.168/seb** — live service status + recent logs, auto-refreshes every 15s.

---

## Features

- **Signal** — via signal-cli REST API Docker container (+1 617-396-4754)
- **Telegram** — coming soon
- **Claude Agent SDK** — persistent sessions per contact via Claude Code subprocess (OAuth, no API credits)
- **Skills** — modular skill system in `~/.claude/skills/`
- **Contact tiers** — admin / trusted / unknown (unknown dropped silently)
- **No auto-send** — Claude must explicitly call `send-signal` or `send-telegram` scripts
- **Session persistence** — sessions resume across daemon restarts via `state/sessions.json`

---

## Setup

```bash
git clone https://github.com/sammcgrail/seb ~/seb
cd ~/seb

# 1. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install Python deps
~/.local/bin/uv sync

# 3. Install Claude Code CLI and authenticate
npm install -g @anthropic-ai/claude-code
claude login  # Opens browser for OAuth — uses your Claude Pro/Max subscription

# 4. Configure
cp .env.example .env
# Edit .env — see Configuration section below

# 5. Link skills to ~/.claude/skills/
mkdir -p ~/.claude/skills
for d in ~/seb/skills/*/; do ln -sf "$d" ~/.claude/skills/; done

# 6. Install systemd services
mkdir -p ~/.config/systemd/user
cp systemd/seb.service ~/.config/systemd/user/
cp systemd/seb-status.service /etc/systemd/system/  # root service (needs docker access)
loginctl enable-linger root
systemctl --user enable --now seb
systemctl enable --now seb-status

# 7. Start Signal (Docker)
docker compose up -d signal-cli
```

---

## Configuration (`.env`)

```bash
cp .env.example .env
```

| Variable | Where to get it | Example |
|---|---|---|
| `CLAUDE_MODEL` | Model choice: `haiku` (default), `sonnet`, `opus` | `haiku` |
| `TELEGRAM_BOT_TOKEN` | Telegram → @BotFather → `/newbot` | `123456789:AAG...` |
| `SIGNAL_NUMBER` | The bot's Signal phone number | `+16173964754` |
| `SIGNAL_API_URL` | signal-cli REST API (Docker, localhost) | `http://127.0.0.1:6001` |
| `ADMIN_SIGNAL_NUMBER` | Your personal Signal number (E.164) | `+15084153544` |
| `ADMIN_TELEGRAM_ID` | Your Telegram numeric user ID (message @userinfobot) | `123456789` |
| `TRUSTED_SIGNAL_NUMBERS` | Comma-separated additional Signal numbers | `+15555550001,+15555550002` |
| `TRUSTED_TELEGRAM_IDS` | Comma-separated Telegram user IDs | `` |

**No `ANTHROPIC_API_KEY` needed!** The Agent SDK uses OAuth via `claude login`. Usage comes from your Claude Pro ($20/mo) or Max ($100/mo) subscription.

Only `SIGNAL_NUMBER` and `ADMIN_SIGNAL_NUMBER` are required to get going.

---

## Logs

### Terminal / SSH

```bash
# Follow live logs
journalctl --user -u seb -f

# Last 100 lines
journalctl --user -u seb -n 100 --no-pager

# Signal-cli Docker logs
docker compose -f ~/seb/docker-compose.yml logs -f signal-cli
```

### VSCode (SSH remote)

1. Connect to the server via SSH remote in VSCode
2. Open a terminal (`Ctrl+` `` ` ``)
3. Run `journalctl --user -u seb -f` — logs stream inline
4. Or install the **[Log Viewer](https://marketplace.visualstudio.com/items?itemName=berublan.vscode-log-viewer)** extension and point it at `/tmp/seb.log` if you redirect logs there

### Status dashboard

**http://204.168.135.168/seb** — accessible from anywhere, auto-refreshes every 15s.

---

## Service management

```bash
# Status
systemctl --user status seb

# Restart
systemctl --user restart seb

# Stop
systemctl --user stop seb

# View all recent logs
journalctl --user -u seb -n 200 --no-pager
```

### Admin commands (send via Signal)

| Message | Effect |
|---|---|
| `RESTART` | Save sessions and exit (systemd auto-restarts) |
| `HEALME` | Reset all active sessions |

---

## Architecture

Three layers:

**1. Ingestion** — async listeners normalize messages to a common format:
- `SignalListener` — WebSocket connection to signal-cli-rest-api Docker container
- `TelegramListener` — python-telegram-bot long polling (when token configured)

**2. Routing** (`manager.py`) — tier lookup (admin/trusted/unknown), admin command interception

**3. Sessions** (`sdk_backend.py` / `sdk_session.py`) — pool of Claude Agent SDK sessions, one per contact. Each session runs as a Claude Code subprocess with:
- OAuth authentication (no API credits)
- Native tool access (Bash, Read, Write, Grep, etc.)
- Session persistence and resume
- Per-contact transcript directories

Claude sends replies by calling `scripts/send-signal` or `scripts/send-telegram` via the Bash tool.

---

## Key Changes from v0.1 (Anthropic API)

| Before (v0.1) | After (v0.2) |
|---|---|
| Raw `anthropic` Python SDK | Claude Agent SDK (`claude-agent-sdk`) |
| API key (`ANTHROPIC_API_KEY`) | OAuth via `claude login` (subscription) |
| Manual agentic loop + tool execution | SDK manages turns, tools, persistence |
| Custom tool definitions (send-signal, send-telegram, Bash) | Native Claude Code tools + send scripts |
| Manual message history management | SDK handles conversation state |
| API credits per token | Included in Claude Pro/Max subscription |

---

## Infrastructure

| Component | How it runs |
|---|---|
| `seb` daemon | systemd user service (`~/.config/systemd/user/seb.service`) |
| `signal-cli` | Docker (`bbernhard/signal-cli-rest-api`, json-rpc mode) |
| Status page | systemd service (`/etc/systemd/system/seb-status.service`) on port 8765 |
| Caddy (web) | Docker via [sammcgrail/box](https://github.com/sammcgrail/box), proxies `/seb` → port 8765 |

Signal account data lives in `~/seb/signal-data/` (gitignored — back it up).

---

## Structure

```
seb/
├── seb/
│   ├── main.py              # entry point
│   ├── manager.py           # routing + tiers
│   ├── sdk_backend.py       # session pool (Agent SDK)
│   ├── sdk_session.py       # per-contact Claude session (ClaudeSDKClient)
│   ├── config.py            # .env loading, tier lookups
│   ├── listeners/
│   │   ├── telegram.py      # Telegram long polling
│   │   └── signal.py        # signal-cli-rest-api WebSocket + HTTP
│   └── tools/               # (legacy — kept for reference)
│       ├── send_telegram.py
│       └── send_signal.py
├── scripts/
│   ├── send-signal           # CLI: send Signal message (called by Claude via Bash)
│   ├── send-telegram         # CLI: send Telegram message (called by Claude via Bash)
│   ├── install.sh
│   └── setup_signal.sh
├── skills/                  # symlinked to ~/.claude/skills/
├── services/
│   ├── memory-search/       # SQLite FTS5 memory HTTP daemon (port 7890)
│   └── status/              # Status dashboard HTTP server (port 8765)
├── transcripts/             # per-contact session directories (created at runtime)
├── signal-data/             # signal-cli account data (gitignored)
├── state/                   # sessions.json at runtime (gitignored)
├── systemd/                 # systemd unit files
└── docker-compose.yml       # signal-cli container
```

---

## See Also

- [PLAN.md](PLAN.md) — full architecture doc
- [svenflow/dispatch](https://github.com/svenflow/dispatch) — the macOS original this is based on
- [sammcgrail/box](https://github.com/sammcgrail/box) — server infrastructure (Caddy, Docker)
