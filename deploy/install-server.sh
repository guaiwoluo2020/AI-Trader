#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/ai-trader
DATA_DIR=/var/lib/ai-trader
ENV_FILE=/etc/ai-trader.env
CREDENTIAL_FILE=/root/ai-trader-admin-credentials
PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-http://182.92.119.121/api}

if [[ ${EUID} -ne 0 ]]; then
  echo "This script must run as root." >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx python3-venv

if ! id ai-trader >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin ai-trader
fi

install -d -o ai-trader -g ai-trader -m 0750 "$DATA_DIR"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$ENV_FILE" ]]; then
  admin_password=$(openssl rand -hex 12)
  cat >"$ENV_FILE" <<EOF
PYTHONUNBUFFERED=1
AI_TRADER_DB_FILE=$DATA_DIR/ai_trader.db
AI_TRADER_DATA_DIR=$DATA_DIR
AI_TRADER_DEFAULT_ADMIN_USERNAME=admin
AI_TRADER_DEFAULT_ADMIN_PASSWORD=$admin_password
AI_TRADER_PUBLIC_BASE_URL=$PUBLIC_BASE_URL
AI_TRADER_MT5_EA_EX5=$APP_DIR/dist/mt5TerminalEA.ex5
EOF
  chmod 0600 "$ENV_FILE"
  printf 'username=admin\npassword=%s\n' "$admin_password" >"$CREDENTIAL_FILE"
  chmod 0600 "$CREDENTIAL_FILE"
fi

if ! grep -q '^AI_TRADER_DATA_DIR=' "$ENV_FILE"; then
  printf '\nAI_TRADER_DATA_DIR=%s\n' "$DATA_DIR" >>"$ENV_FILE"
fi

chown -R root:root "$APP_DIR"
chmod -R a+rX "$APP_DIR"
chown -R ai-trader:ai-trader "$DATA_DIR"

install -m 0644 "$APP_DIR/deploy/ai-trader.service" /etc/systemd/system/ai-trader.service
install -m 0644 "$APP_DIR/deploy/nginx-ai-trader.conf" /etc/nginx/conf.d/ai-trader.conf
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable --now ai-trader
systemctl enable --now nginx
systemctl restart ai-trader nginx

echo "AI-Trader deployment completed."
echo "Admin credentials: $CREDENTIAL_FILE"
