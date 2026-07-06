---
name: payment-assistant
description: 解决手游订单发货问题。当用户需要解决手游订单发货问题时激活。
mcp_servers:
  - payment-mcp-server
---

# Payment Assistant Skill

解决用户手游订单发货问题，输出 Markdown 格式。

## 支持问题类型

| 问题类型 | ID | 问题 示例 |
|------|-----|----------|
| 查询订单是否发货 | query_order_status | `这笔订单是否发货呢？ 查询这笔订单是否到账呢？` |
| 查下订单是否充值到xx游戏的，再给下账号 | query_order_game_and_account | `查下这笔订单是否充值到龙之谷世界的，再给下账号` |

## 依赖安装

本 skill 使用 uv 管理依赖。首次使用前需要安装：

```bash
# 项目级使用（推荐）
cd .claude/skills/payment-assistant
uv sync

# 或用户级使用
cd ~/.claude/skills/payment-assistant
uv sync
```

> **说明**: 此 skill 可放置在项目级 (`.claude/skills/`) 或用户级 (`~/.claude/skills/`) 目录。项目级便于团队共享，用户级便于跨项目复用。

**重要**: 所有脚本必须使用 `uv run` 执行，不要直接用 `python` 运行。`uv run` 会自动使用项目虚拟环境中的依赖。

## 使用方式

### MCP 工具（payment-mcp-server）

本 Skill 已绑定 MCP 服务 `payment-mcp-server`，用于图像/文本联合分析支付订单情况的场景能力。

| MCP 服务 | 工具名 | 适用场景 |
|---------|--------|---------|
| payment-mcp-server | `payment-mcp-server_analysisImageContentAndText` | 用户提供订单截图、图文混合描述，需分析订单信息并给出补单建议或处理提示 |

**MCP 工具参数（直接按此传参，禁止在代码库中搜索参数定义）：**

| 工具名 | 必填参数 | 参数说明 |
|--------|---------|---------|
| `payment-mcp-server_analysisImageContentAndText` | `request` | 外层对象，包含以下两个字段 |
| ↳ `request.addTextDescription` | 建议填写 | 用户问题中的**文字补充描述**：订单号、游戏名称、是否发货/到账诉求、报错信息等 |
| ↳ `request.imageDescription` | 建议填写 | **订单截图/图片内容的文字描述**：从截图中识别出的订单号、支付状态、金额、时间、报错提示等；无截图时可留空或填「无截图」 |

**参数 JSON 结构示例：**

```json
{
  "request": {
    "addTextDescription": "查询订单791000803PP022260501164943000001是否发货",
    "imageDescription": "截图显示订单号791000803PP022260501164943000001，支付状态已支付，页面提示待发货"
  }
}
```

**使用原则：**

1. 先 `load_skill("payment-assistant")` 加载本 Skill 指令
2. **参数提取规则**（从用户问题中提取，组成上述 `request` JSON）：
   - **订单号** → 填入 `addTextDescription`：匹配长串字母数字（如 `791000803PP022260501164943000001`），或「订单号/商户单号/支付单号」后的值
   - **游戏名称** → 填入 `addTextDescription`：从「充值到 XX 游戏」「XX 游戏的订单」等表述中提取游戏名（如 `龙之谷世界`）
   - **用户诉求** → 填入 `addTextDescription`：是否发货、是否到账、是否充值到某游戏、要账号等原话摘要
   - **截图/图片信息** → 填入 `imageDescription`：若用户提供了订单截图或描述了截图内容，将可见的订单号、支付状态、报错信息、金额、时间等整理为文字描述；纯文字问题无截图时，`imageDescription` 可省略或设为 `"无截图"`
3. 当问题涉及**订单截图分析**或**图文混合的订单履约问题**时，调用 `payment-mcp-server_analysisImageContentAndText`，传入提取后的 `request` 参数
4. 当问题仅为**纯文字订单号查询**（无截图）时，优先使用下方脚本 `query_order_status.py` / `query_order_game_and_account.py`；若 MCP 可用且需交叉验证，可同时传入 `addTextDescription`（含订单号与诉求），`imageDescription` 设为 `"无截图"`
5. 若 MCP 返回结果与脚本查询结果冲突，以 MCP 分析 + 脚本查询交叉验证后给出结论
6. MCP 调用失败时，回退到下方脚本方式处理
7. **禁止**使用 grep、read_file、list_dir 等工具在代码库中查找 MCP 参数或工具定义；参数以上表为准

