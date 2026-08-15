#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/ai-trader

if [[ ${EUID} -ne 0 ]]; then
  echo "This script must run as root." >&2
  exit 1
fi

# Source files must be synced with --exclude venv.  Validate dependencies
# before restarting the API so a code update cannot create an extended 502 gap.
test -x "$APP_DIR/venv/bin/python"
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
"$APP_DIR/venv/bin/python" -c 'import fastapi, uvicorn'

chown -R root:root "$APP_DIR"
chmod -R a+rX "$APP_DIR"
install -m 0644 "$APP_DIR/deploy/ai-trader.service" /etc/systemd/system/ai-trader.service
install -m 0644 "$APP_DIR/deploy/nginx-ai-trader.conf" /etc/nginx/conf.d/ai-trader.conf

nginx -t
systemctl daemon-reload
systemctl restart ai-trader
systemctl reload nginx
systemctl is-active --quiet ai-trader

echo "AI-Trader update completed."
