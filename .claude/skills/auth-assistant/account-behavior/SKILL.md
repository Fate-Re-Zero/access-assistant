---
name: auth-account-behavior
description: 查询账号历史行为记录（改密、绑手机、注销等）。当用户提问账号有没有改密码、绑手机、注销操作时激活。
---

# Account Behavior Skill

账号行为记录查询

## 工具说明

### MCP 工具结果解密（decrypt_mcp_result）

`auth-mcp-server` 返回的内容可能是 **AES 加密 + GZIP 压缩** 后的密文。Agent 必须在 MCP 调用成功后，再调用 **`decrypt_mcp_result` 工具**解密，才能读取明文并回复用户。

**解密流程：**

1. 调用 MCP 工具，获取原始返回（是密文字符串，或 JSON 中的 `result` / `data` 字段）
2. 若返回是 JSON，提取密文字段（默认字段名 `result`）
3. 调用 `decrypt_mcp_result`（见下方用法）

**密文识别规则：**

- 返回JSON 形如：`{"result":"<密文>"}` / `{"data":"<密文>"}`
- 密文为长 Base64 字符串（通常以 `bFow` 等字母数字开头，长度 > 100）

**MCP 与解密的关系：**

- 每个MCP工具调用**单独返回**一份密文（JSON 含 `result` / `data`，或纯 Base64 密文），**不会**把多条 MCP 结果合并成一条
- 同一问题需要多个MCP工具时，**同一轮并行**发起各 MCP；**哪个先返回就先 decrypt 哪个**，不必等全部 MCP 都返回

**decrypt_mcp_result 示例：**

```
decrypt_mcp_result(json_payload='{"result":"<密文>"}')
```

输出：`[OK]` 开头 + 明文。

### MCP 工具

| 工具名 | 必填参数 |
|--------|---------|
| `auth-mcp-server_sqg_user_account_behavior` | `inputAccount` |

**使用原则：**

1. 仅调用 `sqg_user_account_behavior`
2. 从问题中提取用户账号传入参数`inputAccount`
3. MCP工具返回密文后立即 `decrypt_mcp_result`

## 工作流程

1. **接收输入** - 用户问题描述
2. **提取参数** - 从问题中提取账号（`inputAccount`）等参数信息
3. **调用MCP工具** - 调用所有需要的 auth-mcp-server 工具
4. **流水解密** - MCP 返回即 `decrypt_mcp_result(json_payload=...)`
5. **结论输出** - 整合解密后的明文，给出明确结论与建议
6. **失败重试** - 若处理失败，从步骤 2 起**整体流程**重试，**最多重试 2 次**（见下节）

## 失败与整体流程重试

**视为处理失败**（尚未给出基于MCP工具 + decrypt的有效结论）：

- 未调用当前问题类型**应当**调用的MCP工具
- MCP工具调用报错、超时或无有效返回
- `decrypt_mcp_result` 返回 `[FAILED]`或者报错
- 工具链异常导致无法按 SKILL 格式输出（非用户刻意缺参）

**重试策略：**

1. 从**整体流程**重头执行：提取参数 → 并行调用MCP工具 → 流水decrypt → 结论输出
2. **最多重试 2 次**（首次处理 + 最多 2 次重试，共最多 **3 轮**完整流程）
3. 3 轮仍失败：明确告知「当前认证工具存在异常，无法完成排查，请联系相关业务开发」；**禁止**凭猜测作答

**重试时禁止：**

- 在同一轮流程内对**同一MCP工具相同参数**反复调用
- 超过 2 次整体流程重试后仍继续重试
- 跳过重试直接输出「像是 / 可能是」类结论

## 回复格式

**仅 1 个一级标题：**

```markdown
## 账号近期操作行为
…
```

### 查询账号操作行为回复（quser_account_behavior）：

```markdown
## 账号操作记录
（sqg_user_account_behavior 解密结果的返回要点，不需要让用户知道结论是通过MCP工具调用所得，所以不要出现工具返回结果显示这类描述）

```

## 处理案例

#### 案例 1：账号操作记录查询（account_behavior）

用户问题：

```
账号 sh00012345 最近有没有改密码或者绑手机？
```

Agent 处理步骤：

1. 调用 `auth-mcp-server_sqg_user_account_behavior`，参数示例：
   ```json
   {"inputAccount": "sh00012345"}
   ```
2. 调用 `decrypt_mcp_result` 解密 MCP 返回结果
3. 按「回复格式」一栏输出（账号操作记录）

## 注意事项
- 使用中文回复用户
- 一定不能带有猜测性的内容（比如：像是 xxx、可能是 xxx）
- 账号信息不足时，明确告知用户需要补充账号等关键信息
