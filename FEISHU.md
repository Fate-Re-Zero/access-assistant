# 飞书私聊 Bot 接入指南（阶段 0–4）

当前实现：**私聊（p2p）+ 群聊 @ 机器人**，Webhook 收消息 → `SupervisorSkillsAgent.invoke()` → 回复用户。

**阶段 2 已支持：** 事件幂等、白名单、`/new` 新对话、`/help`、处理中提示。

**阶段 3 已支持：** lark_md 格式化、交互卡片回复、Agent 执行进度推送、长文分段。

**阶段 4 已支持：** 会话版本持久化、Agent 多轮记忆 SQLite、审计日志、飞书 SSO 身份校验。

**阶段 5A 已支持：** 入站 `.txt` / `.md` / `.markdown` 文件，下载后作为 Agent 输入。

## 阶段 0：飞书开放平台配置

### 1. 创建企业自建应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)
2. 创建 **企业自建应用**
3. 记录 **App ID**、**App Secret**

### 2. 启用机器人能力

在应用后台 → **添加应用能力** → 启用 **机器人**

### 3. 申请权限

至少开通：

| 权限 | 用途 |
|------|------|
| `im:message.p2p_msg:readonly` | 读取用户发给机器人的**私聊**消息（必开） |
| `im:message.group_at_msg:readonly` | 读取群聊中 **@ 机器人** 的消息（群聊必开） |
| `im:message.group_msg` | 读取群内**全部消息**（群文件免 @ 时必开，**敏感权限**，需管理员审核） |
| `im:message:send_as_bot` | 机器人回复消息 |
| `im:resource` | 下载用户发送的文件（阶段 5A 必开） |

保存后如提示需管理员审核，请企业管理员通过。

### 4. 事件订阅

路径：**事件订阅** → **请求地址**：

```text
https://你的域名/feishu/webhook
```

订阅事件：

- `im.message.receive_v1`（接收消息 v2.0）

**Encrypt Key（加密）**：阶段 1 请先 **关闭** 或留空。若开启加密，当前版本会返回 400。

**Verification Token**：自定义一串随机字符串，填入 `.env` 的 `FEISHU_VERIFICATION_TOKEN`。

保存后飞书会发起 URL 校验（challenge），服务正常时会自动通过。

### 5. 发布应用

- 创建版本并提交发布（内测可用「仅自用」或指定可用范围）
- 在飞书客户端搜索你的机器人，**私聊**发消息测试
- 将机器人**添加到群聊**，在群内 **@ 机器人** 发文字测试
- 群内发送 `.txt` / `.md` 文件可**不 @**（需开通「获取群组中所有消息」权限，见下方说明）

### 6. Nginx 反代示例

```nginx
location /feishu/ {
    proxy_pass http://127.0.0.1:7777;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 30s;
}
```

说明：Webhook 本身很快返回；Agent 推理在后台异步执行，不占这条连接。

## 阶段 1：access-assistant 环境变量

在 `access-assistant/.env` 增加：

```env
FEISHU_ENABLED=true
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
FEISHU_VERIFICATION_TOKEN=your-random-token
# FEISHU_ENCRYPT_KEY=          # 阶段 1 留空，并在飞书后台关闭加密
# FEISHU_API_BASE=https://open.feishu.cn
# FEISHU_GROUP_ENABLED=true
# FEISHU_BOT_OPEN_ID=ou_xxxxxxxx
# FEISHU_GROUP_REQUIRE_MENTION=true
```

## 阶段 2：生产增强（可选）

```env
# 长任务先回复「正在处理…」，最终结果另发一条消息
FEISHU_SHOW_PROCESSING=true
FEISHU_PROCESSING_TEXT=正在处理，请稍候…

# 内测白名单（留空=不限制）
# FEISHU_ALLOWED_CHAT_IDS=oc_xxx,oc_yyy
# FEISHU_ALLOWED_OPEN_IDS=ou_xxx,ou_yyy

# Auth 专用 webhook（POST /feishu/auth/webhook）群聊白名单；留空=不限制，配置后仅 listed 群可触发 Auth-Agent
# 见下方「Auth 专用机器人」独立配置 FEISHU_AUTH_* 变量

# 事件去重（默认 24h）
# FEISHU_DEDUPE_TTL_SECONDS=86400
# FEISHU_DEDUPE_MAX_SIZE=10000
```

### Auth 专用机器人（可选，第二个飞书应用）

主机器人走 `/feishu/webhook`（Supervisor）；Auth 专用机器人需**单独创建飞书应用**，事件订阅请求地址填：

```text
https://你的域名/feishu/auth/webhook
```

在 `.env` 配置独立凭证（与 `FEISHU_*` 主机器人分离）：

