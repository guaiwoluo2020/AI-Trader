#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .env.local ]]; then
  set -a
  source .env.local
  set +a
fi
if [[ -z "${FEISHU_APP_SECRET:-}" ]]; then
  read -r "FEISHU_APP_SECRET?请粘贴飞书 App Secret（不会显示，也不会写入聊天）： "
  export FEISHU_APP_SECRET
fi
export FEISHU_APP_ID="${FEISHU_APP_ID:-cli_aa30f44f16789bda}"
exec node server.mjs
