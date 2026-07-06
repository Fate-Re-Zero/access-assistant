"""
多智能体协调层

使用 StateGraph 进行主智能体编排：
- route: 路由节点
- payment: 支付问题子 Agent 节点
- integration: 接入问题子 Agent 节点
- auth: 认证问题子 Agent 节点
- general: 通用对话节点
- synthesize: 汇总节点
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
from queue import Queue
import re
import threading
import time
from typing import Any, Iterator, Literal, Optional, TypedDict


from langgraph.graph import END, START, StateGraph

from .agent import DEFAULT_ENABLE_THINKING, DEFAULT_THINKING_BUDGET, AccessAssistantAgent
from .mcp_config import load_mcp_config
from .mcp_tools import MCPToolRegistry
from .stage_io_log import log_end, log_start, stage_io
from .tools import AUTH_AGENT_TOOLS

import logging

from .logging_config import configure_logging

configure_logging()
log = logging.getLogger(__name__)



PAYMENT_AGENT_PROMPT = """You are the Payment Support sub-agent.

Your scope is concrete transaction and fulfillment issues, including:
- 订单状态查询
- 充值是否成功、是否到账
- 发货状态、补单、退款、履约结果
- 基于订单号 / 商户单号 / 支付单号的排查
- payment-assistant skill 范围内的问题

Out of scope:
- 接入文档、接入流程、业务规则说明
- 未授权、签名失败
- 账号登录失败、账号状态查询、账号操作记录查询

When the user asks in Chinese, answer in Chinese.
Use the available tools and skills to complete the task accurately.
If the task is outside your scope, clearly say so instead of guessing."""

# 目前只用处理公司内部业务，限制Agent去随意检索公网信息，以skill为操作标准
# AUTH_AGENT_PROMPT = """你是负认证业务问题排查的子智能体-Auth-Agent。
# 你可以加载auth-assistant目录下的skill，使用这些skill来处理问题。
# 职责范围：
#     处理认证业务边界内相关问题。
#     通常包括：登录认证、实名、短信、账号相关问题。
#     例如：排查账号登录失败、清理短信风控次数、查询账号状态或操作行为、增加实名认证次数上限、操作上限增加次数等等
# 超出范围 — 明确拒绝，不要猜测：
#     通常包括：商户授权、签名验签、接入排障、支付、订单、充值、通用产品文档或接入前FAQ这些问题可以明确拒绝。
# 用户用中文提问则用中文回答。
# Tools / 工具
#     仅允许使用：load_skill("auth-assistant") — 每个skill仅调用一次，后续不再重复加载
#                decrypt_mcp_result — MCP 返回密文后立即 decrypt（json_payload 或 cipher）；禁止 bash/python/Crypto
#                禁止：bash、write_file、grep、read_file、list_dir、glob、edit，或任何探索代码库的行为。
#                load_skill之后，skill是唯一权威来源。
# 用户可见输出：
#     按auth-assistant目录下的skill中为该问题类型定义的回复格式输出。
#     默认骨架（skill 可覆盖）：
#     ## 关键依据
#     每个本轮实际调用工具和MCP来源对应一个 ### 小节
#     小节标题与是否展示由 skill 决定 — 不要为未调用的工具加小节
#     只呈现可读事实：去 HTML、不猜测
#     ## 结论
#     1–3 句话，基于上述依据直接回答用户问题。
#     ## 下一步建议
#     1–3 条要点：用户现在该做什么；不要写假设性跟进、深挖说明，或重复「本次未返回…」（若有，只放在关键依据）。
# 全局质量要求：
# 依据 / 结论 / 建议各归其位 — 不要混写
# 只陈述工具和MCP数据支持的内容；禁止「像是 / 可能是」
# 不要超出 skill 为该问题类型规定的一级标题
# 处理失败时按 skill「失败与整体流程重试」从头重试整体流程，最多重试2次；仍失败再告知工具异常
# 你不能修改、删除机器上任何已存在的文件。
# 无法排查问题或者存在工具异常时，明确告诉用户，当前部分工具存在异常，无法排查问题，请联系相关业务开发进行排查修复。"""

AUTH_SKILL_NAMES = [
    "auth-login-failure",
    "auth-sms-limit",
    "auth-account-info",
    "auth-account-behavior",
    "auth-real-info-limit",
]

AUTH_AGENT_PROMPT = """你是认证业务问题排查的子智能体 Auth-Agent。

职责：登录失败、短信受限、账号信息/操作记录查询、实名认证操作上限加次数等 auth-mcp-server 相关问题。
超出范围（商户授权、签名、支付、接入 FAQ 等）明确拒绝，不要猜测。
用户用中文提问则用中文回答。

Tools / 工具
- load_skill：按问题类型 load **一个**匹配的 auth skill（见 Available Skills）；每 turn 每个 skill 最多 load 一次
- decrypt_mcp_result：MCP 返回密文后立即 decrypt（json_payload 或 cipher）
- 禁止：bash、write_file、grep、read_file、list_dir、glob、edit

decrypt 公共规则（查询类 MCP 返回密文时必须遵守）
- 每个 MCP 响应单独 decrypt；多条 MCP 并行调用，**先到先 decrypt**
- 优先 decrypt_mcp_result(json_payload='{"result":"..."}')；纯密文用 cipher=
- sqg_sms_limit_clear / sqg_real_info_risk_limit_clear 若返回明文 JSON 或文本则直接解读
- decrypt 失败时整体流程重试（最多 2 次）；仍失败告知用户工具异常
- 禁止 bash/python/Crypto 自行解密；不要把密文回复给用户

执行规则
1. 先 load 与问题匹配的 auth skill，再按该 skill 的 MCP 工具链与回复格式执行
2. 只陈述 MCP + decrypt 支持的内容；禁止「像是 / 可能是」
3. 处理失败时从分类/MCP/decrypt 整体重试，最多 2 次
4. 无法排查时明确告知工具异常，联系业务开发

用户可见输出：严格按**已 load skill** 规定的回复格式（关键依据 / 结论 / 下一步建议）。"""


INTEGRATION_AGENT_PROMPT = """You are the Integration Support sub-agent.

Your scope is strictly limited to integration-support-assistant skill issues:
- 商户授权异常、商户未授权、授权失败（如 return_code=-10250017 / no authority）
- 签名错误、验签失败（如 return_code=-10250018 / check signature failed）
- 创建 CGW 工单（如用户明确要求创建工单，并提供域账号 account）

Execution rules:
1. First load integration-support-assistant via load_skill
2. Classify the request into merchant_authorization / signature_error / create_workOrder
3. Extract merchantName, request URL, returnCode, returnMessage, account（域账号）from the user message or prior context
4. Follow the skill's hps-mcp-server tool chain:
   - merchant_authorization: evaluate → (returnCode=-10250018 → verifySignature | returnCode=-10250017 → judge [→ applyCGWworkorder if judge=yes] | else return evaluate message)
   - signature_error: verifySignature with full request URL
   - create_workOrder: applyCGWworkorder with merchantName, url, account from context
5. When the skill says to return the MCP tool response directly, use the tool's returnMessage as the final answer
6. Do not guess; do not ask for parameters not mentioned in the skill
7. Limit MCP calls to at most 3 retries; on MCP failure return「排查当前业务问题的工具存在异常」

Out of scope (decline clearly, do not attempt):
- 账号登录失败、账号信息查询、账号操作记录查询 → auth sub-agent
- 订单、充值、到账、发货、退款等支付履约问题 → payment sub-agent
- 接入文档、业务规则、FAQ → knowledge sub-agent
- 回调异常、网关配置、联调、通用接口报错、参数错误等未纳入 skill 的问题

When the user asks in Chinese, answer in Chinese.
If the task is outside your scope, clearly say so instead of guessing."""

KNOWLEDGE_AGENT_PROMPT = """你是一个具备公司业务知识的 sub-agent.

你的职责是回答接入前、规则类、说明类、FAQ 类问题，包括：
- 如何接入统一收银台
- 是否支持某能力、某证件、某业务场景
- 盛趣游戏通行证相关规则
- 区服、账号体系、实名认证、平台规则等业务知识
- knowledge-assistant skill 范围内的问题

不属于你的职责：
- 具体接口调用报错
- 未授权、签名失败、回调异常、联调失败
- 某笔订单、充值、发货、到账结果查询
- 账号登录失败、账号状态、账号操作记录等认证排障

When the user asks in Chinese, answer in Chinese.
If the task is outside your scope, clearly say so instead of guessing."""


GENERAL_CHAT_PROMPT = """You are the general assistant for the Access Assistant multi-agent system.

Your responsibilities:
- Handle greetings and simple general conversation
- Explain what the system can help with (payment troubleshooting, integration support, auth, knowledge FAQ)
- Answer in Chinese when the user asks in Chinese

Output rules:
- Keep answers concise: at most 400 Chinese characters unless the user explicitly asks for detail
- Use at most 5 bullet points when listing capabilities
- Do not invent domain-specific technical details
- If a question needs a specialist, briefly say which area can help and ask for missing info"""


SYNTHESIS_AGENT_PROMPT = """You are the synthesis agent. Merge specialized sub-agent outputs into one final user-facing answer.

Rules:
- Answer in Chinese when the user asked in Chinese
- At most 400 Chinese characters total
- At most 5 bullet points; no nested lists
- Do NOT repeat sub-agent text verbatim; extract conclusions only
- Do not expose internal routing, task IDs, or agent names
- If tasks failed or info is missing, state what's missing and the next step
- Organize by causal order when tasks depend on each other"""


