---
name: integration-support-assistant
description: 解决业务集成报错问题。当用户需要解决业务集成报错相关问题时激活。
---

# Integration Support Assistant Skill

解决用户业务集成报错问题，输出 JSON 和 Markdown 格式。

## 支持问题类型

| 问题类型 | ID | 问题 示例 |
|------|-----|----------|
| 商户授权问题 | merchant_authorization | `{"return_code":-10250017,"return_message":"Server reject, no authority","data":{}}，这个是没授权成功么？12.345.67.89  这个ip，授权过了。` |
| 签名错误问题 | signature_error | `{"resultCode": "unauthorized-request","resultMsg": "签名校验失败"}，调用xx接口时或者接入xx业务签名校验失败，帮忙为什么签名错误呢？` |

## 依赖安装

本 skill 使用 uv 管理依赖。首次使用前需要安装： 

```bash
# 项目级使用（推荐）
cd .claude/skills/integration-support-assistant
uv sync

# 或用户级使用
cd ~/.claude/skills/integration-support-assistant
uv sync
```

> **说明**: 此 skill 可放置在项目级 (`.claude/skills/`) 或用户级 (`~/.claude/skills/`) 目录。项目级便于团队共享，用户级便于跨项目复用。

**重要**: 所有脚本必须使用 `uv run` 执行，不要直接用 `python` 运行。`uv run` 会自动使用项目虚拟环境中的依赖。

### 依赖列表

| 包名 | 用途 |
|------|------|
| pydantic | 数据模型验证 |
| requests | HTTP 请求 |
| tenacity | 重试机制 |
| demjson3 | 非标准 JSON 解析 |

## 使用方式

### 基本用法

```bash
# 排查用户商户未授权原因，输出 JSON 和 Markdown
uv run .claude/skills/integration-support-assistant/scripts/merchant_authorization_handler.py --params "调接口时的参数信息" --return_code "错误码" --return_message "错误信息" --merchant_name "商户名称" --ip "ip地址" --interface "接口名称" --biz "所属业务"

# 排查签名错误原因，输出 JSON 和 Markdown
uv run .claude/skills/integration-support-assistant/scripts/signature_error_handler.py --params "调接口时的参数信息" --return_code "错误码" --return_message "错误信息" --interface "接口名称" --biz "所属业务"

# 指定输出目录
uv run .claude/skills/integration-support-assistant/scripts/merchant_authorization_handler.py --params "调接口时的参数信息" --return_code "错误码" --return_message "错误信息" --interface "接口名称" --biz "所属业务" --output ./output

# 指定输出 JSON
uv run .claude/skills/integration-support-assistant/scripts/merchant_authorization_handler.py --params "调接口时的参数信息" --return_code "错误码" --return_message "错误信息" --interface "接口名称" --biz "所属业务" --format json

# 指定输出 Markdown
uv run .claude/skills/integration-support-assistant/scripts/merchant_authorization_handler.py --params "调接口时的参数信息" --return_code "错误码" --return_message "错误信息" --interface "接口名称" --biz "所属业务" --format markdown
```

### 输出文件

脚本默认输出两种格式到指定目录（默认 当前Skill的`./output`目录）：
- 年月日时分秒`.json`，格式为 `20260512100101.json` - 结构化 JSON 数据
- 年月日时分秒`.md`，格式为 `20260512100101.md` - Markdown 格式文章

## 工作流程

1. **接收输入** - 用户提供问题内容
2. **分类检测** - 自动识别问题类型
3. **提取参数** - 自动从问题内容中分析出调接口时的参数信息、错误码、错误信息、接口名称、所属业务，问题中能够分析出这些相关参数则必须提取，没有则默认值为空字符串
4. **问题处理** - 调用对应函数处理问题
5. **格式转换** - 生成 JSON 和 Markdown
6. **输出文件** - 保存到指定目录

## 输出格式

### JSON 结构

```json
{
  "hand_code": "处理结果错误码",
  "hand_message": "处理结果信息",
  "hand_time": "处理时间",
  "hand_trace_id": "处理请求唯一标识"
}
```

