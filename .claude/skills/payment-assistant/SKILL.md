---
name: payment-assistant
description: 解决手游游戏外充值问题。支持国内、海外游戏外充值问题处理。当用户需要解决手游游戏外充值相关问题时激活。
---

# Payment Assistant Skill

解决用户手游游戏外充值问题，输出 JSON 和 Markdown 格式。

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

### 依赖列表

| 包名 | 用途 |
|------|------|
| pydantic | 数据模型验证 |
| requests | HTTP 请求 |
| curl_cffi | 浏览器模拟抓取 |
| tenacity | 重试机制 |
| parsel | HTML/XPath 解析 |
| demjson3 | 非标准 JSON 解析 |

## 使用方式

### 基本用法

```bash
# 查询订单是否发货，输出 JSON
uv run .claude/skills/payment-assistant/scripts/query_order_status.py "订单号"

# 查下订单是否充值到xx游戏的，再给下账号，输出 JSON
uv run .claude/skills/payment-assistant/scripts/query_order_game_and_account.py "订单号"

# 指定输出目录
uv run .claude/skills/payment-assistant/scripts/query_order_status.py "订单号" --output ./output

# 指定输出 JSON
uv run .claude/skills/payment-assistant/scripts/query_order_status.py "订单号" --format json
```

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
