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
"$APP_DIR/venv/bin/python" -c 'import fastapi, uvicorn, pymysql'
set -a
source /etc/ai-trader.env
set +a

# Production storage is MySQL. Do not run local-file database initialization
# during deploy; it can fail before the service restart on a MySQL-only host.
"$APP_DIR/venv/bin/python" -c 'import pymysql'

# Keep the persisted public structure defaults in step with the code defaults.
# Only replace the previous shipped values, so an administrator's deliberate
# override is preserved across deployments.
"$APP_DIR/venv/bin/python" - <<'PY'
import json
import os
import pymysql

keys = {
    "location_reclaim_min_body_atr": (0.3, 0.5),
    "location_reclaim_min_close_extension_atr": (0.1, 0.2),
    "location_proximity_atr": (0.6, 0.4),
}

def update(layer):
    changed = False
    if not isinstance(layer, dict):
        return layer, changed
    for key, value in list(layer.items()):
        if key in keys and isinstance(value, (int, float)):
            old, new = keys[key]
            if float(value) == old:
                layer[key] = new
                changed = True
        elif isinstance(value, dict):
            layer[key], nested = update(value)
            changed = changed or nested
    return layer, changed

conn = pymysql.connect(
    host=os.environ["AI_TRADER_MYSQL_HOST"],
    port=int(os.environ.get("AI_TRADER_MYSQL_PORT", "3306")),
    user=os.environ["AI_TRADER_MYSQL_USER"],
    password=os.environ["AI_TRADER_MYSQL_PASSWORD"],
    database=os.environ["AI_TRADER_MYSQL_DATABASE"],
    charset="utf8mb4",
    autocommit=False,
)
try:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT config_json FROM structure_default_configs "
            "WHERE user_id=0 AND status='active' FOR UPDATE"
        )
        row = cur.fetchone()
        if row:
            config = row[0]
            if isinstance(config, str):
                config = json.loads(config)
            config, changed = update(config)
            if changed:
                cur.execute(
                    "UPDATE structure_default_configs SET config_json=%s, "
                    "version=version+1, updated_at=UNIX_TIMESTAMP() "
                    "WHERE user_id=0 AND status='active'",
                    (json.dumps(config, ensure_ascii=False),),
                )
    conn.commit()
finally:
    conn.close()
PY

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