### Markdown 结构

```markdown
## 用户问题信息
**问题类型**: xxx
**所属业务**: xxx, 能从用户问题中分析出的所属业务则写入该行, 否则不写入该行
**接口名称**: xxx, 能从用户问题中分析出的接口名称则写入该行, 否则不写入该行
**商户名称**: xxx, 能从用户问题中分析出的商户名称则写入该行, 否则不写入该行
**调接口时的参数信息**: xxx, 能从用户问题中分析出的调接口时的参数信息则写入该行, 否则不写入该行
**ip地址**: xxx, 能从用户问题中分析出的ip地址则写入该行, 否则不写入该行
**错误码**: xxx, 能从用户问题中分析出的错误码则写入该行, 否则不写入该行
**错误信息**: xxx, 能从用户问题中分析出的错误信息则写入该行, 否则不写入该行

---

## 处理结果

段落内容...
**处理结果错误码**: xxx
**处理结果信息**: xxx
**处理时间**: xxx
**处理请求唯一标识**: xxx

---
```

## 使用示例

### 商户授权问题

原始问题示例：`调用你提供的查询订单接口时报了这个错误：{"return_code":-10250017,"return_message":"Server reject, no authority","data":{}}，这个是没授权成功么？商户是：MEIYU_79100008，12.345.67.89这个ip，授权过了。`

```bash
uv run .claude/skills/integration-support-assistant/scripts/merchant_authorization_handler.py --params "" --return_code "-10250017" --return_message "Server reject, no authority" --merchant_name "MEIYU_79100008" --ip "12.345.67.89" --interface "查询订单接口" --biz ""
```

输出示例:
```
[INFO] Extracting content...
[INFO] 处理结果错误码: xxx
[INFO] 处理结果信息: 由于未提供你的商户名，无法精准定位到你的问题，我这边确实检索到了未授权的报错日志，但是无法确认这个报错日志是否是你触发的，你可以提供一下关键信息，比如：商户名，调用接口时的传参，具体的错误响应等，我可以更精准的帮你定位问题。
[INFO] 处理时间: xxx
[INFO] 处理请求唯一标识: xxx
[SUCCESS] Saved: ./output/20260512100101.json
[SUCCESS] Saved: ./output/20260512100101.md
```

### 签名错误问题

原始问题示例：`接入爆文猫登录认证服务，调用验证票据接口报了这个错误：{"resultCode": "unauthorized-request","resultMsg": "签名校验失败"}，帮忙看下为什么签名错误呢？ticket：ABC1234567`

```bash
uv run .claude/skills/integration-support-assistant/scripts/signature_error_handler.py --params "ABC1234567" --return_code "unauthorized-request" --return_message "签名校验失败" --interface "验证票据接口" --biz "爆文猫登录认证服务"
```

## 错误处理

| 错误类型 | 说明 | 解决方案 |
|----------|------|----------|
| `无法识别该问题类型` | 问题内容不匹配任何支持的问题类型 | 提示用户如何提问，比如：xxx业务，xxx接口，调用时的传参，具体的错误响应等关键信息提供给我，我才能更好的解决你的问题。并告知用户你具备哪些能力 |
| `问题处理异常` | 问题处理过程中出现异常 | 根据异常信息给用户一个友好的提示，比如：你如果能告知我你的调用接口的关键参数，我才能更好的解决你的问题。 |

## 注意事项

- 不要进行超过3次的函数调用重试，以避免对业务造成压力
- 一定不能将需要写入文件的JSON结构数据和Markdown结构数据响应给用户
- 不需要告诉用户需要自查那些点
- 一定不能让用户提供文档中未提及的参数信息
- 回复中一定不能带有猜测性的内容，也一定不能用猜测性的语言描述回答问题（比如：像是xxx, 可能是xxx），能解决问题就回复解决问题的内容，不能解决就回复当前提供的信息无法精确定位到问题，让用户补充必须业务参数