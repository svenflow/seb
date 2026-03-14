#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEB_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== seb install ==="

# 1. Install uv if missing
if ! command -v uv &>/dev/null; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. Sync Python deps
echo "Installing Python dependencies..."
cd "$SEB_DIR"
uv sync

# 3. Link skills to ~/.claude/skills/
echo "Linking skills..."
mkdir -p "$HOME/.claude/skills"
for skill_dir in "$SEB_DIR/skills"/*/; do
  skill_name="$(basename "$skill_dir")"
  target="$HOME/.claude/skills/$skill_name"
  if [ -L "$target" ]; then
    rm "$target"
  fi
  ln -sf "$skill_dir" "$target"
  echo "  linked: $skill_name"
done

# 4. Install systemd user services
echo "Installing systemd services..."
mkdir -p "$HOME/.config/systemd/user"
cp "$SEB_DIR/systemd/seb.service" "$HOME/.config/systemd/user/seb.service"
cp "$SEB_DIR/systemd/signal-cli.service" "$HOME/.config/systemd/user/signal-cli.service"

systemctl --user daemon-reload
echo "  systemd services installed (not started yet)"

# 5. Enable user lingering
sudo loginctl enable-linger "$USER" 2>/dev/null || true

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. Edit ~/seb/.env with your tokens and numbers"
echo "  2. Register Signal: ./scripts/setup_signal.sh"
echo "  3. Start services:"
echo "       systemctl --user enable --now signal-cli"
echo "       systemctl --user enable --now seb"
