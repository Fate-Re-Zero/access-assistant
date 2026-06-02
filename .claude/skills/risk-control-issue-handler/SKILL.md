---
name: risk-assistant
description: 解决风控问题。当用户需要解决风控相关问题时激活。
---

# Risk Assistant Skill

解决用户风控问题，输出 JSON 和 Markdown 格式。

## 支持问题类型

| 问题类型 | ID | 问题 示例 |
|------|-----|----------|
| 商品限购 | product_limitation | `这个用户反馈xx商品单日买了两个，超出了我们单日限购1个的限制，帮忙查查是什么问题呢？` |
| 未成年限额 | minor_age_limit | `这个账号是未成年，限购没限住，帮忙查查是什么问题呢？` |

## 依赖安装

本 skill 使用 uv 管理依赖。首次使用前需要安装：

```bash
# 项目级使用（推荐）
cd .claude/skills/risk-assistant
uv sync

# 或用户级使用
cd ~/.claude/skills/risk-assistant
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