```env
FEISHU_AUTH_BOT_ENABLED=true
FEISHU_AUTH_APP_ID=cli_auth_xxxxxxxx
FEISHU_AUTH_APP_SECRET=xxxxxxxxxxxxxxxx
FEISHU_AUTH_VERIFICATION_TOKEN=your-auth-random-token
# FEISHU_AUTH_BOT_OPEN_ID=ou_xxxxxxxx
# FEISHU_AUTH_ALLOWED_CHAT_IDS=oc_auth_group_1,oc_auth_group_2
# FEISHU_AUTH_P2P_ENABLED=false          # 禁止 Auth 机器人私聊（仅 /feishu/auth/webhook）
# FEISHU_AUTH_DATA_DIR=./data/feishu-auth
```

说明：
- 未配置 `FEISHU_AUTH_APP_ID` 等时，`/feishu/auth/webhook` 返回 503
- Auth 机器人使用独立 `tenant_access_token` 回复，会话 thread_id 前缀为 `feishu-auth:`
- 群聊白名单 `FEISHU_AUTH_ALLOWED_CHAT_IDS` 仅作用于 Auth 机器人
- `FEISHU_AUTH_P2P_ENABLED=false` 可禁止 Auth 机器人私聊（默认 `true`；仅影响 `/feishu/auth/webhook`）
- `FEISHU_AUTH_SHOW_PROCESSING` / `FEISHU_AUTH_SHOW_PROGRESS_UPDATES` 可单独控制 Auth 入口的中间处理提示与进度推送（未设置则继承主机器人 `FEISHU_SHOW_*` 配置）

### 用户指令

| 指令 | 行为 |
|------|------|
| `/new` 或 `新对话` | 开启新会话（不带入旧上下文） |
| `/help` 或 `帮助` | 返回能力说明 |

## 阶段 3：体验增强（可选）

```env
# Markdown 转 lark_md，并用交互卡片发送最终答案
FEISHU_USE_LARK_MD=true
FEISHU_USE_INTERACTIVE_CARD=true

# 执行过程中推送进度（agent_call / tool_call 等），默认每 3 秒最多 1 条
FEISHU_SHOW_PROGRESS_UPDATES=true
FEISHU_PROGRESS_MIN_INTERVAL_SECONDS=3

# Supervisor 进度（与 ENABLE_THINKING 无关）
# SHOW_PLANNER_PROGRESS=true   → 飞书/Web 展示 [task plan] 规划说明
# SHOW_SUBAGENT_PROGRESS=true  → 展示 **Auth Agent** 处理中 / 工具调用进度

# 纯文本分段大小（关闭卡片时生效）
# FEISHU_TEXT_CHUNK_SIZE=3800
```

## 阶段 5A：入站 txt / md 文件（双向 pending）

用户可在私聊或群聊（@ 机器人）中发送文本文件；默认采用**双向 pending**，不会收到文件就立刻跑 Agent。

**流程 A：先发文件**

1. 用户发送 `.txt` / `.md`
2. Bot 回复：已收到文件，请说明想做什么
3. 用户发送文字问题
4. Bot 合并「文件内容 + 问题」后调用 Agent

**流程 B：先发文字**

1. 用户发送含「文件/文档」等意图的描述
2. Bot 回复：请发送文件
3. 用户发送文件
4. Bot 合并后调用 Agent

```env
# 默认开启双向 pending
# FEISHU_FILE_BIDIRECTIONAL=true

# pending 内存 TTL（秒，默认 600 = 10 分钟）
# FEISHU_FILE_PENDING_TTL_SECONDS=600

# pending 最大条目数（内存）
# FEISHU_FILE_PENDING_MAX_SIZE=5000

# 单文件大小上限（字节，默认 512000 ≈ 500KB）
# FEISHU_FILE_MAX_BYTES=512000

# 送入 Agent 的最大字符数（默认 80000，超出会截断）
# FEISHU_FILE_MAX_PROMPT_CHARS=80000

# 允许的扩展名
# FEISHU_FILE_ALLOWED_EXTENSIONS=.txt,.md,.markdown
```

用户指令：

| 指令 | 行为 |
|------|------|
| `取消` / `/cancel` | 放弃当前 pending 的文件或问题 |
| `/new` | 清 pending + 新会话 |

说明：

- pending 存内存，**服务重启会丢失**
- 设为 `FEISHU_FILE_BIDIRECTIONAL=false` 可恢复「收到文件立即处理」旧行为
- 出站仍以卡片 / lark_md 文本回复，不会发文件

### 群聊 @ 规则

默认：**群文件可不 @**；**普通文字仍须 @**；pending 配对期间相关文字可免 @。

```env
# FEISHU_GROUP_REQUIRE_MENTION=true          # 普通群文字是否必须 @
# FEISHU_GROUP_FILE_WITHOUT_MENTION=true     # 群文件是否免 @（默认 true）
```

须飞书开通 **获取群组中所有消息**（权限 key：`im:message.group_msg`，敏感权限），否则未 @ 消息不会推到 Webhook。