**MCP 使用案例：**

#### 案例 1：图文混合 — 查询订单是否发货（query_order_status + MCP）

用户问题：

```
【附订单截图】查询一下791000803PP022260501164943000001这笔订单是否发货呢
```

Agent 处理步骤：

1. 提取订单号 `791000803PP022260501164943000001`、诉求「是否发货」、截图中的支付/发货状态描述
2. 调用 `payment-mcp-server_analysisImageContentAndText`，参数示例：
   ```json
   {
     "request": {
       "addTextDescription": "查询订单791000803PP022260501164943000001是否发货",
       "imageDescription": "截图显示订单号791000803PP022260501164943000001，支付已完成，发货状态待确认"
     }
   }
   ```
3. 可选：再用 `query_order_status.py` 脚本交叉验证
4. 整合 MCP 与脚本结论回复用户

#### 案例 2：纯文字 — 查订单是否充值到某游戏并给账号（query_order_game_and_account）

用户问题：

```
龙之谷世界，791000803PP022260501164943000001查下这笔订单是否充值到账
```

Agent 处理步骤：

1. 提取订单号 `791000803PP022260501164943000001`、游戏名 `龙之谷世界`
2. 调用`payment-mcp-server_analysisImageContentAndText`，参数示例：
   ```json
   {
     "request": {
       "addTextDescription": "戏名 `龙之谷世界`，订单号 `791000803PP022260501164943000001`查下这笔订单是否发货",
       "imageDescription": ""
     }
   }
   ```
4. MCP 返回结果作为最终结论
5. MCP 调用失败时，返回'排查当前业务问题的工具存在异常'

### 输出文件

脚本默认输出两种格式到指定目录（默认 `./output`）：
- `{order_id}.json` - 结构化 JSON 数据
- `{order_id}.md` - Markdown 格式文章

## 工作流程

1. **接收输入** - 用户提供问题内容
2. **分类检测** - 自动识别问题类型
3. **提取订单号** - 自动从问题内容中提取订单号
3. **问题处理** - 调用对应函数处理问题
4. **格式转换** - 生成 JSON 和 Markdown
5. **输出文件** - 保存到指定目录

## 输出格式

### JSON 结构

```json
{
  "order_id": "订单id",
  "app_id": "游戏id",
  "game_name": "游戏名称",
  "account_id": "账号id",
  "pay_time": "支付时间",
  "result_message": "查询结果",
  "uuid": "请求唯一标识"
}
```

### Markdown 结构

```markdown
## 订单信息
**订单号**: xxx
**游戏id**: 123456
**游戏名称**: 龙之谷世界
**支付时间**: 2024-01-01 12:00
**uuid**: 请求唯一标识

---

## 处理结果

段落内容...
**查询结果**: 查询结果
---
```

## 使用示例

### 查询订单是否发货

原始问题示例：查询一下791000803PP022260501164943000001这笔订单是否发货呢

```bash
uv run .claude/skills/payment-assistant/scripts/query_order_status.py "791000803PP022260501164943000001"
```

输出:
```
[INFO] Extracting content...
[INFO] Order Id: 791000803PP022260501164943000001
[INFO] Pay Time: 2024-01-01 12:00
[INFO] Result Message: 当前订单丢单，已进行补发
[SUCCESS] Saved: ./output/ebMzDPu2zMT_mRgYgtL6eQ.json
[SUCCESS] Saved: ./output/ebMzDPT/ebMzDPu2zMT_mRgYgtL6eQ.md
```

### 查下订单是否充值到xx游戏的，再给下账号

原始问题示例：791000803PP022260501164943000001查下这笔订单是否充值到龙之谷世界的，再给下账号

```bash
uv run .claude/skills/payment-assistant/scripts/query_order_game_and_account.py "791000803PP022260501164943000001"
```

## 错误处理

| 错误类型 | 说明 | 解决方案 |
|----------|------|----------|
| `无法识别该问题类型` | 问题内容不匹配任何支持的问题类型 | 检查问题内容 是否正确 |
| `问题类型不支持` | 非支持的问题类型 | 本 Skill 仅支持列出的问题类型 |
| `问题处理失败` | 网络错误或处理逻辑错误 | 重试或检查 问题内容 有效性 |

## 注意事项

- 不要进行超过3次的函数调用重试，以避免对业务造成压力

## 参考

- [问题类型匹配模式说明](references/payment-problem-patterns.md)
