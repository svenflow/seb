# seb — Personal AI Assistant Daemon

**seb** is a personal AI assistant daemon running on Ubuntu 24.04 (Hetzner VPS), modeled after [svenflow/dispatch](https://github.com/svenflow/dispatch) but adapted for Linux. Reachable via Telegram and Signal. Powered by the Anthropic Agent SDK.

---

## Architecture

Three layers:

### 1. Ingestion
Two async listeners normalize messages into a unified internal format:
- `TelegramListener` — `python-telegram-bot` long polling (no phone number needed, just a BotFather token)
- `SignalListener` — connects to `signal-cli` JSON-RPC daemon over a UNIX socket (requires a dedicated phone number)

### 2. Routing (`manager.py`)
- Contact allowlist with tiers: `admin`, `trusted`, `unknown` (unknown messages silently dropped)
- Admin commands (`HEALME`, `RESTART`) intercepted before routing to Claude
- One session per contact (keyed by platform + sender ID)

### 3. Sessions (`sdk_backend.py` / `sdk_session.py`)
- Pool of `SDKSession` objects, one per contact
- Each wraps a `ClaudeSDKClient` (Anthropic Agent SDK — not the raw API)
- Messages queued via `asyncio.Queue`; mid-turn injection supported
- Session IDs persisted to `state/sessions.json` for resumption across restarts

---

## Messaging Channels

| Channel | Method | Notes |
|---|---|---|
| Telegram | Bot API long polling | BotFather token; no phone number needed |
| Signal | signal-cli JSON-RPC socket | Dedicated phone number required |
| iMessage | — | Not supported on Ubuntu |

---

## Skills System

Skills live in `~/seb/skills/` (symlinked to `~/.claude/skills/`).

Each skill is a directory containing:
- `SKILL.md` — YAML frontmatter (name, description, triggers, allowed-tools) + instructions
- `scripts/` — optional CLI executables Claude can call via Bash

Initial skills: `telegram-assistant`, `signal-assistant`, `memory`

---

## Services

- `services/memory-search/` — SQLite FTS5 HTTP daemon for persistent memory (save/load/search/consolidate)

---

## Key Design Decisions (same as sven)

- **No auto-send** — Claude must explicitly call `send-telegram` or `send-signal` tools; no automatic replies
- **In-process sessions** — all sessions as async coroutines in one event loop; no tmux overhead
- **Mid-turn steering** — new messages reach Claude between tool calls via async queue
- **Skills as symlinked modules** — shared, version-controlled, independently testable
- **`uv` only** — all Python deps in `pyproject.toml`; never bare `pip install`

---

## vs sven (macOS → Ubuntu differences)

| sven (macOS) | seb (Ubuntu 24.04) |
|---|---|
| iMessage via `chat.db` polling | — (no iMessage on Linux) |
| macOS Contacts.app for tiers | Config file / env var allowlist |
| LaunchAgents for services | systemd units |
| macOS-specific paths | Linux paths |
| Signal via signal-cli socket | Signal via signal-cli socket (same!) |

---

## Tech Stack

- Python 3.12+, `uv`
- `claude-agent-sdk` >= 0.1.48
- `python-telegram-bot`
- `python-dotenv`
- `ruff` (linting), `ty` (type checking)
- `pytest` + `pytest-asyncio`
- signal-cli native binary (no Java required)

---

## Directory Structure

```
seb/
├── PLAN.md                  # this file
├── README.md
├── pyproject.toml
├── .env.example
├── seb/
│   ├── __init__.py
│   ├── main.py              # entry point; starts all listeners + backend
│   ├── manager.py           # routing, contact tiers, admin commands
│   ├── sdk_backend.py       # session pool management
│   ├── sdk_session.py       # per-contact Claude session wrapper
│   ├── listeners/
│   │   ├── __init__.py
│   │   ├── telegram.py      # TelegramListener
│   │   └── signal.py        # SignalListener (signal-cli JSON-RPC)
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── send_telegram.py # send-telegram tool for Claude
│   │   └── send_signal.py   # send-signal tool for Claude
│   └── config.py            # loads .env, defines contact tiers
├── skills/
│   ├── telegram-assistant/
│   │   └── SKILL.md
│   ├── signal-assistant/
│   │   └── SKILL.md
│   └── memory/
│       ├── SKILL.md
│       └── scripts/
├── services/
│   └── memory-search/       # SQLite FTS5 memory HTTP daemon
├── state/
│   └── .gitkeep             # sessions.json written here at runtime
├── systemd/
│   ├── seb.service
│   └── signal-cli.service
└── scripts/
    ├── install.sh           # installs signal-cli, sets up systemd, links skills
    └── setup_signal.sh      # walks through signal-cli registration
```

---

## Setup

```bash
# 1. Clone and install deps
git clone https://github.com/sammcgrail/seb ~/seb
cd ~/seb
uv sync

# 2. Configure
cp .env.example .env
# Edit .env: TELEGRAM_BOT_TOKEN, SIGNAL_NUMBER, ADMIN_TELEGRAM_ID, ADMIN_SIGNAL_NUMBER, ANTHROPIC_API_KEY

# 3. Register Signal (if not already done)
./scripts/setup_signal.sh

# 4. Link skills
mkdir -p ~/.claude/skills
ln -sf ~/seb/skills/* ~/.claude/skills/

# 5. Install systemd services
./scripts/install.sh

# 6. Start
systemctl --user start seb
```
