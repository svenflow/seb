#!/usr/bin/env bash
set -euo pipefail

# Walk through signal-cli registration for a dedicated phone number.
# Requires signal-cli to be installed at /usr/local/bin/signal-cli

SIGNAL_CLI=/usr/local/bin/signal-cli
VERSION="0.14.1"

echo "=== signal-cli setup ==="
echo ""

# Install signal-cli if missing
if ! command -v signal-cli &>/dev/null; then
  echo "Installing signal-cli ${VERSION} (native binary)..."
  curl -L -O "https://github.com/AsamK/signal-cli/releases/download/v${VERSION}/signal-cli-${VERSION}-Linux-native.tar.gz"
  sudo tar -xzf "signal-cli-${VERSION}-Linux-native.tar.gz" -C /opt
  sudo ln -sf "/opt/signal-cli-${VERSION}/bin/signal-cli" /usr/local/bin/signal-cli
  rm "signal-cli-${VERSION}-Linux-native.tar.gz"
  echo "signal-cli installed."
fi

echo ""
echo "You need a dedicated phone number for Signal."
echo "Options: spare SIM, JMP.chat (XMPP/SIP), or link as secondary device."
echo ""
echo "Choose registration method:"
echo "  1) Register new phone number (SMS verification)"
echo "  2) Link as secondary device (scan QR with existing Signal app)"
echo ""
read -rp "Choice [1/2]: " choice

case "$choice" in
  1)
    read -rp "Phone number (E.164, e.g. +12025551234): " NUMBER
    echo ""
    echo "Attempting registration..."
    if ! signal-cli -a "$NUMBER" register 2>&1; then
      echo ""
      echo "If you got a captcha error, go to:"
      echo "  https://signalcaptchas.org/registration/generate.html"
      echo "Complete the captcha, copy the signal-hcaptcha:// URL, then run:"
      echo "  signal-cli -a $NUMBER register --captcha 'signal-hcaptcha://...'"
      echo ""
      read -rp "Captcha URL (or press Enter to skip): " CAPTCHA
      if [ -n "$CAPTCHA" ]; then
        signal-cli -a "$NUMBER" register --captcha "$CAPTCHA"
      fi
    fi
    echo ""
    read -rp "Enter the 6-digit verification code from SMS: " CODE
    signal-cli -a "$NUMBER" verify "$CODE"
    signal-cli -a "$NUMBER" updateProfile --given-name "seb" --family-name ""
    echo ""
    echo "Signal registered! Set SIGNAL_NUMBER=$NUMBER in ~/seb/.env"
    ;;
  2)
    echo ""
    echo "Linking as secondary device..."
    echo "Install 'qrencode' if you want a QR code: sudo apt install qrencode"
    echo ""
    LINK_URL=$(signal-cli link -n "seb-server" 2>&1 | tail -1)
    echo "Link URL: $LINK_URL"
    echo ""
    if command -v qrencode &>/dev/null; then
      qrencode -t ansiutf8 "$LINK_URL"
    fi
    echo ""
    echo "Scan this with your Signal app: Settings → Linked Devices → Link new device"
    echo "Then set SIGNAL_NUMBER=<your-number> in ~/seb/.env"
    ;;
  *)
    echo "Invalid choice."
    exit 1
    ;;
esac
