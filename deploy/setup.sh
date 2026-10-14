#!/usr/bin/env bash
# Put the NUERiQ String Testing page online (Ubuntu 24.04, e.g. AWS Lightsail).
#
#   sudo bash deploy/setup.sh                 # free https address: <your-ip>.sslip.io
#   sudo bash deploy/setup.sh tests.example.com   # or your own domain (point it at the server first)
#
# What it does: installs Python packages and Caddy (automatic https), runs the
# app as a service that restarts by itself, and makes ONLY the testing page,
# the results dashboard and result saving public. Safe to run again.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${SUDO_USER:-ubuntu}"
DATA_DIR=/var/lib/stringcoach
ENV_FILE=/etc/stringcoach.env
DOMAIN="${1:-}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo:  sudo bash deploy/setup.sh"; exit 1; }

say "Checking the system"
PYV=$(python3 -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])')
if [ "$PYV" -lt 311 ]; then
  echo "This server has Python $(python3 -V | cut -d' ' -f2); version 3.11 or newer is needed."
  echo "Create the server with Ubuntu 24.04 LTS instead."
  exit 1
fi

say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -qq
apt-get install -y -qq python3-venv python3-pip curl gnupg debian-keyring debian-archive-keyring apt-transport-https >/dev/null

if ! command -v caddy >/dev/null; then
  say "Installing Caddy (web server with automatic https)"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y -qq && apt-get install -y -qq caddy >/dev/null
fi

say "Installing the app's Python packages (a few minutes the first time)"
sudo -u "$RUN_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$RUN_USER" "$APP_DIR/venv/bin/pip" install -q --upgrade pip
sudo -u "$RUN_USER" "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

say "Settings and the dashboard key"
mkdir -p "$DATA_DIR" && chown "$RUN_USER": "$DATA_DIR"
if [ ! -f "$ENV_FILE" ]; then
  KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
  printf 'STRINGCOACH_DATA=%s\nTESTER_DASHBOARD_KEY=%s\nWORKERS=1\n' "$DATA_DIR" "$KEY" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

say "Running the app as a service"
cat > /etc/systemd/system/stringcoach.service <<EOF
[Unit]
Description=NUERiQ String Testing (StringCoach server)
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/uvicorn backend.api:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q stringcoach
systemctl restart stringcoach

if [ -z "$DOMAIN" ]; then
  IP=$(curl -s --max-time 10 https://checkip.amazonaws.com || true)
  [ -n "$IP" ] || { echo "Couldn't find this server's public IP address."; exit 1; }
  DOMAIN="$(echo "$IP" | tr . -).sslip.io"
fi

say "Setting up https for $DOMAIN"
# Only the testing page, the dashboard and result saving are public; every
# other address (the full StringCoach app, its sign-up, the API docs) goes to
# the testing page.
cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
	encode gzip
	@public path /tester /tester/* /tester-api/*
	handle @public {
		reverse_proxy 127.0.0.1:8000
	}
	handle {
		redir * /tester/ 302
	}
}
EOF
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl restart caddy

say "Waiting for the app to start"
for _ in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8000/health && break; sleep 2; done
curl -s -o /dev/null http://127.0.0.1:8000/health || { echo "The app didn't start. See: sudo journalctl -u stringcoach -n 50"; exit 1; }

KEY=$(grep '^TESTER_DASHBOARD_KEY=' "$ENV_FILE" | cut -d= -f2-)
cat <<EOF

=====================================================================
 Done. The first visit can take up to a minute while the https
 certificate is issued.

 Send testers this link:   https://$DOMAIN/tester/
 Your results dashboard:   https://$DOMAIN/tester/dashboard.html
 Dashboard key:            $KEY

 Keep the key private. To see it again:
   sudo grep KEY $ENV_FILE
=====================================================================
EOF
