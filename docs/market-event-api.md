# Hermes Agent 市场事件数据上报指南

本文档用于指导 Hermes Agent 将外部采集的金十数据写入 AI Trader 的公共市场事件服务。
市场事件数据保存在 SQLite 中，对所有已登录用户共享。

## 1. 接入目标

Hermes Agent 需要维护三类数据：

| 数据类型 | 写入方式 | 写入接口 | 是否 WebSocket 推送 |
| --- | --- | --- | --- |
| 财经日历 | 指定日期全量覆盖 | `POST /news/calendar/daily` | 否 |
| 关键事件 | 指定日期全量覆盖 | `POST /news/key-events/daily` | 否 |
| 市场快讯 | 按 ID 增量新增或更新 | `POST /news/flash` | 是 |

“指定日期全量覆盖”表示服务端在同一个 SQLite 事务中，先删除该日期的已有数据，
再插入本次请求的全部数据。不要只发送当天发生变化的部分数据。

## 2. 服务地址

所有示例使用 `API_BASE` 表示 API 基地址：

```bash
# 本地直接访问后端
export API_BASE="http://127.0.0.1:8000"

# 生产环境经过 /api 反向代理时
export API_BASE="https://your-domain.example/api"
```

生产环境最终地址示例：

```text
https://your-domain.example/api/news/calendar/daily
https://your-domain.example/api/news/key-events/daily
https://your-domain.example/api/news/flash
```

## 3. 管理员认证

三个写入接口只允许 AI Trader 管理员调用。Hermes Agent 不应保存网页登录后的临时页面状态，
管理员也统一使用邮箱验证码登录。无人值守 Agent 应由管理员安全提供短期 Bearer Token；
后续建议为采集服务单独实现可撤销的 API Key，不要保存邮箱验证码。

### 3.1 登录

```http
POST {API_BASE}/auth/login/email-code
Content-Type: application/json
```

请求：

```json
{
  "email": "175821555@qq.com"
}
```

成功响应：

```json
{
  "status": "ok",
  "token": "<bearer-token>",
  "expires_in": 43200,
  "user": {
    "username": "admin",
    "role": "admin"
  },
  "next_path": "/"
}
```

邮箱收到验证码后，再登录：

```http
POST {API_BASE}/auth/login/email
Content-Type: application/json
```

```bash
curl --fail-with-body \
  -X POST "$API_BASE/auth/login/email" \
  -H "Content-Type: application/json" \
  -d '{"email":"175821555@qq.com","verification_code":"<6-digit-code>"}'
```

### 3.2 写入请求头

后续三个上报请求都必须携带：

```http
Authorization: Bearer <bearer-token>
Content-Type: application/json
```

Token 过期并收到 `401` 后，Hermes Agent 应重新登录并只重试失败的请求。普通用户 Token
调用写入接口会收到 `403`。

## 4. 通用数据约定

| 约定 | 要求 |
| --- | --- |
| 字符编码 | UTF-8 JSON |
| 日期 | `YYYY-MM-DD`，例如 `2026-08-09` |
| 时间 | 推荐 ISO 8601 且包含时区，例如 `2026-08-09T20:30:00+08:00` |
| 来源 | 顶层传入 `"source": "jin10"` |
| 单次数量 | 每个数组最多 2000 条 |
| 重要性 | `0-3`；超出范围时服务端会归一到该范围 |
| 关联品种 | 字符串数组，服务端会去空值并去重 |
| 扩展字段 | 未被标准化的金十原始字段会原样保存在 `payload_json` 中 |

数组字段既可以使用本文档推荐名称，也可以直接使用 `data`：

```json
{"date":"2026-08-09","source":"jin10","data":[...]}
```

日历和关键事件中，同一天内的 `id` 必须唯一。建议始终使用金十返回的原始 ID，避免同一事件
因标题变化产生新记录。

## 5. 财经日历按天上报

### 5.1 接口

```http
POST {API_BASE}/news/calendar/daily
```

### 5.2 请求结构

```json
{
  "date": "2026-08-09",
  "source": "jin10",
  "events": [
    {
      "id": "calendar-10001",
      "name": "美国7月CPI年率",
      "country": "美国",
      "currency": "USD",
      "time": "2026-08-09T20:30:00+08:00",
      "star": 3,
      "previous": "2.7%",
      "consensus": "2.8%",
      "actual": "",
      "unit": "%",
      "symbols": ["GOLD", "USDJPY"]
    }
  ]
}
```