在开放平台 **权限管理** 中请按**中文名称**搜索，不要只搜 `:readonly` 后缀：

| 控制台显示名称 | 权限 key | 说明 |
|--------------|----------|------|
| **获取群组中所有消息** | `im:message.group_msg` | 常见、敏感，需管理员审核后生效 |
| 获取群聊中所有的用户聊天消息 | `im:message.group_msg:readonly` | 文档中也有，部分租户控制台不展示此条目 |

开通后须 **创建版本并重新发布** 应用。若企业禁止申请该敏感权限，则群内文件也必须 **@ 机器人** 才能收到。

## 阶段 4：持久化、审计、SSO（正式上线前）

```env
# 会话版本与审计日志落 SQLite（重启不丢 /new 会话号）
FEISHU_PERSISTENCE_ENABLED=true
FEISHU_DATA_DIR=./data/feishu
FEISHU_AUDIT_ENABLED=true
# FEISHU_AUDIT_MAX_CONTENT_LENGTH=2000

# 飞书 SSO：校验 open_id 对应企业用户（需开通通讯录权限）
# FEISHU_SSO_ENABLED=true
# FEISHU_SSO_ALLOWED_EMAIL_DOMAINS=sdo.com,corp.com
# FEISHU_SSO_CACHE_TTL_SECONDS=3600
```

SSO 需额外申请权限：

| 权限 | 用途 |
|------|------|
| `contact:user.base:readonly` | 读取用户姓名、邮箱、在职状态 |

重启服务：

```bash
cd access-assistant
./service.sh restart
# 或
uv run access-assistant-web
```

### 健康检查

```bash
curl http://127.0.0.1:7777/feishu/health
# {"enabled":true,"mode":"p2p-only","phase":4,...}
```

查询最近审计记录（内网调试）：

```bash
curl "http://127.0.0.1:7777/feishu/audit/recent?limit=20"
```

## 行为说明

| 项 | 行为 |
|----|------|
| 私聊（p2p） | 直接处理用户消息 |
| 群聊（group）文字 | 默认须 **@ 机器人**；pending 追问 / 文件意图 / 有 pending 时的取消可免 @ |
| 群聊（group）文件 | 默认**可不 @**（需 `im:message.group_msg` + `FEISHU_GROUP_FILE_WITHOUT_MENTION=true`） |
| 会话 ID | `feishu:{chat_id}:{open_id}`（群内按用户隔离上下文；`/new` 后带 `:s1`、`:s2`…） |
| 回复方式 | 先 reply「处理中」→ 进度推送（可选）→ 卡片/lark_md 发最终结果 |
| 进度推送 | `agent_call` / `tool_call` / `thinking` 等，按间隔节流 |
| 最终格式 | 默认交互卡片 + lark_md；可关闭卡片改纯文本 |
| 文件入站 | 双向 pending：文件↔文字配对后再调 Agent；支持 `取消` |
| 会话持久化 | SQLite 保存 `/new` 会话版本号（飞书 thread_id 隔离） |
| 审计日志 | 入站/出站消息写入 `FEISHU_DATA_DIR/feishu.sqlite`（内容脱敏） |
| SSO | 可选：校验飞书 open_id 对应在职员工及邮箱域 |
| 事件幂等 | 相同 `event_id` / `message_id` 只处理一次 |
| 白名单 | 配置 `FEISHU_ALLOWED_*` 后仅允许名单内用户 |

## 常见问题

**URL 校验失败**

- 检查 Nginx 是否把 `/feishu/webhook` 转到 access-assistant 端口
- 检查 `FEISHU_ENABLED=true` 且 App 凭证、Verification Token 已配置
- 查看服务日志是否有 `Feishu URL verification succeeded`

**群文件不 @ 无回复**

- 确认已开通 **获取群组中所有消息**（`im:message.group_msg`，敏感权限；仅 `group_at_msg` 收不到未 @ 消息）
- 确认 `FEISHU_GROUP_FILE_WITHOUT_MENTION=true`
- 查看日志：`Feishu group message ignored: mention required` 表示被 @ 规则拦截

**群聊 @ 无回复**

- 确认已开通 `im:message.group_at_msg:readonly` 且应用已发布
- 确认机器人已加入该群
- 消息必须 **@ 机器人**（`FEISHU_GROUP_REQUIRE_MENTION=true` 时）
- 查看日志：`Feishu group message ignored: bot not mentioned` 或 `Feishu group message received`

**私聊发消息无回复**

- 确认应用已发布且机器人可用
- 确认权限 `im:message` / `im:message:send_as_bot` 已生效
- 查看日志：`Feishu p2p message received` / `Feishu agent failed`

**收到加密事件错误**

- 飞书后台关闭 Encrypt Key，或阶段 2 再实现解密

## 本地调试（无公网域名）

使用内网穿透把本地 `7777` 暴露为 HTTPS 地址，将请求地址填为：

```text
https://<tunnel-host>/feishu/webhook
```
