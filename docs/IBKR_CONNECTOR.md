# IBKR Gateway Connector

Connector 是独立 Linux 进程，不放进 FastAPI 主进程。它主动连接同机的
IBKR Gateway TCP 端口，再通过出站 WebSocket 把标准化事件发送到 AI-Trader
Server。Gateway 的账号登录由 IBKR Gateway 管理，Connector 不保存密码。

## 安装

在 Gateway 所在机器安装 Java、IBKR Gateway，并安装 Python 依赖：

```bash
python3 -m pip install aiohttp ibapi
```

复制 `deploy/ibkr-connector.service.example` 为 systemd service，并创建
`/etc/ai-trader/ibkr-connector.env`：

```dotenv
IBKR_SERVER_WS_URL=wss://trader.example.com/ws/ibkr
IBKR_CONNECTOR_TOKEN=由服务端签发的长期凭证
IBKR_GATEWAY_HOST=127.0.0.1
IBKR_GATEWAY_PORT=4002
IBKR_CLIENT_ID=207
IBKR_ACCOUNT=DU123456
IBKR_USER_ID=1
IBKR_TRADING_ACCOUNT_ID=12
IBKR_SYMBOLS=AAPL:STK,EURUSD:CASH
IBKR_READ_ONLY=true
```

`IBKR_SYMBOLS` 支持简化字符串；后台配置建议使用完整合约对象，例如：

```json
{"symbol":"AAPL","con_id":265598,"sec_type":"STK","exchange":"SMART","currency":"USD"}
```

生产环境优先使用 `con_id`，避免同名股票、期货或期权被订阅成错误合约。

服务端同时设置同一个 `IBKR_CONNECTOR_TOKEN`。Connector 只能通过出站连接
访问 `/ws/ibkr`；服务端不会反向连接 Gateway。管理员可通过带登录令牌的
`GET /admin/ibkr/connectors` 查看在线 Connector。

`IBKR_USER_ID` 用于把行情归属到 AI-Trader 用户级市场引擎；即使没有配置
`IBKR_TRADING_ACCOUNT_ID`，行情也会正常保存并计算 K 线、Pivot 和结构。
配置 `IBKR_TRADING_ACCOUNT_ID` 后，才会进一步驱动该执行账户的策略和风控。
行情会进入统一 `MarketTickIngress`，因此与 MT5
`GET /get_trades` 使用同一套 K 线、结构、计划和策略执行逻辑。

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ibkr-connector
sudo journalctl -u ibkr-connector -f
```

## 阶段边界

`READ_ONLY=true` 时服务端发来的订单命令会被拒绝。下单必须同时满足本地
`IBKR_READ_ONLY=false` 和服务端命令 `live=true`，并使用唯一 `command_id`。
Connector 已将 IBKR 的订单状态和成交回报转换为统一事件，由服务端执行回执链处理
`accepted/pending/filled/rejected/timeout/canceled`。生产开启下单前必须为每个 Connector 分配唯一
`client_id`、账号白名单、幂等 `command_id` 和熔断开关。