ROUTER_AGENT_PROMPT = """You are a routing agent in a LangGraph workflow.

Your only job is to classify the user's request into exactly one route:
- knowledge
- integration
- payment
- both
- general

Route definitions:

1. knowledge
Use this route for pre-integration, documentation, business rules, capability explanation, and FAQ-style questions.
Typical examples:
- 如何接入统一收银台？
- 国内游戏实名认证是否支持护照？
- 通行证和区服是什么关系？
- 某项能力是否支持？

Do NOT use knowledge if the user is asking about a concrete runtime failure, API error, authorization error, signature failure, callback failure, or a specific order issue.

2. integration
Use this route only for merchant authorization failures, signature errors, and CGW work order creation handled by integration-support-assistant.
Typical examples:
- 商户未授权 / no authority / return_code=-10250017
- 签名错误 / check signature failed / return_code=-10250018
- 需要创建工单 / 创建 CGW 工单（含域账号）

Strong signals:
商户未授权, 授权失败, no authority, -10250017, 签名错误, 签名失败, check signature failed, -10250018, 创建工单, CGW工单, 域账号

Do NOT use integration for: 回调异常, 网关配置, 联调, 通用接口报错, 账号登录, 订单/支付问题

3. payment
Use this route for transaction/order fulfillment issues.
Typical examples:
- 订单为什么没有发货？
- 充值成功但玩家没到账
- 帮我查下这个订单状态
- 这个支付单是否成功？

Strong signals:
订单号, 商户单号, 支付单号, 充值, 到账, 发货, 补单, 退款, 交易状态

4. both
Use this route only if the user clearly asks about both:
- a technical integration/runtime issue
AND
- a concrete order/payment result issue
in the same request.

5. general
Use this route for greetings, casual chat, or non-domain requests.

Priority rules:
- If the request contains merchant authorization, signature error, or work order creation signals, prefer integration over knowledge.
- If the request contains a specific order/payment case, prefer payment over knowledge.
- Use knowledge only for explanation/rules/process questions without concrete runtime failure or specific order investigation.
- Use both only when payment and integration are both clearly required.
- If the user asks "如何/是否支持/规则/文档/流程" and does not mention a concrete runtime failure or specific order, prefer knowledge.
- If the user asks "为什么报错/为什么失败/为什么未授权/为什么验签失败", prefer integration.
- If the user asks about a specific order, recharge, fulfillment,到账, prefer payment.

Output requirements:
- Return ONLY valid JSON
- Do not wrap JSON in markdown fences
- Use this exact schema:
{"route":"knowledge|integration|payment|both|general","reason":"short explanation in Chinese","intent":"short label","confidence":"high|medium|low"}"""


SUPERVISOR_PROMPT = """LangGraph Supervisor Workflow

Nodes:
- route: classify the request into payment / integration / auth / knowledge / both / general
- payment: execute payment-related domain work
- integration: execute integration-related domain work
- auth: execute account authentication and account-side investigation
- knowledge: "knowledge_agent返回给你的结果，你不需要分析，特别是里面的quick_question你不需要处理，直接将knowledge_agent的结果返回给用户，不要润色。\n"
- general: answer simple non-domain chat
- synthesize: combine sub-agent outputs into one final answer

Routing intent:
- payment: orders, recharge, shipping, game/account/payment issues
- integration: merchant authorization failures, signature errors, and CGW work order creation
- auth: account login failures, SMS send limit restrictions, account info lookup, account operation history
- knowledge: business knowledge, onboarding guides, account/passport/zone/platform rules
- both: a request spans payment and integration domains
- general: greetings or generic chat"""


TASK_PLANNER_PROMPT = """你是支付中心接入助手的任务规划器。根据用户消息（及可选对话历史）输出 JSON 计划，不要输出其它文字，不要用 markdown 代码块。
## 目标
将用户请求路由到 1～4 个子智能体任务，或由系统直接回复（不派子 agent）。
## 子智能体职责（按意图匹配，非关键词堆砌）
| agent | 负责 | 不负责 |
|-------|------|--------|
| integration | 商户 API 授权失败、签名校验失败、创建 CGW 工单 | 回调/网关/联调/泛化接口报错；账号登录；订单履约 |
| auth | 账号登录失败、账号状态/操作、短信发送受限、实名次数 | 商户接口报错；订单充值发货 |
| payment | 订单/充值/到账/发货/退款/交易状态排查 | 接入文档说明；账号登录；商户授权签名 |
| knowledge | 接入前说明、业务规则、平台 FAQ；**无法 confident 归入上三者时的兜底** | 助手自我介绍；已明确的运行时排障/具体单号排查 |
强信号提示（辅助判断，非充分条件）：
- integration: 未授权/-10250017、签名/-10250018、CGW工单/域账号
- auth: 登录失败、封禁/注销、改密/绑手机、短信受限、实名次数
- payment: 订单号/商户单号/支付单号、充值、到账、发货、退款
## 路由决策（自上而下，命中即停）
1. **直接回复** `reply_mode=direct`, `tasks=[]`：
   - 纯问候 → `direct_kind=greeting`
   - 问「你能做什么/有哪些能力/介绍自己」→ `direct_kind=capabilities`
2. **单域 specialist**：意图明确时只派 1 个 task（integration / auth / payment / knowledge）
3. **跨域拆分**：同时存在多个明确 domain（如「未授权 + 订单未到账」）→ 多个 task，`depends_on` 为空，可并行
4. **顺序依赖**：「先…再…」「如果…再…」→ 用 `depends_on` 表达先后
5. **模糊兜底**：无法 confident 选择 payment/integration/auth → **恰好 1 个 knowledge task**（不用 general）
6. **general**：仅用于合并总结多个子Agent的返回结果
## 规划约束
- 能 1 个 task 解决就不要拆；最多 4 个 task
- 不要创建 synthesis task（supervisor 会汇总）
- `instruction` 用中文，具体、可执行，且不超过目标 agent 职责范围
- 有对话历史时，结合上下文理解指代（如「继续查」「刚才那个订单」）
## 输出 JSON Schema
{
  "reason": "简短中文说明",
  "reply_mode": "direct|tasks",
  "direct_kind": "greeting|capabilities",   // reply_mode=direct 时必填
  "tasks": [
    {
      "id": "唯一 task id",
      "agent": "knowledge|integration|auth|payment|general",
      "title": "中文短标题",
      "instruction": "该 agent 要做什么",
      "depends_on": []
    }
  ]
}
规则：
- `reply_mode=direct` 时 `tasks` 必须为 `[]`
- `reply_mode=tasks` 时省略 `direct_kind`；tasks 至少 1 项
## 示例（格式参考，勿照抄 reason 措辞）
用户: 你好
→ {"reason":"纯问候","reply_mode":"direct","direct_kind":"greeting","tasks":[]}
用户: 你有哪些能力？
→ {"reason":"问助手能力","reply_mode":"direct","direct_kind":"capabilities","tasks":[]}
用户: 如何接入统一收银台？
→ {"reason":"接入说明","reply_mode":"tasks","tasks":[{"id":"knowledge_1","agent":"knowledge","title":"说明接入流程","instruction":"概述统一收银台接入步骤与前置条件","depends_on":[]}]}
用户: return_code=-10250017 商户未授权，同时订单也没到账
→ {"reason":"授权+履约跨域","reply_mode":"tasks","tasks":[
  {"id":"integration_1","agent":"integration","title":"排查商户未授权","instruction":"按 integration skill 排查授权失败","depends_on":[]},
  {"id":"payment_1","agent":"payment","title":"排查订单未到账","instruction":"分析未到账原因并列出还需的信息","depends_on":[]}
]}
用户: 帮我看看这个问题怎么处理？
→ {"reason":"意图不明，knowledge 兜底","reply_mode":"tasks","tasks":[{"id":"knowledge_1","agent":"knowledge","title":"初步解答并引导补充","instruction":"给出可能方向并说明需补充的关键信息（订单号/账号/报错码等）","depends_on":[]}]}
"""


def _build_planner_user_prompt(message: str, *, conversation_history: str = "") -> str:
    """Per-turn planner input: user question (+ optional history). Rules live in TASK_PLANNER_PROMPT."""
    text = (message or "").strip()
    history = (conversation_history or "").strip()
    if history:
        return f"【对话历史】\n{history}\n\n【当前问题】\n{text or '（空）'}"
    return text or "（空消息）"


AGENT_DISPLAY_NAMES = {
    "payment": "Payment Agent",
    "integration": "Integration Agent",
    "auth": "Auth Agent",
    "knowledge": "Knowledge Agent",
    "general": "General Agent",
}

AGENT_REGISTRY: list[dict[str, Any]] = [
    {
        "key": "supervisor",
        "name": "access-assistant",
        "display_name": "Access Assistant 主智能体",
        "role": "supervisor",
        "parent_key": None,
        "description": "LangGraph Supervisor，负责任务规划、路由编排与子智能体协调。",
        "skill_slugs": [],
    },
    {
        "key": "payment",
        "name": "access-assistant-payment",
        "display_name": "支付子智能体",
        "role": "sub",
        "parent_key": "supervisor",
        "description": "处理订单、充值、到账、发货、退款等支付履约问题。",
        "skill_slugs": ["payment-assistant"],
    },
    {
        "key": "integration",
        "name": "access-assistant-integration",
        "display_name": "接入子智能体",
        "role": "sub",
        "parent_key": "supervisor",
        "description": "处理商户授权异常、签名错误、创建 CGW 工单三类接入排障问题。",
        "skill_slugs": ["integration-support-assistant"],
    },
    {
        "key": "auth",
        "name": "access-assistant-auth",
        "display_name": "认证子智能体",
        "role": "sub",
        "parent_key": "supervisor",
        "description": "处理账号登录失败、短信发送受限、账号信息查询、账号操作记录查询等认证排障问题。",
        "skill_slugs": list(AUTH_SKILL_NAMES),
    },
    {
        "key": "knowledge",
        "name": "access-assistant-knowledge",
        "display_name": "知识子智能体",
        "role": "sub",
        "parent_key": "supervisor",
        "description": "回答接入文档、业务规则、能力说明与 FAQ 类问题。",
        "skill_slugs": ["knowledge-assistant"],
    },
    {
        "key": "general",
        "name": "access-assistant-general",
        "display_name": "通用子智能体",
        "role": "sub",
        "parent_key": "supervisor",
        "description": "处理问候、简单对话，并汇总各子智能体输出。",
        "skill_slugs": [],
    },
]