### 5.3 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `date` | 是 | 本次覆盖的数据日期 |
| `source` | 否 | 建议固定为 `jin10`，默认 `external` |
| `events` / `data` | 是 | 当天完整财经日历数组，可为空数组 |
| `id` | 否 | 事件 ID；缺失时服务端生成稳定 ID |
| `name` / `title` | 是 | 事件名称 |
| `time` | 否 | 金十时间字段，可用 `publish_time` 或 `event_time` 代替 |
| `star` | 否 | 金十重要性字段，可用 `importance` 代替 |
| `consensus` | 否 | 金十预期值字段，可用 `forecast` 代替 |
| `previous` | 否 | 前值 |
| `actual` | 否 | 公布值，尚未公布时传空字符串 |
| `country` | 否 | 国家或地区 |
| `currency` | 否 | 关联货币代码 |
| `unit` | 否 | 数据单位 |
| `symbols` | 否 | 关联交易品种 |

成功响应：

```json
{
  "status": "ok",
  "message": "2026-08-09 财经日历已覆盖",
  "date": "2026-08-09",
  "count": 1
}
```

传入空数组会清空当天财经日历：

```json
{"date":"2026-08-09","source":"jin10","events":[]}
```

### 5.4 查询校验

```http
GET {API_BASE}/news/calendar?date=2026-08-09
Authorization: Bearer <bearer-token>
```

```bash
curl --fail-with-body \
  "$API_BASE/news/calendar?date=2026-08-09" \
  -H "Authorization: Bearer $TOKEN"
```

## 6. 关键事件按天上报

### 6.1 接口

```http
POST {API_BASE}/news/key-events/daily
```

### 6.2 请求结构

```json
{
  "date": "2026-08-09",
  "source": "jin10",
  "events": [
    {
      "id": "important-20001",
      "title": "美联储主席发表讲话",
      "time": "2026-08-09T22:00:00+08:00",
      "category": "央行动态",
      "importance": 3,
      "summary": "关注对通胀和利率路径的表述",
      "symbols": ["GOLD", "SPX", "USDJPY"]
    }
  ]
}
```

### 6.3 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `date` | 是 | 本次覆盖的数据日期 |
| `source` | 否 | 建议固定为 `jin10` |
| `events` / `data` | 是 | 当天完整关键事件数组，可为空数组 |
| `id` | 否 | 关键事件 ID；缺失时服务端生成稳定 ID |
| `title` | 是 | 标题，可使用 `name` 或 `content` 代替 |
| `time` | 否 | 发生时间，可使用 `event_time` 或 `publish_time` 代替 |
| `category` | 否 | 事件分类，例如央行动态、地缘政治、能源 |
| `importance` / `star` | 否 | 重要性 `0-3` |
| `summary` | 否 | 事件摘要，也可保留 `description` 或 `content` |
| `symbols` | 否 | 关联交易品种 |

成功响应：

```json
{
  "status": "ok",
  "message": "2026-08-09 关键事件已覆盖",
  "date": "2026-08-09",
  "count": 1
}
```

### 6.4 查询校验

```http
GET {API_BASE}/news/key-events?date=2026-08-09
Authorization: Bearer <bearer-token>
```

## 7. 市场快讯实时上报

### 7.1 接口

```http
POST {API_BASE}/news/flash
```

### 7.2 请求结构

```json
{
  "source": "jin10",
  "items": [
    {
      "id": "30001",
      "content": "美联储官员表示仍需关注通胀上行风险。",
      "time": "2026-08-09T22:05:12+08:00",
      "importance": 2,
      "keywords": ["美联储", "通胀"],
      "symbols": ["GOLD", "SPX"]
    }
  ]
}
```

### 7.3 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `source` | 否 | 建议固定为 `jin10` |
| `items` / `data` | 是 | 本次新增或更新的快讯数组 |
| `id` | 强烈建议 | 金十快讯 ID；相同 ID 会更新已有记录 |
| `content` | 是 | 快讯正文，可使用 `title` 代替 |
| `time` | 否 | 发布时间，可使用 `published_at` 或 `create_time` 代替 |
| `importance` / `star` | 否 | 重要性 `0-3` |
| `keywords` | 否 | 金十关键词数组，原样保留 |
| `symbols` | 否 | 关联品种；也兼容 `related_symbols` |

没有传 `id` 时，服务端根据发布时间和正文生成稳定 ID。为了避免正文轻微修改产生重复快讯，
Hermes Agent 应优先传入金十原始 ID。

