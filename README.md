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
- **Claude** (Anthropic API) — persistent sessions per contact, mid-turn message injection
- **Skills** — modular skill system in `~/.claude/skills/`
- **Contact tiers** — admin / trusted / unknown (unknown dropped silently)
- **No auto-send** — Claude must explicitly call `send-signal` or `send-telegram`
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

# 3. Configure
cp .env.example .env
# Edit .env — see Configuration section below

# 4. Link skills to ~/.claude/skills/
mkdir -p ~/.claude/skills
for d in ~/seb/skills/*/; do ln -sf "$d" ~/.claude/skills/; done

# 5. Install systemd services
mkdir -p ~/.config/systemd/user
cp systemd/seb.service ~/.config/systemd/user/
cp systemd/seb-status.service /etc/systemd/system/  # root service (needs docker access)
loginctl enable-linger root
systemctl --user enable --now seb
systemctl enable --now seb-status

# 6. Start Signal (Docker)
docker compose up -d signal-cli
```

---

## Configuration (`.env`)

```bash
cp .env.example .env
```

| Variable | Where to get it | Example |
|---|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys | `sk-ant-...` |
| `TELEGRAM_BOT_TOKEN` | Telegram → @BotFather → `/newbot` | `123456789:AAG...` |
| `SIGNAL_NUMBER` | The bot's Signal number | `+16173964754` |
| `SIGNAL_API_URL` | signal-cli REST API (Docker, localhost) | `http://127.0.0.1:6001` |
| `ADMIN_SIGNAL_NUMBER` | Your personal Signal number (E.164) | `+15084153544` |
| `ADMIN_TELEGRAM_ID` | Your Telegram numeric user ID (message @userinfobot) | `123456789` |
| `TRUSTED_SIGNAL_NUMBERS` | Comma-separated additional Signal numbers | `+15555550001,+15555550002` |
| `TRUSTED_TELEGRAM_IDS` | Comma-separated Telegram user IDs | `` |
| `CLAUDE_MODEL` | Claude model to use | `claude-opus-4-5` |

Only `ANTHROPIC_API_KEY`, `SIGNAL_NUMBER`, and `ADMIN_SIGNAL_NUMBER` are required to get going.

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

**3. Sessions** (`sdk_backend.py` / `sdk_session.py`) — pool of Claude sessions, one per contact, with mid-turn message injection via `asyncio.Queue`

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
│   ├── sdk_backend.py       # session pool
│   ├── sdk_session.py       # per-contact Claude session
│   ├── config.py            # .env loading, tier lookups
│   ├── listeners/
│   │   ├── telegram.py      # Telegram long polling
│   │   └── signal.py        # signal-cli-rest-api WebSocket + HTTP
│   └── tools/
│       ├── send_telegram.py
│       └── send_signal.py
├── skills/                  # symlinked to ~/.claude/skills/
├── services/
│   ├── memory-search/       # SQLite FTS5 memory HTTP daemon (port 7890)
│   └── status/              # Status dashboard HTTP server (port 8765)
├── signal-data/             # signal-cli account data (gitignored)
├── state/                   # sessions.json at runtime (gitignored)
├── systemd/                 # systemd unit files
├── docker-compose.yml       # signal-cli container
└── scripts/
    ├── install.sh
    └── setup_signal.sh
```

---

## See Also

- [PLAN.md](PLAN.md) — full architecture doc
- [svenflow/dispatch](https://github.com/svenflow/dispatch) — the macOS original this is based on
- [sammcgrail/box](https://github.com/sammcgrail/box) — server infrastructure (Caddy, Docker)
