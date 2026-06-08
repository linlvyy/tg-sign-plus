# TG-Sign-Plus

<div align="center">

**功能强大的 Telegram 自动化任务管理平台**

[![License](https://img.shields.io/badge/license-BSD--3--Clause-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109.2+-green.svg)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-14.2+-black.svg)](https://nextjs.org/)

[功能特性](#功能特性) • [快速开始](#快速开始) • [部署方式](#部署方式) • [使用文档](#使用文档) • [配置说明](#配置说明)

</div>

---

## 📖 项目简介

TG-Sign-Plus 是一个基于 Telegram 的自动化任务管理平台，提供 Web 管理界面和 CLI 工具，支持自动签到、消息监控、AI 智能回复等功能。

### 核心能力

- 🤖 **自动化任务执行**：支持定时签到、发送消息、点击按钮等多种自动化操作
- 🧠 **AI 智能处理**：集成 OpenAI API，支持图片识别、计算题求解、诗词填空等 AI 功能
- 📊 **Web 管理界面**：现代化的 Next.js 前端，提供直观的任务配置和监控
- 🔐 **安全认证**：支持 JWT + 双因素认证（2FA/TOTP）
- 📡 **消息监控转发**：实时监控群组/频道消息，支持 UDP/HTTP 转发
- 🐳 **容器化部署**：提供 Docker 镜像，支持一键部署

---

## 📸 项目预览

<div align="center">

### 登录界面
![登录界面](assets/login.jpeg)

### 控制台
![控制台](assets/dashboard.jpeg)

### 任务管理
![任务管理](assets/tasks.jpeg)

</div>

---

## ✨ 功能特性

### 自动化任务

- **签到任务**
  - 定时自动签到（支持 Cron 表达式）
  - 多账号、多群组批量管理
  - 随机延迟执行，模拟真实用户行为
  - 支持发送文本、骰子表情、点击按钮等操作

- **AI 增强功能**
  - 图片识别选择选项
  - 自动解答计算题
  - 诗词填空智能匹配
  - 图片文字识别并回复

- **消息监控**
  - 实时监控私聊/群组/频道消息
  - 支持正则表达式、关键词匹配
  - 自动回复或转发到指定聊天
  - 支持 Server酱 推送通知
  - 支持 UDP/HTTP 外部转发

### 管理功能

- **Web 控制台**
  - 账号管理（登录、登出、会话管理）
  - 任务配置（可视化编辑签到流程）
  - 执行历史查看
  - 实时日志监控

- **CLI 工具**
  - 命令行快速操作
  - 批量任务管理
  - 配置导入导出

---

## 🚀 快速开始

### 前置要求

- Python 3.10+
- Node.js 20+（仅开发环境需要）
- Telegram API 凭证（api_id 和 api_hash）

### 获取 Telegram API 凭证

1. 访问 [https://my.telegram.org/apps](https://my.telegram.org/apps)
2. 登录你的 Telegram 账号
3. 创建应用获取 `api_id` 和 `api_hash`

### 本地开发

#### 1. 克隆项目

```bash
git clone https://github.com/ssfun/tg-sign-plus.git
cd tg-sign-plus
```

#### 2. 安装后端依赖

```bash
pip install -e .
```

#### 3. 启动后端服务

```bash
# 设置环境变量
export APP_SECRET_KEY="your-secret-key-here"
export TG_API_ID="your-api-id"
export TG_API_HASH="your-api-hash"

# 启动 FastAPI 服务
uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

#### 4. 启动前端（开发模式）

```bash
cd frontend
npm install
npm run dev
```

访问 `http://localhost:3000` 即可使用 Web 界面。

---

## 🐳 部署方式

### Docker 部署（推荐）

#### 使用 Docker Compose

```bash
# 创建 docker-compose.yml
cat > docker-compose.yml <<EOF
version: '3.8'

services:
  tg-signer:
    image: sfun/tg-sign-plus:latest
    container_name: tg-sign-plus
    ports:
      - "8080:8080"
    volumes:
      - ./data:/data
    environment:
      - APP_SECRET_KEY=your-secret-key-here
      - TG_API_ID=your-api-id
      - TG_API_HASH=your-api-hash
      - TZ=Asia/Shanghai
      # 如通过 HTTPS 反向代理访问，建议开启：
      # - APP_REFRESH_COOKIE_SECURE=true
      # 可选：AI 功能配置
      - OPENAI_API_KEY=your-openai-key
      - OPENAI_BASE_URL=https://api.openai.com/v1
      - OPENAI_MODEL=gpt-4o-mini
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
EOF

# 启动服务
docker-compose up -d
```

#### 使用 Docker 命令

```bash
docker run -d \
  --name tg-sign-plus \
  -p 8080:8080 \
  -v $(pwd)/data:/data \
  -e APP_SECRET_KEY=your-secret-key-here \
  -e TG_API_ID=your-api-id \
  -e TG_API_HASH=your-api-hash \
  -e TZ=Asia/Shanghai \
  --restart unless-stopped \
  sfun/tg-sign-plus:latest
```

如果使用 `http://服务器IP:8080` 或局域网 IP 直接访问，请保持
`APP_REFRESH_COOKIE_SECURE` 未设置或为 `false`。否则浏览器不会保存登录后
下发的 CSRF cookie，后续保存配置、修改用户名等写操作会返回 403。
如通过 HTTPS 反向代理访问，可在 `docker run` 中额外添加
`-e APP_REFRESH_COOKIE_SECURE=true`。

### 构建自定义镜像

```bash
# 克隆项目
git clone https://github.com/ssfun/tg-sign-plus.git
cd tg-sign-plus

# 构建镜像
docker build -t tg-sign-plus:custom .

# 运行
docker run -d \
  --name tg-sign-plus \
  -p 8080:8080 \
  -v $(pwd)/data:/data \
  -e APP_SECRET_KEY=your-secret-key-here \
  tg-sign-plus:custom
```

### 低内存环境部署（512MB，如 Render 免费套餐）

在内存受限的平台上部署时，建议添加以下环境变量以优化内存使用：

```bash
# 降低 Telegram channel diff 并发数（默认 2，极端情况可设为 1）
TG_CHANNEL_DIFF_CONCURRENCY=2

# 限制全局任务并发
TG_GLOBAL_CONCURRENCY=1

# 限制 Telegram RPC 重试和连接等待时间，避免网络异常时长时间堆积后台任务
TG_RPC_RETRIES=2
TG_RPC_TIMEOUT=30
TG_CALLBACK_RETRIES=3
TG_SEND_MESSAGE_TIMEOUT=20
TG_SUCCESS_ASSERT_TIMEOUT=30
TG_AI_REQUEST_TIMEOUT=45
TG_CONNECT_TIMEOUT=20
TG_TCP_TIMEOUT=8
TG_SLEEP_THRESHOLD=120
TG_WORKERS=16
SIGN_TASK_RUN_TIMEOUT=180

# 低内存排障时可强制关闭后端签到实时 updates；按钮/回复类签到可能失败，默认不要设置
# TG_SIGN_TASK_DISABLE_UPDATES=true

# 减少 glibc 内存碎片（已内置于 Docker 镜像）
MALLOC_ARENA_MAX=2
```

如果账号加入了大量群组（100+），启动时可能触发大量 `GetChannelDifference` 请求导致内存峰值。上述配置通过限制并发数来平滑内存使用。

---

## 📚 使用文档

### CLI 命令

```bash
# 查看帮助
tg-signer --help

# 登录账号
tg-signer login my_account

# 配置签到任务
tg-signer config my_account my_task

# 执行签到任务
tg-signer run my_account my_task

# 单次执行（不等待定时）
tg-signer run-once my_account my_task

# 配置 AI 模型
tg-signer llm-config

# 发送消息
tg-signer send my_account --chat-id 123456 --text "Hello"

# 查看任务列表
tg-signer list my_account
```

### Web 界面使用

1. **首次登录**
   - 访问 `http://localhost:8080`
   - 使用默认管理员账号登录（首次启动会自动创建）
   - 建议立即修改密码并启用 2FA

2. **添加 Telegram 账号**
   - 进入「账号管理」页面
   - 点击「添加账号」
   - 输入手机号，接收验证码完成登录

3. **配置签到任务**
   - 选择账号，进入「任务管理」
   - 点击「新建任务」
   - 配置签到时间、目标群组、执行动作
   - 保存并启用任务

4. **查看执行历史**
   - 在「任务历史」页面查看执行记录
   - 支持查看详细日志和错误信息

### 签到任务配置示例

#### 简单文本签到

```json
{
  "chats": [
    {
      "chat_id": -1001234567890,
      "name": "示例群组",
      "actions": [
        {
          "action": 1,
          "text": "/签到"
        }
      ],
      "delete_after": 5
    }
  ],
  "sign_at": "0 6 * * *",
  "random_seconds": 300
}
```

#### 带按钮点击的签到

```json
{
  "chats": [
    {
      "chat_id": -1001234567890,
      "actions": [
        {
          "action": 1,
          "text": "/start"
        },
        {
          "action": 3,
          "text": "签到"
        }
      ]
    }
  ],
  "sign_at": "0 8 * * *"
}
```

#### AI 图片识别签到

```json
{
  "chats": [
    {
      "chat_id": -1001234567890,
      "actions": [
        {
          "action": 1,
          "text": "/checkin"
        },
        {
          "action": 4
        }
      ]
    }
  ],
  "sign_at": "0 9 * * *"
}
```

---

## ⚙️ 配置说明

### 环境变量

#### 必需配置

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `APP_SECRET_KEY` | JWT 密钥（生产环境必须设置） | `your-random-secret-key` |
| `TG_API_ID` | Telegram API ID | `12345678` |
| `TG_API_HASH` | Telegram API Hash | `abcdef1234567890` |

#### 可选配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `PORT` | 服务端口 | `8080` |
| `TZ` | 时区 | `Asia/Shanghai` |
| `BASE_DIR` | 数据目录 | `/data` |
| `DATABASE_URL` | 数据库连接（支持 PostgreSQL）；未设置时使用可写数据目录下的 `db.sqlite`，Docker 通常为 `/data/db.sqlite` | `sqlite:////data/db.sqlite` |
| `APP_REFRESH_COOKIE_SECURE` | 仅通过 HTTPS 访问时设为 `true`；HTTP/IP 直连需保持 `false`，否则写操作会因 CSRF cookie 丢失返回 403 | `false` |
| `OPENAI_API_KEY` | OpenAI API 密钥 | - |
| `OPENAI_BASE_URL` | OpenAI API 地址 | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | 使用的模型 | `gpt-4o-mini` |
| `SERVER_CHAN_SEND_KEY` | Server酱推送密钥 | - |
| `TG_CHANNEL_DIFF_CONCURRENCY` | Telegram GetChannelDifference 并发数 | `2` |
| `TG_GLOBAL_CONCURRENCY` | Telegram 任务全局并发数 | `1` |
| `TG_RPC_RETRIES` | Telegram RPC 默认重试次数 | `2` |
| `TG_RPC_TIMEOUT` | Telegram RPC 默认超时秒数 | `30` |
| `TG_CALLBACK_RETRIES` | Telegram 按钮回调超时重试次数 | `3` |
| `TG_SEND_MESSAGE_TIMEOUT` | 发送 Telegram 文本消息超时秒数 | `20` |
| `TG_SUCCESS_ASSERT_TIMEOUT` | 结果关键词等待秒数 | `30` |
| `TG_AI_REQUEST_TIMEOUT` | AI/OCR/视觉识别单次请求超时秒数 | `45` |
| `TG_CONNECT_TIMEOUT` | Telegram 连接/认证阶段超时秒数 | `20` |
| `SIGN_TASK_ACCOUNT_LOCK_TIMEOUT` | 签到任务等待同账号执行锁的最长秒数 | `300` |
| `SIGN_TASK_GLOBAL_CONCURRENCY_TIMEOUT` | 签到任务等待全局并发槽的最长秒数 | `300` |
| `TG_CONNECT_RETRIES` | Telegram 连接/认证阶段超时或网络错误重试次数 | `3` |
| `TG_CONNECT_RETRY_WAIT` | Telegram 连接/认证阶段重试等待秒数 | `3` |
| `TG_TCP_TIMEOUT` | Telegram 底层 TCP 连接超时秒数 | `8` |
| `TG_SLEEP_THRESHOLD` | Telegram FloodWait 自动等待阈值秒数 | `120` |
| `TG_WORKERS` | Telegram updates handler worker 数量 | `16` |
| `SIGN_TASK_RUN_TIMEOUT` | 单次签到任务总超时秒数；事件引擎任务会自动按 chat `event_timeout`、chat 数量和间隔抬高到足够覆盖内部事件等待 | `180` |
| `SIGN_TASK_RUN_TIMEOUT_OVERHEAD` | 事件引擎外层任务超时额外余量秒数，用于覆盖登录、预热、清理和少量调度开销 | `90` |
| `TG_EVENT_ENGINE_ACTION_TIMEOUT` | 事件引擎单个响应动作超时秒数；用于限制一次按钮回调、下载/OCR、验证码回复等交互卡住的时间 | `45` |
| `TG_EVENT_ENGINE_AI_FALLBACK` | 未配置的后续交互是否启用 AI 兜底；默认关闭，可在单个 chat 上用 `event_ai_fallback: true` 开启 | `0` |
| `TG_SIGN_TASK_DISABLE_UPDATES` | 强制关闭后端签到任务的 Telegram 实时 updates（仅低内存排障时使用） | `false` |
| `MALLOC_ARENA_MAX` | glibc malloc arena 数量（降低可减少内存碎片） | `2` |

### 动作类型说明

| 动作代码 | 说明 | 参数 |
|---------|------|------|
| `1` | 发送文本 | `text`: 要发送的文本 |
| `2` | 发送骰子 | `dice`: 骰子表情（🎲/🎯/🏀/⚽/🎳/🎰） |
| `3` | 点击键盘按钮 | `text`: 按钮文本 |
| `4` | AI 图片识别选择 | 无 |
| `5` | AI 回复计算题 | 无 |
| `6` | AI 图片文字识别并回复 | 可选 `caption_pattern`: 图片 caption 正则；可选 `captcha_lengths`: 验证码长度列表；可选 `captcha_charset`: 允许字符；可选 `captcha_case`: `preserve`/`upper`/`lower`；可选 `reply_to_message`: 是否回复到验证码图片消息 |
| `7` | AI 计算题点击按钮 | 无 |
| `8` | AI 诗词填空点击按钮 | 无 |
| `9` | 判断签到结果 | `keywords`: 结果关键词列表；命中任意关键词即视为完成 |

### 签到执行引擎

签到任务统一使用消息事件驱动引擎：收到机器人消息后即时处理按钮、验证码图片、计算题，并只根据用户在 `action: 9` 中配置的 `keywords` 判断任务结果。旧导入配置中即使包含 `engine: legacy` 也会被归一化为 `event`，不再提供按动作列表逐步等待和执行的兼容流水线。

事件引擎只有在动作列表包含 `action: 9`（成功关键字判断）时才会等待最终结果；如果任务只是发送消息、投骰子或点击按钮，不需要确认机器人返回结果，可以省略 `action: 9`，动作完成后会直接视为成功。

示例：

```json
{
  "_version": 3,
  "engine": "event",
  "sign_at": "0 6 * * *",
  "retry_count": 3,
  "chats": [
    {
      "chat_id": 10001,
      "name": "sample_captcha_bot",
      "event_timeout": 120,
      "event_retries": 3,
      "event_retry_wait": 2,
      "event_history_limit": 3,
      "event_action_timeout": 45,
      "event_ai_fallback": false,
      "actions": [
        { "action": 1, "text": "/start" },
        { "action": 3, "text": "签到" },
        {
          "action": 6,
          "caption_pattern": "请输入验证码",
          "captcha_lengths": [4],
          "captcha_charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
          "captcha_case": "upper",
          "reply_to_message": true
        },
        {
          "action": 9,
          "keywords": ["签到成功", "签到过了"]
        }
      ]
    }
  ]
}
```

事件引擎相关环境变量：

也可以在单个 chat 上配置 `event_timeout`、`event_retries`、`event_retry_wait`、`event_history_limit`、`event_action_timeout`、`event_ai_fallback` 覆盖下列环境变量，适合响应较慢、需要单独加长等待或开启历史救援的任务。

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `TG_EVENT_ENGINE_TIMEOUT` | 单个 chat 的事件驱动总等待秒数 | `120` |
| `TG_EVENT_ENGINE_INLINE_RETRIES` | 事件流内部遇到验证码/网络错误时的重试次数 | `3` |
| `TG_EVENT_ENGINE_RETRY_WAIT` | 事件流内部重试入口命令前等待秒数 | `2` |
| `TG_EVENT_ENGINE_ACTION_TIMEOUT` | 单个响应动作超时秒数；超时会记录 `event_engine_response_action_timeout` 并触发事件流内部重试 | `45` |
| `TG_EVENT_ENGINE_HISTORY_LIMIT` | 扫描最近历史消息条数；默认扫描最近 3 条，会在启动入口命令前和运行等待期间低频补漏，适合救援漏掉的验证码/结果消息；设为 `0` 可关闭 | `3` |
| `TG_EVENT_ENGINE_HISTORY_RESCUE_INTERVAL` | 运行等待期间历史补漏扫描间隔秒数，仅在 `TG_EVENT_ENGINE_HISTORY_LIMIT` 或 chat `event_history_limit` 大于 0 时生效 | `5` |
| `TG_EVENT_ENGINE_HISTORY_RESULT_MAX_AGE` | 启动前历史扫描允许消费的消息最大年龄秒数，避免把很久以前的结果/验证码消息当作本次结果；设为 `0` 表示不限制 | `600` |
| `TG_EVENT_ENGINE_AI_FALLBACK` | 未配置的后续交互是否启用 AI 兜底；默认关闭。建议只对会临时弹出额外验证按钮、且动作列表无法稳定覆盖的任务开启 | `0` |

启动前历史扫描只消费明确结果消息和可回复的图片验证码；旧菜单按钮、图片选项、计算题点击和文本计算题不会推进当前流程，避免旧 callback 或旧题目跳过 fresh 入口命令。运行期间的历史补漏仍会处理本次入口之后的新消息和已跟踪消息编辑。

事件引擎诊断报告：

任务历史记录会保存结构化 `flow_items`，并自动生成 `diagnostics` 诊断摘要。后台历史弹窗会展示“诊断通过 / 需观察 / 诊断失败”和每个检查项。CLI canary 与 API 报告会基于当前账号真实任务配置和最近运行历史汇总，不绑定任何特定 bot，也不是独立总览模块。做回归时重点看：

- `事件引擎启动`、预期按钮点击、验证码识别/回复、结果关键词命中是否通过。
- `可信按钮超时后继续推进` 是否通过，用于验证“签到”“我不是机器人”这类 callback timeout 不会卡死。
- `消息驱动动作推进` 出现时，表示某条机器人消息确实触发了响应动作从第 N 步推进到第 N+1 步，便于确认流程不是按脚本盲目前进。
- `图片选项题严格回调` 是否通过，用于确认 `action=4` 不会因默认推进误判。
- `结果命中后不再 OCR` 是否通过，用于确认图片 caption 命中结果关键词后不会再次识别验证码。
- `历史已处理消息编辑复查` 出现时，表示运行期间漏掉了实时 edited update，但事件引擎通过历史补漏复查启动历史或实时流程中已处理过的消息编辑版本救回了结果。
- `无回调按钮跳过` 出现时，表示匹配到同名按钮但它不是可回调按钮，事件引擎已跳过以免点错 URL/菜单按钮。
- `按钮回调未确认` 出现时，表示 Telegram 没确认本次 callback；事件引擎会保留重试空间，不把这次点击当作已经可靠完成。
- `事件内部重试耗尽` 出现时，表示事件引擎内部验证码/网络重试预算已经用完，需要结合前序 retry 原因和机器人返回继续排查。
- 失败任务如果出现 `事件引擎总超时`、`事件响应动作超时` 或 `历史补漏失败隔离`，说明还需要结合网络/RPC 日志继续观察。

也可以用 CLI 汇总当前任务的最新事件引擎诊断：

```bash
tg-signer --account jiegto canary-report
tg-signer --account jiegto canary-report --json-output
tg-signer --account jiegto canary-report --max-age-hours 36
tg-signer --account jiegto canary-report --strict
tg-signer --database-url sqlite:////data/db.sqlite --account jiegto canary-report --max-age-hours 36
tg-signer --data-dir /data --account jiegto canary-report --max-age-hours 36
```

报告为 `pass` 表示当前配置和最新历史证据足以证明任务的事件引擎关键路径通过。缺配置、缺关键动作、无历史、失败、需观察或最新证据过期都会让整体结果不是 `pass`。默认只接受 36 小时内的最新历史，避免旧成功记录误判当前已经稳定。
使用 `--strict` 时，整体状态不是 `pass` 会返回非零退出码，适合部署后或 CI 中做硬性验收。

### Cron 表达式

支持标准 Cron 表达式或简化时间格式：

```bash
# 每天 6:00 执行
0 6 * * *

# 每天 8:30 执行
30 8 * * *

# 每周一 9:00 执行
0 9 * * 1

# 简化格式（自动转换为 Cron）
06:00:00
```

---

## 🔧 高级功能

### 消息监控与转发

```bash
# 配置监控任务
tg-signer monitor my_account my_monitor

# 配置示例：监控群组消息并转发
{
  "match_cfgs": [
    {
      "chat_id": -1001234567890,
      "rule": "contains",
      "rule_value": "关键词",
      "forward_to_chat_id": 123456789,
      "push_via_server_chan": true
    }
  ]
}
```

### AI 自动回复

```bash
# 配置 AI 回复
{
  "chat_id": -1001234567890,
  "rule": "all",
  "ai_reply": true,
  "ai_prompt": "你是一个友好的助手，请简洁回复用户的问题。"
}
```

### 外部系统集成

支持通过 UDP 或 HTTP 转发消息到外部系统：

```json
{
  "external_forwards": [
    {
      "host": "127.0.0.1",
      "port": 9999
    },
    {
      "url": "http://example.com/webhook"
    }
  ]
}
```

---

## 🏗️ 技术架构

### 后端技术栈

- **框架**: FastAPI + Uvicorn
- **数据库**: SQLAlchemy（支持 SQLite/PostgreSQL）
- **任务调度**: APScheduler
- **Telegram 客户端**: Pyrogram (kurigram fork)
- **认证**: JWT + python-jose + pyotp (2FA)
- **限流**: slowapi

### 前端技术栈

- **框架**: Next.js 14 (App Router)
- **UI 库**: React 18 + Tailwind CSS
- **图标**: Phosphor Icons + Lucide React
- **类型**: TypeScript

### 项目结构

```
tg-signer/
├── backend/              # FastAPI 后端
│   ├── api/             # REST API 路由
│   ├── core/            # 核心功能（认证、数据库、配置）
│   ├── models/          # SQLAlchemy 模型
│   ├── services/        # 业务逻辑层
│   ├── repositories/    # 数据访问层
│   ├── scheduler/       # 任务调度
│   └── main.py          # 应用入口
├── frontend/            # Next.js 前端
│   ├── app/            # 页面路由
│   ├── components/     # React 组件
│   ├── lib/            # 工具函数
│   └── context/        # React Context
├── tg_signer/          # Telegram 自动化核心
│   ├── core.py         # 核心逻辑
│   ├── config.py       # 配置模型
│   ├── ai_actions.py   # AI 功能
│   └── __main__.py     # CLI 入口
├── docker/             # Docker 配置
├── Dockerfile          # 镜像构建
└── pyproject.toml      # Python 项目配置
```

---

## 🛡️ 安全建议

1. **生产环境必须设置强密钥**
   ```bash
   # 生成随机密钥
   openssl rand -hex 32
   ```

2. **启用双因素认证（2FA）**
   - 在 Web 界面的「设置」中启用 TOTP
   - 使用 Google Authenticator 等应用扫描二维码

3. **使用 HTTPS**
   - 生产环境建议使用 Nginx 反向代理并配置 SSL 证书

4. **限制访问**
   - 使用防火墙限制端口访问
   - 配置 CORS 白名单

5. **定期备份**
   ```bash
   # 备份数据目录
   tar -czf backup-$(date +%Y%m%d).tar.gz ./data
   ```

---

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

### 开发流程

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 提交 Pull Request

### 代码规范

- Python: 使用 `ruff` 进行代码检查
- TypeScript: 使用 `eslint` 进行代码检查

```bash
# 运行代码检查
ruff check .

# 自动修复
ruff check --fix .
```

---

## 📝 许可证

本项目采用 [BSD-3-Clause License](LICENSE) 开源协议。

---

## 🙏 致谢

- [tg-signer](https://github.com/amchii/tg-signer) by [amchii](https://github.com/amchii) - 本项目基于此项目进行重构与扩展
- [TG-SignPulse](https://github.com/akasls/TG-SignPulse) by [akasls](https://github.com/akasls) - 本项目基于此项目进行重构与扩展
- [Pyrogram](https://github.com/pyrogram/pyrogram) - Telegram MTProto API 客户端
- [FastAPI](https://fastapi.tiangolo.com/) - 现代化的 Python Web 框架
- [Next.js](https://nextjs.org/) - React 应用框架

---

<div align="center">

**如果这个项目对你有帮助，请给个 ⭐️ Star 支持一下！**

</div>