成功响应：

```json
{
  "status": "ok",
  "message": "市场快讯已写入",
  "count": 1
}
```

同一个 ID 重复上报是安全的：服务端会更新原记录，不会新增重复记录。数据库写入成功后，
本次 `items` 会立即广播给市场事件页面。

### 7.4 查询校验

```http
GET {API_BASE}/news/flash?limit=100
Authorization: Bearer <bearer-token>
```

`limit` 允许 `1-500`，默认 `100`。

## 8. WebSocket 快讯消息

Hermes Agent 只负责 HTTP 上报，不需要主动连接 WebSocket。本节用于联调前台实时效果。

WebSocket 地址：

```text
本地：ws://127.0.0.1:8000/news/ws
生产：wss://your-domain.example/api/news/ws
```

连接后必须在 10 秒内发送登录消息：

```json
{"type":"auth","token":"<user-token>"}
```

登录成功：

```json
{
  "type": "connected",
  "message": "已连接到公共市场事件服务",
  "user_id": 1
}
```

市场快讯写入后的广播：

```json
{
  "type": "market_flash_news_updated",
  "count": 1,
  "items": [
    {
      "id": "30001",
      "content": "美联储官员表示仍需关注通胀上行风险。",
      "published_at": "2026-08-09T22:05:12+08:00",
      "importance": 2,
      "source": "jin10"
    }
  ],
  "updated_at": 1786284312
}
```

财经日历和关键事件写入不会触发 WebSocket 广播。

## 9. 错误处理

| HTTP 状态码 | 原因 | Hermes Agent 处理方式 |
| --- | --- | --- |
| `400` | 日期、数组或字段格式错误 | 修正数据，不要原样无限重试 |
| `401` | Token 缺失、无效或过期 | 重新登录，然后重试一次 |
| `403` | 登录用户不是管理员 | 停止任务并报告权限配置错误 |
| `422` | 请求结构不满足 FastAPI 校验 | 修正请求结构 |
| `500` | 数据库或服务端异常 | 指数退避重试并告警 |

推荐重试间隔为 `2s、5s、15s、30s`，最多重试 4 次。按天覆盖接口必须重试完整的
当天数据集；市场快讯必须保持原 ID 重试。

## 10. Hermes Agent 推荐执行流程

1. 从安全的环境变量读取 `API_BASE`、管理员用户名和密码，禁止把密码写入日志。
2. 调用登录接口取得 Token，并根据 `expires_in` 提前更新。
3. 每天抓取目标日期完整财经日历，清洗后调用财经日历接口。
4. 每天抓取目标日期完整关键事件，清洗后调用关键事件接口。
5. 持续轮询市场快讯，只把新出现或内容发生变化的 ID 组成批次上报。
6. 检查每次 POST 返回的 `status` 和 `count`，非 `ok` 视为失败。
7. 日数据上报后调用对应 GET 接口，确认返回 `count` 与本次完整数组长度一致。
8. 快讯上报后可抽样调用 GET 接口，确认最新 ID 已写入。
9. 日历或关键事件抓取失败时，不要发送空数组，否则会清空当天已有数据。
10. 只有确定来源当天确实没有数据时，才使用空数组清空当天记录。

## 11. 可直接交给 Hermes Agent 的任务摘要

```text
你负责从金十数据采集并向 AI Trader 上报公共市场事件。

配置：
- API_BASE 从环境变量读取。
- 使用管理员邮箱验证码登录取得短期 Bearer Token，或由管理员安全提供 Token。
- 所有请求使用 UTF-8 JSON，source 固定为 jin10，时间使用带时区的 ISO 8601。

任务：
1. 财经日历：POST {API_BASE}/news/calendar/daily。按 date 上报当天完整 events；
   服务端会先删除该日期旧数据再插入。抓取失败时禁止发送空数组。
2. 关键事件：POST {API_BASE}/news/key-events/daily。按 date 上报当天完整 events；
   规则与财经日历相同。
3. 市场快讯：POST {API_BASE}/news/flash。使用 items 增量上报，必须尽量保留金十原始 id；
   相同 id 可以安全重试并更新，写入成功后服务端会通过 WebSocket 推送前台。
4. 401 时重新登录后重试一次；403 立即停止；400/422 修正数据；500 使用指数退避。
5. 每次记录接口、日期、发送数量、响应数量和耗时，但不得记录密码或完整 Token。
```
