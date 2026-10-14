#!/usr/bin/env bash
# Update the live server after pushing new code to GitHub:
#   cd ~/Preetham-string && sudo bash deploy/update.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${SUDO_USER:-ubuntu}"
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo:  sudo bash deploy/update.sh"; exit 1; }
cd "$APP_DIR"
sudo -u "$RUN_USER" git pull --ff-only
sudo -u "$RUN_USER" "$APP_DIR/venv/bin/pip" install -q -r requirements.txt
systemctl restart stringcoach
for _ in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8000/health && break; sleep 2; done
curl -s -o /dev/null http://127.0.0.1:8000/health && echo "Updated and running. Testers may need to refresh the page (Ctrl+Shift+R)." \
  || { echo "The app didn't start. See: sudo journalctl -u stringcoach -n 50"; exit 1; }