AGENT_CONCURRENCY_LIMITS = {
    "payment": 1,
    "integration": 1,
    "auth": 1,
    "knowledge": 1,
    "general": 1,
}

# 无依赖时直接把用户原问题交给子 Agent，避免 planner 长 instruction 诱导模型跳过 MCP/Skill。
SPECIALIST_DIRECT_MESSAGE_AGENTS = frozenset({"auth", "payment", "integration", "knowledge", "general"})

AUTH_MCP_RETRY_SUFFIX = """

【系统提醒】上一轮未检测到 auth-mcp-server 工具调用。禁止凭记忆或猜测作答。
必须：1) load 与问题匹配的 auth skill（如 auth-login-failure）；2) 调用所需 auth-mcp-server MCP；3) 密文用 decrypt_mcp_result 解密后再输出。处理失败时整体流程最多重试 2 次。"""


def _auth_mcp_tools_used(tool_names: list[str]) -> bool:
    return any(name.startswith("auth-mcp-server_") for name in tool_names)


def _format_tool_calls_for_log(tool_names: list[str]) -> str:
    return ",".join(tool_names) if tool_names else "(none)"


def _format_planner_capability_lines() -> list[str]:
    lines: list[str] = []
    for item in AGENT_REGISTRY:
        if item.get("role") != "sub" or item.get("key") == "general":
            continue
        display_name = str(item.get("display_name", "")).strip()
        description = str(item.get("description", "")).strip()
        if display_name and description:
            lines.append(f"- **{display_name}**：{description}")
    return lines


def build_planner_direct_response(
    message: str,
    *,
    direct_kind: str = "greeting",
) -> str:
    """Build a fast greeting/capability reply without invoking sub-agents."""
    capability_lines = _format_planner_capability_lines()
    kind = (direct_kind or "greeting").strip().lower()

    if kind == "capabilities":
        intro = "我可以协调以下子智能体帮你处理问题："
    else:
        intro = "你好！很高兴为你服务。\n\n我可以协调以下子智能体帮你处理问题："

    body = "\n".join(capability_lines) if capability_lines else "- 支付、接入、认证、知识等业务排障与咨询"
    closing = "请直接描述你的具体问题，我会路由到对应专家处理。"
    return f"{intro}\n\n{body}\n\n{closing}"


PAYMENT_STRONG_SIGNALS = (
    "订单号",
    "商户单号",
    "支付单号",
    "订单查询",
    "交易状态",
    "充值成功但未到账",
    "充值未到账",
    "未发货",
    "发货失败",
    "补单",
    "退款",
)

PAYMENT_CONTEXT_SIGNALS = (
    "订单",
    "充值",
    "到账",
    "发货",
    "支付结果",
    "履约",
    "玩家",
    "游戏账号",
)

INTEGRATION_STRONG_SIGNALS = (
    "商户未授权",
    "授权失败",
    "no authority",
    "-10250017",
    "签名错误",
    "签名失败",
    "check signature failed",
    "-10250018",
    "验签失败",
    "创建工单",
    "cgw工单",
    "域账号",
)

INTEGRATION_CONTEXT_SIGNALS = (
    "merchant_name",
    "商户",
    "return_code",
    "return_message",
    "signature",
    "signature_method",
    "未授权",
    "验签",
)

AUTH_STRONG_SIGNALS = (
    "登录失败",
    "登不上",
    "无法登录",
    "登录异常",
    "登录不上",
    "短信发送次数",
    "短信次数被限制",
    "短信发送受限",
    "发送短信受限",
    "短信受限",
    "短信登录",
    "账号状态",
    "操作记录",
    "登录记录",
    "登录行为",
    "账号信息",
    "账号查询",
    "改密",
    "绑手机",
    "账号封禁",
    "账号冻结",
    "账号注销",
)

AUTH_CONTEXT_SIGNALS = (
    "登录",
    "账号",
    "account",
    "通行证",
    "票据",
    "ticket",
    "密码",
    "封禁",
    "冻结",
    "注销",
    "上架",
    "下线",
    "操作行为",
    "行为记录",
)

KNOWLEDGE_STRONG_SIGNALS = (
    "如何接入",
    "接入流程",
    "接入文档",
    "接入说明",
    "是否支持",
    "支持什么",
    "业务规则",
    "平台规则",
    "账号体系",
    "实名认证",
    "通行证",
    "区服",
)

KNOWLEDGE_CONTEXT_SIGNALS = (
    "统一收银台",
    "文档",
    "流程",
    "规则",
    "说明",
    "指南",
    "faq",
    "护照",
    "passport",
    "onboarding",
    "guide",
)

KNOWLEDGE_QUESTION_SIGNALS = (
    "如何",
    "怎么接",
    "是否",
    "能否",
    "是什么",
    "有什么区别",
    "说明",
    "介绍",
)

GENERAL_SIGNALS = ("你好", "您好", "hello", "hi", "早上好", "晚上好", "谢谢")
COMPLEX_REQUEST_MARKERS = (
    "同时",
    "另外",
    "顺便",
    "并且",
    "以及",
    "一并",
    "先",
    "再帮我",
    "再看",
    "再判断",
    "然后",
    "如果",
    "若",
)


def _resolve_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _resolve_retry_count(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _resolve_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0.1, float(raw))
    except ValueError:
        return default


def _parse_bool_env(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _is_planner_transient_error(exc: BaseException) -> bool:
    """Network / gateway errors where an immediate retry often succeeds."""
    type_name = type(exc).__name__.lower()
    if any(token in type_name for token in ("connection", "timeout", "connect")):
        return True
    msg = str(exc).lower()
    markers = (
        "connection error",
        "connection reset",
        "connect timeout",
        "read timeout",
        "timed out",
        "temporarily unavailable",
        "502",
        "503",
        "504",
    )
    return any(marker in msg for marker in markers)


def _truncate_for_synthesis(text: str, max_chars: int) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return (
        cleaned[:max_chars].rstrip()
        + f"\n\n…（已截断，原长 {len(cleaned)} 字符）"
    )


def _resolve_lightweight_model_overrides(
    model: Optional[str],
    model_provider: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], bool]:
    """Resolve LIGHTWEIGHT_* env for planner / general agents.

    Returns (model, provider, api_key, base_url, enabled).
    When LIGHTWEIGHT_MODEL is unset, falls back to the main model config.
    """
    lightweight_model = (os.getenv("LIGHTWEIGHT_MODEL") or "").strip() or None
    if not lightweight_model:
        return model, model_provider, None, None, False

    lightweight_provider = (
        (os.getenv("LIGHTWEIGHT_MODEL_PROVIDER") or "").strip() or model_provider
    )
    api_key = (os.getenv("LIGHTWEIGHT_API_KEY") or "").strip() or None
    base_url = (os.getenv("LIGHTWEIGHT_BASE_URL") or "").strip() or None
    return lightweight_model, lightweight_provider, api_key, base_url, True


class SupervisorState(TypedDict, total=False):
    message: str
    thread_id: str
    route: Literal["payment", "integration", "auth", "knowledge", "both", "general"]
    route_reason: str
    router_raw_output: str
    fallback_used: bool
    planner_reason: str
    planner_raw_output: str
    planner_fallback_used: bool
    planned_tasks: list[dict[str, Any]]
    task_results: dict[str, str]
    payment_result: str
    integration_result: str
    auth_result: str
    knowledge_result: str
    final_response: str


class SupervisorSkillsAgent:
    """基于 LangGraph 的主智能体协调器。"""

    def __init__(
        self,
        model: Optional[str] = None,
        model_provider: Optional[str] = None,
        knowledge_model: Optional[str] = None,
        knowledge_model_provider: Optional[str] = None,
        knowledge_api_key_override: Optional[str] = None,
        knowledge_base_url_override: Optional[str] = None,
        knowledge_extra_body_override: Optional[dict[str, Any]] = None,
        skill_paths: Optional[list[Path]] = None,
        working_directory: Optional[Path] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        enable_thinking: Optional[bool] = None,
        show_subagent_progress: Optional[bool] = None,
        show_planner_progress: Optional[bool] = None,
        thinking_budget: int = DEFAULT_THINKING_BUDGET,
    ):
        self.working_directory = working_directory or Path.cwd()
        resolved_enable_thinking = (
            enable_thinking
            if enable_thinking is not None
            else os.getenv("ENABLE_THINKING", str(DEFAULT_ENABLE_THINKING)).strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self.show_subagent_progress = (
            show_subagent_progress
            if show_subagent_progress is not None
            else os.getenv("SHOW_SUBAGENT_PROGRESS", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self.show_planner_progress = (
            show_planner_progress
            if show_planner_progress is not None
            else os.getenv("SHOW_PLANNER_PROGRESS", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        resolved_knowledge_model = knowledge_model or os.getenv("KNOWLEDGE_MODEL")
        resolved_knowledge_model_provider = (
            knowledge_model_provider or os.getenv("KNOWLEDGE_MODEL_PROVIDER")
        )
        resolved_knowledge_api_key = (
            knowledge_api_key_override or os.getenv("KNOWLEDGE_API_KEY")
        )
        resolved_knowledge_base_url = (
            knowledge_base_url_override or os.getenv("KNOWLEDGE_BASE_URL")
        )
        resolved_knowledge_user_token = os.getenv("KNOWLEDGE_USER_TOKEN")
        resolved_knowledge_extra_body = (
            dict(knowledge_extra_body_override) if knowledge_extra_body_override else {}
        )
        if resolved_knowledge_user_token:
            form_data = dict(resolved_knowledge_extra_body.get("form_data", {}))
            form_data.setdefault("userToken", resolved_knowledge_user_token)
            resolved_knowledge_extra_body["form_data"] = form_data

        (
            lightweight_model,
            lightweight_provider,
            lightweight_api_key,
            lightweight_base_url,
            lightweight_enabled,
        ) = _resolve_lightweight_model_overrides(model, model_provider)
        if lightweight_enabled:
            log.info(
                "Lightweight model enabled for planner/general: model=%s provider=%s",
                lightweight_model,
                lightweight_provider,
            )

        skills_root = self.working_directory / ".claude" / "skills"

        self.mcp_registry = MCPToolRegistry(load_mcp_config(self.working_directory))

        payment_skill_dir = skills_root / "payment-assistant"
        integration_skill_dir = skills_root / "integration-support-assistant"
        auth_skill_dir = skills_root / "auth-assistant"
        knowledge_skill_dir = skills_root / "knowledge-assistant"

        self.payment_agent = AccessAssistantAgent(
            model=model,
            model_provider=model_provider,
            skill_paths=[payment_skill_dir],
            allowed_skill_names=["payment-assistant"],
            working_directory=self.working_directory,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_thinking=resolved_enable_thinking,
            thinking_budget=thinking_budget,
            system_prompt_override=PAYMENT_AGENT_PROMPT,
            mcp_registry=self.mcp_registry,
            mcp_agent_key="payment",
        )
        self.enable_thinking = self.payment_agent.enable_thinking
        self.model_provider = self.payment_agent.model_provider
        self.model_name = self.payment_agent.model_name
        self.max_tokens = self.payment_agent.max_tokens
        self.temperature = self.payment_agent.temperature

        self.integration_agent = AccessAssistantAgent(
            model=model,
            model_provider=model_provider,
            skill_paths=[integration_skill_dir],
            allowed_skill_names=["integration-support-assistant"],
            working_directory=self.working_directory,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_thinking=resolved_enable_thinking,
            thinking_budget=thinking_budget,
            system_prompt_override=INTEGRATION_AGENT_PROMPT,
            mcp_registry=self.mcp_registry,
            mcp_agent_key="integration",
        )

        default_max_tokens = max_tokens or _resolve_int_env("MAX_TOKENS", 4096)
        auth_max_tokens = _resolve_int_env("AUTH_MAX_TOKENS", default_max_tokens)

        self.auth_agent = AccessAssistantAgent(
            model=model,
            model_provider=model_provider,
            skill_paths=[auth_skill_dir],
            allowed_skill_names=list(AUTH_SKILL_NAMES),
            working_directory=self.working_directory,
            max_tokens=auth_max_tokens,
            temperature=temperature,
            enable_thinking=resolved_enable_thinking,
            thinking_budget=thinking_budget,
            system_prompt_override=AUTH_AGENT_PROMPT,
            tools_override=AUTH_AGENT_TOOLS,
            append_skill_metadata=True,
            mcp_registry=self.mcp_registry,
            mcp_agent_key="auth",
        )
        auth_mcp_tool_names = [tool.name for tool in self.auth_agent.mcp_tools]
        log.info(
            "Auth agent ready: mcp_tools=%s mcp_errors=%s",
            auth_mcp_tool_names or "(none)",
            self.mcp_registry.load_errors.get("auth-mcp-server"),
        )
        if not auth_mcp_tool_names:
            log.warning(
                "Auth agent has no MCP tools loaded; auth-mcp-server may be unreachable at startup"
            )

        general_max_tokens = _resolve_int_env("GENERAL_MAX_TOKENS", default_max_tokens)
        synthesis_max_tokens = _resolve_int_env("SYNTHESIS_MAX_TOKENS", 512)
        self.synthesis_input_max_chars = _resolve_int_env("SYNTHESIS_INPUT_MAX_CHARS", 2000)
        log.info(
            "Agent token limits: auth_max_tokens=%s general_max_tokens=%s synthesis_max_tokens=%s "
            "synthesis_input_max_chars=%s",
            auth_max_tokens,
            general_max_tokens,
            synthesis_max_tokens,
            self.synthesis_input_max_chars,
        )

        self.general_agent = AccessAssistantAgent(
            model=lightweight_model or model,
            model_provider=lightweight_provider or model_provider,
            api_key_override=lightweight_api_key,
            base_url_override=lightweight_base_url,
            skill_paths=[],
            allowed_skill_names=[],
            working_directory=self.working_directory,
            max_tokens=general_max_tokens,
            temperature=temperature,
            enable_thinking=resolved_enable_thinking,
            thinking_budget=thinking_budget,
            system_prompt_override=GENERAL_CHAT_PROMPT,
            tools_override=[],
            append_skill_metadata=False,
        )

        self.synthesis_agent = AccessAssistantAgent(
            model=lightweight_model or model,
            model_provider=lightweight_provider or model_provider,
            api_key_override=lightweight_api_key,
            base_url_override=lightweight_base_url,
            skill_paths=[],
            allowed_skill_names=[],
            working_directory=self.working_directory,
            max_tokens=synthesis_max_tokens,
            temperature=temperature,
            enable_thinking=resolved_enable_thinking,
            thinking_budget=thinking_budget,
            system_prompt_override=SYNTHESIS_AGENT_PROMPT,
            tools_override=[],
            append_skill_metadata=False,
        )

        self.planner_agent = AccessAssistantAgent(
            model=lightweight_model or model,
            model_provider=lightweight_provider or model_provider,
            api_key_override=lightweight_api_key,
            base_url_override=lightweight_base_url,
            skill_paths=[],
            allowed_skill_names=[],
            working_directory=self.working_directory,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_thinking=resolved_enable_thinking,
            thinking_budget=thinking_budget,
            system_prompt_override=TASK_PLANNER_PROMPT,
            tools_override=[],
            append_skill_metadata=False,
            request_timeout=_resolve_float_env("PLANNER_REQUEST_TIMEOUT", 30.0),
            max_retries=_resolve_retry_count("PLANNER_SDK_MAX_RETRIES", 1),
        )
        self._warmup_planner_agent()
        self.max_parallel_tasks = max(
            1,
            int(os.getenv("SUPERVISOR_MAX_PARALLEL_TASKS", "3")),
        )

        self.knowledge_agent = AccessAssistantAgent(
            model=resolved_knowledge_model or model,
            model_provider=resolved_knowledge_model_provider or model_provider,
            api_key_override=resolved_knowledge_api_key,
            base_url_override=resolved_knowledge_base_url,
            extra_body_override=resolved_knowledge_extra_body or None,
            skill_paths=[knowledge_skill_dir],
            allowed_skill_names=["knowledge-assistant"],
            working_directory=self.working_directory,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_thinking=resolved_enable_thinking,
            thinking_budget=thinking_budget,
            system_prompt_override=KNOWLEDGE_AGENT_PROMPT,
            tools_override=[],
            append_skill_metadata=False,
        )

        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(SupervisorState)
        graph.add_node("route", self._route_node)
        graph.add_node("general", self._general_node)
        graph.add_node("payment", self._payment_node)
        graph.add_node("integration", self._integration_node)
        graph.add_node("knowledge", self._knowledge_node)
        graph.add_node("synthesize", self._synthesize_node)

        graph.add_edge(START, "route")
        graph.add_conditional_edges(
            "route",
            self._route_branch,
            {
                "general": "general",
                "payment": "payment",
                "integration": "integration",
                "knowledge": "knowledge",
                "both": "payment",
            },
        )
        graph.add_conditional_edges(
            "payment",
            self._after_payment_branch,
            {
                "integration": "integration",
                "synthesize": "synthesize",
            },
        )
        graph.add_edge("knowledge", END)
        graph.add_edge("integration", "synthesize")
        graph.add_edge("general", END)
        graph.add_edge("synthesize", END)
        grap_desc = graph.compile()
        # png_data = grap_desc.get_graph().draw_mermaid_png()
        # with open("graph.png", "wb") as f:
        #     f.write(png_data)
        # log.info("图已保存到 graph.png")
        return grap_desc

    def _detect_route(self, message: str) -> tuple[str, str]:
        """规则兜底路由，在 LLM 路由失败时使用。"""
        text = message.lower()
        has_payment_strong = any(keyword in text for keyword in PAYMENT_STRONG_SIGNALS)
        has_payment_context = any(keyword in text for keyword in PAYMENT_CONTEXT_SIGNALS)
        has_integration_strong = any(keyword in text for keyword in INTEGRATION_STRONG_SIGNALS)
        has_integration_context = any(keyword in text for keyword in INTEGRATION_CONTEXT_SIGNALS)
        has_auth_strong = any(keyword in text for keyword in AUTH_STRONG_SIGNALS)
        has_auth_context = any(keyword in text for keyword in AUTH_CONTEXT_SIGNALS)
        has_knowledge_strong = any(keyword in text for keyword in KNOWLEDGE_STRONG_SIGNALS)
        has_knowledge_context = any(keyword in text for keyword in KNOWLEDGE_CONTEXT_SIGNALS)
        has_knowledge_question = any(keyword in text for keyword in KNOWLEDGE_QUESTION_SIGNALS)
        has_general = any(keyword in text for keyword in GENERAL_SIGNALS)

        payment_signal = has_payment_strong or (
            has_payment_context and any(keyword in text for keyword in ("查询", "状态", "到账", "发货", "成功", "失败"))
        )
        integration_signal = has_integration_strong or (
            has_integration_context
            and any(
                keyword in text
                for keyword in (
                    "未授权",
                    "no authority",
                    "签名",
                    "signature",
                    "-10250017",
                    "-10250018",
                    "check signature failed",
                )
            )
        )
        auth_signal = has_auth_strong or (
            has_auth_context and any(keyword in text for keyword in ("失败", "异常", "封禁", "注销", "状态", "记录", "查询"))
        )
        knowledge_signal = has_knowledge_strong or (
            has_knowledge_context and has_knowledge_question
        )
        merchant_integration_signal = integration_signal and any(
            keyword in text
            for keyword in (
                "商户",
                "merchant_name",
                "return_code",
                "signature",
                "no authority",
                "check signature failed",
                "-10250017",
                "-10250018",
            )
        )

        if payment_signal and integration_signal:
            return "both", "同时包含技术排障信号和具体订单/交易信号，按双领域流程处理。"
        if auth_signal and not merchant_integration_signal:
            return "auth", "识别为账号登录、账号状态或操作记录类认证问题，路由到 auth 节点。"
        if integration_signal:
            return "integration", "识别为商户授权失败、签名错误或创建工单问题，路由到 integration 节点。"
        if payment_signal:
            return "payment", "识别为订单、充值、到账、发货或履约结果问题，路由到 payment 节点。"
        if knowledge_signal:
            return "knowledge", "识别为接入前说明、能力咨询、规则说明或 FAQ 类问题，路由到 knowledge 节点。"
        if has_general:
            return "general", "识别为通用问候或简单对话，路由到 general 节点。"
        return "general", "未命中特定领域关键词，先按通用节点处理。"

    def _extract_json_payload(self, response_text: str) -> dict[str, Any] | None:
        """从模型响应中提取 JSON 对象。"""
        log.info("Planner JSON extract response_text=%s", response_text)
        if not response_text:
            return None

        candidate = response_text.strip()
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if match:
            candidate = match.group(0)

        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _parse_task_plan_response(self, response_text: str) -> dict[str, Any] | None:
        """解析任务规划输出并进行规范化。"""
        payload = self._extract_json_payload(response_text)
        if payload is None:
            return None

        reason = str(payload.get("reason", "")).strip() or "任务规划完成。"
        reply_mode = str(payload.get("reply_mode", "tasks")).strip().lower() or "tasks"
        if reply_mode == "direct":
            direct_kind = str(payload.get("direct_kind", "greeting")).strip().lower() or "greeting"
            if direct_kind not in {"greeting", "capabilities"}:
                direct_kind = "greeting"
            return {
                "reason": reason,
                "reply_mode": "direct",
                "direct_kind": direct_kind,
                "tasks": [],
            }

        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            return None

        normalized_tasks: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index, raw_task in enumerate(raw_tasks[:4], 1):
            if not isinstance(raw_task, dict):
                continue

            agent_name = str(raw_task.get("agent", "")).strip().lower()
            if agent_name not in AGENT_DISPLAY_NAMES:
                continue

            task_id = str(raw_task.get("id", "")).strip() or f"{agent_name}_{index}"
            if task_id in seen_ids:
                task_id = f"{task_id}_{index}"
            seen_ids.add(task_id)

            title = str(raw_task.get("title", "")).strip() or f"{agent_name} 子任务 {index}"
            instruction = (
                str(raw_task.get("instruction", "")).strip()
                or str(raw_task.get("prompt", "")).strip()
                or title
            )
            raw_depends_on = raw_task.get("depends_on", [])
            depends_on = []
            if isinstance(raw_depends_on, list):
                depends_on = [str(dep).strip() for dep in raw_depends_on if str(dep).strip()]

            normalized_tasks.append(
                {
                    "id": task_id,
                    "agent": agent_name,
                    "title": title,
                    "instruction": instruction,
                    "depends_on": depends_on,
                }
            )

        if not normalized_tasks:
            return None

        valid_ids = {task["id"] for task in normalized_tasks}
        for task in normalized_tasks:
            task["depends_on"] = [
                dep for dep in task["depends_on"] if dep in valid_ids and dep != task["id"]
            ]

        reason = str(payload.get("reason", "")).strip() or "任务规划完成。"
        return {"reason": reason, "reply_mode": "tasks", "tasks": normalized_tasks}

    def _is_direct_reply_plan(self, plan: dict[str, Any]) -> bool:
        return str(plan.get("reply_mode", "")).strip().lower() == "direct"

    def _build_planner_direct_response(self, message: str, plan: dict[str, Any]) -> str:
        return build_planner_direct_response(
            message,
            direct_kind=str(plan.get("direct_kind", "greeting")),
        )

    def _build_fallback_task_plan(self, message: str) -> dict[str, Any]:
        """Planner 不可用时的保底计划，交给 general 子智能体处理。"""
        return {
            "reason": "任务规划失败，回退到通用子智能体处理。",
            "reply_mode": "tasks",
            "tasks": [
                {
                    "id": "general_1",
                    "agent": "general",
                    "title": "处理用户问题",
                    "instruction": message,
                    "depends_on": [],
                }
            ],
        }

    def _warmup_planner_agent(self) -> None:
        """Establish the first LLM connection during startup instead of on the user request."""
        if not _parse_bool_env("PLANNER_WARMUP", True):
            return
        warmup_prompt = '用户问题：ping\n请返回：{"reason":"warmup","reply_mode":"direct","direct_kind":"greeting","tasks":[]}'
        try:
            started = time.perf_counter()
            self._invoke_planner_llm(warmup_prompt, thread_id="__planner_warmup__")
            elapsed_ms = (time.perf_counter() - started) * 1000
            log.info("Planner warmup succeeded in %.1f ms", elapsed_ms)
        except Exception as exc:
            log.warning("Planner warmup failed (non-fatal): %s", exc)

    def _invoke_planner_llm(self, planner_prompt: str, thread_id: str) -> str:
        result = self.planner_agent.invoke(
            planner_prompt,
            thread_id=f"{thread_id}-planner",
        )
        return self.planner_agent.get_last_response(result)

    def _finalize_planner_response(
        self,
        message: str,
        response_text: str,
    ) -> tuple[dict[str, Any], str, bool]:
        parsed = self._parse_task_plan_response(response_text)
        if parsed is not None:
            if self._is_direct_reply_plan(parsed):
                log.info("Task plan success: reply_mode=direct kind=%s", parsed.get("direct_kind"))
            else:
                log.info("Task plan success: tasks=%s", [task["id"] for task in parsed["tasks"]])
            return parsed, response_text, False
        log.warning("Task plan parse failed, fallback to general agent. raw=%s", response_text)
        return self._build_fallback_task_plan(message), response_text, True

    def _plan_tasks(
        self,
        message: str,
        thread_id: str,
        *,
        conversation_history: str = "",
    ) -> tuple[dict[str, Any], str, bool]:
        """使用 LLM planner 拆解任务计划。"""
        planner_prompt = _build_planner_user_prompt(
            message,
            conversation_history=conversation_history,
        )
        """失败重试次数"""
        max_retries = _resolve_retry_count("PLANNER_MAX_RETRIES", 2)
        """重试间隔时间"""
        retry_backoff = _resolve_float_env("PLANNER_RETRY_BACKOFF_SECONDS", 0.1)

        with stage_io(
            "planner",
            thread_id=thread_id,
            message=message,
            prompt=planner_prompt,
            max_retries=max_retries,
        ) as out:
            last_error = ""
            for attempt in range(max_retries + 1):
                if attempt > 0:
                    sleep_seconds = retry_backoff * attempt
                    log.warning(
                        "Planner retry attempt=%s/%s after transient error: %s; sleeping %.1fs",
                        attempt + 1,
                        max_retries + 1,
                        last_error,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)
                try:
                    response_text = self._invoke_planner_llm(planner_prompt, thread_id)
                    plan, raw_output, fallback_used = self._finalize_planner_response(message, response_text)
                    out["plan"] = plan
                    out["raw_output"] = raw_output
                    out["fallback_used"] = fallback_used
                    out["attempts"] = attempt + 1
                    return plan, raw_output, fallback_used
                except Exception as exc:
                    last_error = str(exc)
                    if attempt < max_retries and _is_planner_transient_error(exc):
                        continue
                    log.warning("Task plan failed, fallback to general agent. error=%s", exc)
                    fallback = self._build_fallback_task_plan(message)
                    out["plan"] = fallback
                    out["raw_output"] = last_error
                    out["fallback_used"] = True
                    out["attempts"] = attempt + 1
                    return fallback, last_error, True

    def _get_agent_runtime(self, agent_name: str) -> tuple[AccessAssistantAgent, str]:
        if agent_name == "payment":
            return self.payment_agent, AGENT_DISPLAY_NAMES[agent_name]
        if agent_name == "integration":
            return self.integration_agent, AGENT_DISPLAY_NAMES[agent_name]
        if agent_name == "auth":
            return self.auth_agent, AGENT_DISPLAY_NAMES[agent_name]
        if agent_name == "knowledge":
            return self.knowledge_agent, AGENT_DISPLAY_NAMES[agent_name]
        return self.general_agent, AGENT_DISPLAY_NAMES["general"]

    def _run_specialist_agent(
        self,
        agent: AccessAssistantAgent,
        agent_name: str,
        message: str,
        thread_id: str,
    ) -> tuple[dict, list[str], str]:
        result, tool_calls = agent.invoke_with_tool_trace(message, thread_id=thread_id)
        if agent_name == "auth" and not _auth_mcp_tools_used(tool_calls):
            log.warning(
                "Auth agent skipped MCP on first pass: thread_id=%s tools=%s",
                thread_id,
                _format_tool_calls_for_log(tool_calls),
            )
            retry_message = message + AUTH_MCP_RETRY_SUFFIX
            result, tool_calls = agent.invoke_with_tool_trace(
                retry_message,
                thread_id=f"{thread_id}-mcp-retry",
            )
            if not _auth_mcp_tools_used(tool_calls):
                log.error(
                    "Auth agent still skipped MCP after retry: thread_id=%s tools=%s",
                    thread_id,
                    _format_tool_calls_for_log(tool_calls),
                )
        response = agent.get_last_response(result)
        return result, tool_calls, response

    def _stream_specialist_agent_events(
        self,
        agent: AccessAssistantAgent,
        agent_name: str,
        message: str,
        thread_id: str,
    ) -> Iterator[tuple[dict[str, Any], list[str], str]]:
        """Stream sub-agent events; auth tasks retry once if MCP was skipped."""

        def stream_pass(
            task_message: str,
            run_id: str,
            bucket: list[str],
            final_holder: dict[str, str],
        ) -> Iterator[dict[str, Any]]:
            for event in agent.stream_events(task_message, thread_id=run_id):
                event_type = str(event.get("type", ""))
                if event_type == "tool_call":
                    name = str(event.get("name") or "").strip()
                    if name:
                        bucket.append(name)
                elif event_type == "done":
                    final_holder["response"] = str(event.get("response", ""))
                yield event

        tool_calls: list[str] = []
        final_holder = {"response": ""}
        for event in stream_pass(message, thread_id, tool_calls, final_holder):
            yield event, list(tool_calls), final_holder["response"]

        if agent_name != "auth" or _auth_mcp_tools_used(tool_calls):
            return

        log.warning(
            "Auth agent skipped MCP on first pass (stream): thread_id=%s tools=%s",
            thread_id,
            _format_tool_calls_for_log(tool_calls),
        )
        retry_tool_calls: list[str] = []
        retry_message = message + AUTH_MCP_RETRY_SUFFIX
        for event in stream_pass(
            retry_message,
            f"{thread_id}-mcp-retry",
            retry_tool_calls,
            final_holder,
        ):
            yield event, list(retry_tool_calls), final_holder["response"]

        if not _auth_mcp_tools_used(retry_tool_calls):
            log.error(
                "Auth agent still skipped MCP after retry (stream): thread_id=%s tools=%s",
                thread_id,
                _format_tool_calls_for_log(retry_tool_calls),
            )

    def _build_task_message(
        self,
        original_message: str,
        task: dict[str, Any],
        completed_tasks: dict[str, dict[str, Any]],
    ) -> str:
        if (
            task.get("agent") in SPECIALIST_DIRECT_MESSAGE_AGENTS
            and not task.get("depends_on")
        ):
            return original_message

        dependency_sections = []
        for dep_id in task.get("depends_on", []):
            dep_result = completed_tasks.get(dep_id)
            if not dep_result:
                continue
            dependency_sections.append(
                f"依赖任务 {dep_id}（{dep_result['title']}）结果：\n{dep_result['response']}"
            )

        dependency_text = "\n\n".join(dependency_sections) if dependency_sections else "无"
        return f"""你正在执行一个多智能体流程中的子任务。

用户原始问题：
{original_message}

当前子任务：
- task_id: {task['id']}
- 目标 Agent: {task['agent']}
- 子任务标题: {task['title']}
- 子任务指令: {task['instruction']}

已完成的前置依赖结果：
{dependency_text}

执行要求：
1. 只完成当前子任务职责范围内的内容。
2. 如果依赖信息不足，明确指出缺失项。
3. 输出可直接供 supervisor 汇总，不要解释你是如何被调度的。"""

    def _choose_ready_batch(
        self,
        pending_tasks: list[dict[str, Any]],
        completed_tasks: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        ready_tasks = [
            task
            for task in pending_tasks
            if all(dep in completed_tasks for dep in task.get("depends_on", []))
        ]
        if not ready_tasks:
            # 规划器给出了循环依赖时，强制取一个任务继续执行，避免死锁。
            return [pending_tasks[0]], True

        selected: list[dict[str, Any]] = []
        agent_counts: dict[str, int] = {}
        for task in ready_tasks:
            if len(selected) >= self.max_parallel_tasks:
                break
            agent_name = task["agent"]
            limit = AGENT_CONCURRENCY_LIMITS.get(agent_name, 1)
            if agent_counts.get(agent_name, 0) >= limit:
                continue
            selected.append(task)
            agent_counts[agent_name] = agent_counts.get(agent_name, 0) + 1

        return (selected or [ready_tasks[0]], False)

    def _run_task_sync(
        self,
        task: dict[str, Any],
        original_message: str,
        thread_id: str,
        completed_tasks: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        agent, display_name = self._get_agent_runtime(task["agent"])
        task_message = self._build_task_message(
            original_message,
            task,
            completed_tasks,
        )
        run_id = f"{thread_id}-{task['id']}"
        stage_name = f"sub_agent:{task['agent']}:{task['id']}"
        with stage_io(
            stage_name,
            thread_id=run_id,
            agent=task["agent"],
            task_id=task["id"],
            task_title=task["title"],
            task_message=task_message,
        ) as out:
            try:
                _, tool_calls, response = self._run_specialist_agent(
                    agent,
                    task["agent"],
                    task_message,
                    run_id,
                )
                out["response"] = response
                out["tool_calls"] = _format_tool_calls_for_log(tool_calls)
                out["success"] = True
                return {
                    "id": task["id"],
                    "agent": task["agent"],
                    "title": task["title"],
                    "response": response,
                    "success": True,
                    "display_name": display_name,
                }
            except Exception as exc:
                out["response"] = str(exc)
                out["success"] = False
                return {
                    "id": task["id"],
                    "agent": task["agent"],
                    "title": task["title"],
                    "response": str(exc),
                    "success": False,
                    "display_name": display_name,
                }

    def _build_task_synthesis_prompt(
        self,
        original_message: str,
        completed_tasks: dict[str, dict[str, Any]],
    ) -> str:
        max_chars = self.synthesis_input_max_chars
        task_blocks = []
        for task in completed_tasks.values():
            truncated_response = _truncate_for_synthesis(task["response"], max_chars)
            task_blocks.append(
                f"""子任务：{task['title']}
是否成功：{'是' if task['success'] else '否'}
结果摘要：
{truncated_response}"""
            )

        task_results_text = "\n\n".join(task_blocks) if task_blocks else "(无任务结果)"
        return f"""请将下面多个子 Agent 的处理结果整合为一个最终答复。

用户原始问题：
{original_message}

子任务结果：
{task_results_text}

要求：
1. 使用中文回答。
2. 总长度不超过 400 字，最多 5 条要点。
3. 只写结论与下一步，不要复述子 Agent 原文。
4. 站在最终用户视角整合信息，不要暴露内部调度细节。
5. 如果多个子任务存在依赖关系，按因果顺序组织结论。
6. 如果有任务失败或信息不足，明确说明缺什么、建议下一步怎么做。"""

    def _build_execution_state(
        self,
        message: str,
        planned_tasks: list[dict[str, Any]],
        completed_tasks: dict[str, dict[str, Any]],
        planner_reason: str,
        planner_raw_output: str,
        planner_fallback_used: bool,
        final_response: str,
    ) -> SupervisorState:
        payment_responses = [
            task["response"] for task in completed_tasks.values() if task["agent"] == "payment"
        ]
        integration_responses = [
            task["response"] for task in completed_tasks.values() if task["agent"] == "integration"
        ]
        auth_responses = [
            task["response"] for task in completed_tasks.values() if task["agent"] == "auth"
        ]
        knowledge_responses = [
            task["response"] for task in completed_tasks.values() if task["agent"] == "knowledge"
        ]
        task_results = {
            task_id: task["response"]
            for task_id, task in completed_tasks.items()
        }

        return {
            "message": message,
            "thread_id": "",
            "planner_reason": planner_reason,
            "planner_raw_output": planner_raw_output,
            "planner_fallback_used": planner_fallback_used,
            "planned_tasks": planned_tasks,
            "task_results": task_results,
            "payment_result": "\n\n".join(payment_responses).strip(),
            "integration_result": "\n\n".join(integration_responses).strip(),
            "auth_result": "\n\n".join(auth_responses).strip(),
            "knowledge_result": "\n\n".join(knowledge_responses).strip(),
            "final_response": final_response,
        }

    def _execute_plan_sync(
        self,
        message: str,
        thread_id: str,
        plan: dict[str, Any],
        planner_raw_output: str,
        planner_fallback_used: bool,
    ) -> SupervisorState:
        if self._is_direct_reply_plan(plan):
            with stage_io(
                "planner_direct",
                thread_id=thread_id,
                message=message,
                plan=plan,
            ) as out:
                final_response = self._build_planner_direct_response(message, plan)
                out["response"] = final_response
            state = self._build_execution_state(
                message=message,
                planned_tasks=[],
                completed_tasks={},
                planner_reason=plan["reason"],
                planner_raw_output=planner_raw_output,
                planner_fallback_used=planner_fallback_used,
                final_response=final_response,
            )
            state["thread_id"] = thread_id
            return state

        pending_tasks = [dict(task) for task in plan["tasks"]]
        completed_tasks: dict[str, dict[str, Any]] = {}

        while pending_tasks:
            batch, forced = self._choose_ready_batch(pending_tasks, completed_tasks)
            if forced:
                log.warning("Detected cyclic task dependencies. Forcing execution of task=%s", batch[0]["id"])

            with ThreadPoolExecutor(max_workers=min(len(batch), self.max_parallel_tasks)) as executor:
                futures = {
                    executor.submit(
                        self._run_task_sync,
                        task,
                        message,
                        thread_id,
                        completed_tasks.copy(),
                    ): task
                    for task in batch
                }
                for future in as_completed(futures):
                    task_result = future.result()
                    completed_tasks[task_result["id"]] = task_result

            batch_ids = {task["id"] for task in batch}
            pending_tasks = [task for task in pending_tasks if task["id"] not in batch_ids]

        if len(completed_tasks) == 1:
            final_response = next(iter(completed_tasks.values()))["response"]
        else:
            synthesis_prompt = self._build_task_synthesis_prompt(message, completed_tasks)
            with stage_io(
                "synthesis",
                thread_id=thread_id,
                prompt=synthesis_prompt,
                task_count=len(completed_tasks),
            ) as out:
                synthesis_result = self.synthesis_agent.invoke(
                    synthesis_prompt,
                    thread_id=f"{thread_id}-synthesize",
                )
                final_response = self.synthesis_agent.get_last_response(synthesis_result)
                out["response"] = final_response

        state = self._build_execution_state(
            message=message,
            planned_tasks=plan["tasks"],
            completed_tasks=completed_tasks,
            planner_reason=plan["reason"],
            planner_raw_output=planner_raw_output,
            planner_fallback_used=planner_fallback_used,
            final_response=final_response,
        )
        state["thread_id"] = thread_id
        return state

    def _stream_task_batch(
        self,
        batch: list[dict[str, Any]],
        message: str,
        thread_id: str,
        completed_tasks: dict[str, dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        event_queue: Queue[dict[str, Any]] = Queue()
        batch_results: dict[str, dict[str, Any]] = {}

        def worker(task: dict[str, Any]) -> None:
            agent, display_name = self._get_agent_runtime(task["agent"])
            task_message = self._build_task_message(
                message,
                task,
                completed_tasks,
            )
            run_id = f"{thread_id}-{task['id']}"
            stage_name = f"sub_agent:{task['agent']}:{task['id']}"
            final_response = ""
            success = True
            with stage_io(
                stage_name,
                thread_id=run_id,
                agent=task["agent"],
                task_id=task["id"],
                task_title=task["title"],
                task_message=task_message,
            ) as out:
                try:
                    if task["agent"] == "auth":
                        tool_calls: list[str] = []
                        for event, tool_calls, final_response in self._stream_specialist_agent_events(
                            agent,
                            task["agent"],
                            task_message,
                            run_id,
                        ):
                            event_type = str(event.get("type", "message"))
                            if event_type == "done":
                                continue
                            if event_type == "error":
                                success = False
                                final_response = str(event.get("message", ""))
                            payload = dict(event)
                            payload["agent"] = task["agent"]
                            payload["agent_name"] = task["agent"]
                            payload["agent_run_id"] = run_id
                            payload["task_id"] = task["id"]
                            payload["task_title"] = task["title"]
                            event_queue.put({"kind": "event", "payload": payload})
                        out["tool_calls"] = _format_tool_calls_for_log(tool_calls)
                    else:
                        for event in agent.stream_events(task_message, thread_id=run_id):
                            event_type = str(event.get("type", "message"))
                            if event_type == "done":
                                final_response = str(event.get("response", ""))
                                continue
                            if event_type == "error":
                                success = False
                                final_response = str(event.get("message", ""))
                            payload = dict(event)
                            payload["agent"] = task["agent"]
                            payload["agent_name"] = task["agent"]
                            payload["agent_run_id"] = run_id
                            payload["task_id"] = task["id"]
                            payload["task_title"] = task["title"]
                            event_queue.put({"kind": "event", "payload": payload})
                except Exception as exc:
                    success = False
                    final_response = str(exc)
                    event_queue.put(
                        {
                            "kind": "event",
                            "payload": {
                                "type": "agent_error",
                                "agent": "supervisor",
                                "id": run_id,
                                "agent_name": task["agent"],
                                "task_id": task["id"],
                                "task_title": task["title"],
                                "message": final_response,
                            },
                        }
                    )
                out["response"] = final_response
                out["success"] = success

            event_queue.put(
                {
                    "kind": "done",
                    "task": {
                        "id": task["id"],
                        "agent": task["agent"],
                        "title": task["title"],
                        "response": final_response,
                        "success": success,
                        "display_name": display_name,
                    },
                }
            )

        threads = []
        pending_calls: list[dict[str, Any]] = []
        for task in batch:
            agent_name = task["agent"]
            display_name = AGENT_DISPLAY_NAMES.get(agent_name, agent_name)
            run_id = f"{thread_id}-{task['id']}"
            if self.show_subagent_progress:
                pending_calls.append(
                    {
                        "type": "agent_call",
                        "agent": "supervisor",
                        "id": run_id,
                        "agent_name": agent_name,
                        "title": display_name,
                        "task_id": task["id"],
                        "task_title": task["title"],
                        "depends_on": list(task.get("depends_on", [])),
                    }
                )

        for payload in pending_calls:
            yield payload

        for task in batch:
            thread = threading.Thread(target=worker, args=(task,), daemon=True)
            thread.start()
            threads.append(thread)

        remaining = len(threads)
        while remaining > 0:
            item = event_queue.get()
            if item["kind"] == "event":
                if self.show_subagent_progress:
                    yield item["payload"]
                continue

            task_result = item["task"]
            batch_results[task_result["id"]] = task_result
            if self.show_subagent_progress:
                yield {
                    "type": "agent_done" if task_result["success"] else "agent_error",
                    "agent": "supervisor",
                    "id": f"{thread_id}-{task_result['id']}",
                    "agent_name": task_result["agent"],
                    "task_id": task_result["id"],
                    "task_title": task_result["title"],
                    "response": task_result["response"],
                    "success": task_result["success"],
                }
            remaining -= 1

        for thread in threads:
            thread.join()

        return batch_results

    def _stream_plan_execution(
        self,
        message: str,
        thread_id: str,
        plan: dict[str, Any],
        planner_raw_output: str,
        planner_fallback_used: bool,
    ) -> Iterator[dict[str, Any]]:
        if self._is_direct_reply_plan(plan):
            with stage_io(
                "planner_direct",
                thread_id=thread_id,
                message=message,
                plan=plan,
            ) as out:
                final_response = self._build_planner_direct_response(message, plan)
                out["response"] = final_response
            if self.show_planner_progress:
                yield {
                    "type": "thinking",
                    "agent": "supervisor",
                    "content": f"[task plan] {plan['reason']}",
                    "planner_fallback_used": planner_fallback_used,
                    "planner_raw_output": planner_raw_output,
                    "planned_tasks": [],
                }
            yield {
                "type": "text",
                "agent": "supervisor",
                "content": final_response,
            }
            state = self._build_execution_state(
                message=message,
                planned_tasks=[],
                completed_tasks={},
                planner_reason=plan["reason"],
                planner_raw_output=planner_raw_output,
                planner_fallback_used=planner_fallback_used,
                final_response=final_response,
            )
            state["thread_id"] = thread_id
            yield {"type": "done", "agent": "supervisor", "response": final_response}
            return state

        completed_tasks: dict[str, dict[str, Any]] = {}
        pending_tasks = [dict(task) for task in plan["tasks"]]

        if self.show_planner_progress:
            yield {
                "type": "thinking",
                "agent": "supervisor",
                "content": f"[task plan] {plan['reason']}",
                "planner_fallback_used": planner_fallback_used,
                "planner_raw_output": planner_raw_output,
                "planned_tasks": plan["tasks"],
            }

        while pending_tasks:
            batch, forced = self._choose_ready_batch(pending_tasks, completed_tasks)
            if forced and self.show_planner_progress:
                yield {
                    "type": "thinking",
                    "agent": "supervisor",
                    "content": f"[dependency override] 检测到循环依赖，强制执行任务 {batch[0]['id']}。",
                }

            batch_results = yield from self._stream_task_batch(
                batch,
                message,
                thread_id,
                completed_tasks.copy(),
            )
            completed_tasks.update(batch_results)
            batch_ids = {task["id"] for task in batch}
            pending_tasks = [task for task in pending_tasks if task["id"] not in batch_ids]

        if len(completed_tasks) == 1:
            final_response = next(iter(completed_tasks.values()))["response"]
            if final_response:
                yield {
                    "type": "text",
                    "agent": "supervisor",
                    "content": final_response,
                }
        else:
            synthesis_prompt = self._build_task_synthesis_prompt(message, completed_tasks)
            with stage_io(
                "synthesis",
                thread_id=thread_id,
                prompt=synthesis_prompt,
                task_count=len(completed_tasks),
            ) as out:
                final_response = yield from self._stream_supervisor_agent(
                    self.synthesis_agent,
                    synthesis_prompt,
                    thread_id=f"{thread_id}-synthesize",
                )
                out["response"] = final_response

        state = self._build_execution_state(
            message=message,
            planned_tasks=plan["tasks"],
            completed_tasks=completed_tasks,
            planner_reason=plan["reason"],
            planner_raw_output=planner_raw_output,
            planner_fallback_used=planner_fallback_used,
            final_response=final_response,
        )
        state["thread_id"] = thread_id
        yield {"type": "done", "agent": "supervisor", "response": final_response}
        return state

    def _route_node(self, state: SupervisorState) -> SupervisorState:
        route, reason = self._detect_route(state["message"])
        return {
            "route": route,
            "route_reason": reason,
            "router_raw_output": "",
            "fallback_used": False,
        }

    def _route_branch(self, state: SupervisorState) -> str:
        return state["route"]

    def _after_payment_branch(self, state: SupervisorState) -> str:
        return "integration" if state.get("route") == "both" else "synthesize"

    def _general_node(self, state: SupervisorState) -> SupervisorState:
        result = self.general_agent.invoke(state["message"], thread_id=f"{state['thread_id']}-general")
        return {"final_response": self.general_agent.get_last_response(result)}

    def _payment_node(self, state: SupervisorState) -> SupervisorState:
        result = self.payment_agent.invoke(state["message"], thread_id=f"{state['thread_id']}-payment")
        return {"payment_result": self.payment_agent.get_last_response(result)}

    def _integration_node(self, state: SupervisorState) -> SupervisorState:
        result = self.integration_agent.invoke(
            state["message"],
            thread_id=f"{state['thread_id']}-integration",
        )
        return {"integration_result": self.integration_agent.get_last_response(result)}

    def _knowledge_node(self, state: SupervisorState) -> SupervisorState:
        result = self.knowledge_agent.invoke(
            state["message"],
            thread_id=f"{state['thread_id']}-knowledge",
        )
        return {
            "knowledge_result": self.knowledge_agent.get_last_response(result),
            "final_response": self.knowledge_agent.get_last_response(result),
        }

    def _synthesize_node(self, state: SupervisorState) -> SupervisorState:
        route = state.get("route")
        payment_result = state.get("payment_result", "").strip()
        integration_result = state.get("integration_result", "").strip()
        knowledge_result = state.get("knowledge_result", "").strip()

        if route == "payment":
            return {"final_response": payment_result}
        if route == "integration":
            return {"final_response": integration_result}
        if route == "knowledge":
            return {"final_response": knowledge_result}

        max_chars = self.synthesis_input_max_chars
        synthesis_prompt = f"""请将下面两个专业子Agent的处理结果整合为一个最终答复。

用户问题：
{state['message']}

支付子Agent结果：
{_truncate_for_synthesis(payment_result, max_chars) or "(无)"}

接入子Agent结果：
{_truncate_for_synthesis(integration_result, max_chars) or "(无)"}

要求：
1. 使用中文回答
2. 总长度不超过 400 字，最多 5 条要点
3. 只写结论与下一步，不要复述子 Agent 原文
4. 去重并整合信息
5. 如果信息不足，明确告诉用户缺什么"""

        result = self.synthesis_agent.invoke(
            synthesis_prompt,
            thread_id=f"{state['thread_id']}-synthesize",
        )
        return {"final_response": self.synthesis_agent.get_last_response(result)}

    def _build_synthesis_prompt(self, state: SupervisorState) -> str:
        payment_result = state.get("payment_result", "").strip()
        integration_result = state.get("integration_result", "").strip()
        max_chars = self.synthesis_input_max_chars
        return f"""请将下面两个专业子Agent的处理结果整合为一个最终答复。

用户问题：
{state['message']}

支付子Agent结果：
{_truncate_for_synthesis(payment_result, max_chars) or "(无)"}

接入子Agent结果：
{_truncate_for_synthesis(integration_result, max_chars) or "(无)"}

要求：
1. 使用中文回答
2. 总长度不超过 400 字，最多 5 条要点
3. 只写结论与下一步，不要复述子 Agent 原文
4. 去重并整合信息
5. 如果信息不足，明确告诉用户缺什么"""

    def _stream_supervisor_agent(
        self,
        agent: AccessAssistantAgent,
        message: str,
        thread_id: str,
    ) -> Iterator[dict[str, Any]]:
        final_response = ""
        for event in agent.stream_events(message, thread_id=thread_id):
            event_type = str(event.get("type", "message"))
            if event_type == "done":
                final_response = str(event.get("response", ""))
                continue

            payload = dict(event)
            payload["agent"] = "supervisor"
            yield payload

        return final_response

    def _stream_subagent(
        self,
        agent_name: str,
        title: str,
        agent: AccessAssistantAgent,
        message: str,
        thread_id: str,
    ) -> Iterator[dict[str, Any]]:
        run_id = f"{thread_id}-{agent_name}"
        final_response = ""
        yield {
            "type": "agent_call",
            "agent": "supervisor",
            "id": run_id,
            "agent_name": agent_name,
            "title": title,
        }

        try:
            for event in agent.stream_events(message, thread_id=run_id):
                event_type = str(event.get("type", "message"))
                if event_type == "done":
                    final_response = str(event.get("response", ""))
                    yield {
                        "type": "agent_done",
                        "agent": "supervisor",
                        "id": run_id,
                        "agent_name": agent_name,
                        "response": final_response,
                        "success": True,
                    }
                    continue

                if event_type == "error":
                    final_response = str(event.get("message", ""))
                    yield {
                        "type": "agent_error",
                        "agent": "supervisor",
                        "id": run_id,
                        "agent_name": agent_name,
                        "message": final_response,
                    }
                    continue

                payload = dict(event)
                payload["agent"] = agent_name
                payload["agent_run_id"] = run_id
                yield payload
        except Exception as exc:
            final_response = str(exc)
            yield {
                "type": "agent_error",
                "agent": "supervisor",
                "id": run_id,
                "agent_name": agent_name,
                "message": final_response,
            }

        return final_response

    def get_system_prompt(self) -> str:
        return SUPERVISOR_PROMPT

    def get_agent_registry(self) -> list[dict[str, Any]]:
        """Return supervisor and sub-agent metadata for admin registration."""
        return [dict(item) for item in AGENT_REGISTRY]

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        """Return configured MCP servers and loaded tool metadata."""
        return self.mcp_registry.describe()

    def get_discovered_skills(self) -> list[dict[str, Any]]:
        skills: list[dict[str, Any]] = []
        mcp_lookup = {item["name"]: item for item in self.mcp_registry.describe()}

        for item in self.payment_agent.get_discovered_skills():
            skills.append(self._enrich_skill_with_mcps({**item, "agent": "payment"}, mcp_lookup))

        for item in self.integration_agent.get_discovered_skills():
            skills.append(self._enrich_skill_with_mcps({**item, "agent": "integration"}, mcp_lookup))

        for item in self.auth_agent.get_discovered_skills():
            skills.append(self._enrich_skill_with_mcps({**item, "agent": "auth"}, mcp_lookup))

        for item in self.knowledge_agent.get_discovered_skills():
            skills.append(self._enrich_skill_with_mcps({**item, "agent": "knowledge"}, mcp_lookup))

        return skills

    def _enrich_skill_with_mcps(
        self,
        skill: dict[str, Any],
        mcp_lookup: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        server_names = [str(name) for name in skill.get("mcp_servers", []) if str(name).strip()]
        mcps = []
        for server_name in server_names:
            server = mcp_lookup.get(server_name)
            if server:
                mcps.append(server)
            else:
                mcps.append({"name": server_name, "tool_count": 0, "tools": [], "error": "not loaded"})
        skill["mcps"] = mcps
        return skill

    def _build_direct_agent_message(self, message: str) -> str:
        """Build user message for direct auth specialist calls."""
        return self._build_task_message(
            message,
            {
                "id": "direct",
                "agent": "auth",
                "title": "Direct auth",
                "instruction": message,
                "depends_on": [],
            },
            {},
        )

    def invoke_auth(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        """Run the auth sub-agent directly, bypassing the planner/supervisor."""
        req_start = log_start("auth_request", thread_id=thread_id, message=message)
        agent_message = self._build_direct_agent_message(message)
        run_id = f"{thread_id}-auth"
        try:
            _, tool_calls, response = self._run_specialist_agent(
                self.auth_agent,
                "auth",
                agent_message,
                run_id,
            )
            state = {
                "message": message,
                "thread_id": thread_id,
                "agent": "auth",
                "agent_run_id": run_id,
                "tool_calls": tool_calls,
                "final_response": response,
            }
            log_end("auth_request", req_start, thread_id=thread_id, response=response)
            return state
        except Exception as exc:
            log_end("auth_request", req_start, thread_id=thread_id, error=str(exc))
            raise

    def stream_auth_events(self, message: str, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        """Stream auth sub-agent events directly, bypassing the planner/supervisor."""
        req_start = log_start("auth_request", thread_id=thread_id, message=message)
        agent_message = self._build_direct_agent_message(message)
        run_id = f"{thread_id}-auth"
        display_name = AGENT_DISPLAY_NAMES["auth"]
        final_response = ""
        try:
            yield {
                "type": "agent_call",
                "agent": "auth",
                "id": run_id,
                "agent_name": "auth",
                "title": display_name,
            }
            for event, _, response in self._stream_specialist_agent_events(
                self.auth_agent,
                "auth",
                agent_message,
                run_id,
            ):
                event_type = str(event.get("type", ""))
                if event_type == "done":
                    final_response = response or str(event.get("response", ""))
                    continue
                if event_type == "error":
                    final_response = str(event.get("message", ""))
                payload = dict(event)
                payload["agent"] = "auth"
                payload["agent_name"] = "auth"
                payload["agent_run_id"] = run_id
                yield payload

            yield {
                "type": "agent_done",
                "agent": "auth",
                "id": run_id,
                "agent_name": "auth",
                "response": final_response,
            }
            yield {"type": "done", "agent": "auth", "response": final_response}
            log_end("auth_request", req_start, thread_id=thread_id, response=final_response)
        except Exception as exc:
            log_end("auth_request", req_start, thread_id=thread_id, error=str(exc), response=final_response)
            raise

    def invoke(self, message: str, thread_id: str = "default") -> dict[str, Any]:
        req_start = log_start("request", thread_id=thread_id, message=message)
        try:
            plan, planner_raw_output, planner_fallback_used = self._plan_tasks(
                message,
                thread_id,
            )
            state = self._execute_plan_sync(
                message=message,
                thread_id=thread_id,
                plan=plan,
                planner_raw_output=planner_raw_output,
                planner_fallback_used=planner_fallback_used,
            )
            log_end("request", req_start, thread_id=thread_id, response=state.get("final_response", ""))
            return state
        except Exception as exc:
            log_end("request", req_start, thread_id=thread_id, error=str(exc))
            raise

    def stream(self, message: str, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        yield self.invoke(message, thread_id=thread_id)

    def stream_events(self, message: str, thread_id: str = "default") -> Iterator[dict[str, Any]]:
        req_start = log_start("request", thread_id=thread_id, message=message)
        final_response = ""
        planner_fallback_used = False
        try:
            """分析用户消息构建执行任务"""
            plan, planner_raw_output, planner_fallback_used = self._plan_tasks(
                message,
                thread_id,
            )
            for event in self._stream_plan_execution(
                message=message,
                thread_id=thread_id,
                plan=plan,
                planner_raw_output=planner_raw_output,
                planner_fallback_used=planner_fallback_used,
            ):
                if str(event.get("type", "")) == "done":
                    final_response = str(event.get("response", ""))
                yield event
            log_end("request", req_start, thread_id=thread_id, response=final_response)
        except Exception as exc:
            log_end("request", req_start, thread_id=thread_id, error=str(exc), response=final_response)
            raise

    def get_last_response(self, result: dict[str, Any]) -> str:
        return result.get("final_response", "")
