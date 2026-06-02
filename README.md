## 快速开始

### 1. 安装

```bash 
cd access-assistant
uv sync
```

### 2. 配置模型 API

默认文档仍以 Anthropic 为例；也可以切到 OpenAI 协议。

#### Anthropic（默认）

修改 `.env` 文件：


```bash
MODEL_PROVIDER=anthropic
MODEL_NAME=claude-sonnet-4-5-20250929
MODEL_API_KEY=sk-xxx
MODEL_BASE_URL=https://npai.u.sdo.com/v1
```

兼容旧配置，下面这些变量仍然可用：

```bash
ANTHROPIC_AUTH_TOKEN=sk-xxx
ANTHROPIC_BASE_URL=https://npai.u.sdo.com/v1
CLAUDE_MODEL=claude-sonnet-4-5-20250929
```

### 3. CLI 交互式启动

```bash
uv run access-assistant --interactive
```

## CLI 命令

```bash
# 交互式模式
uv run access-assistant --interactive

# 单次执行
uv run access-assistant "列出当前目录"

# 禁用 Thinking
uv run access-assistant --no-thinking "执行 pwd"

# 查看发现的 Skills
uv run access-assistant --list-skills

# 查看 System Prompt
uv run access-assistant --show-prompt
```

### 一键启动（一键启动前后端，脚本待补充）

```bash
./start.sh
```

### 1. 启动后端 API（端口 8000）

```bash
uv run access-assistant
```

### 2. 启动前端（端口 5173）

```bash
cd web
npm install
npm run dev
```

## 项目结构

```
access-assistant/
├── src/access_assistant/
│   ├── agent.py          # Access Assistant Agent
│   ├── cli.py            # CLI 入口 (流式输出)
│   ├── tools.py          # 工具定义 (load_skill, bash, read_file, write_file, glob, grep, edit, list_dir)
│   ├── skill_loader.py   # Skills 发现和加载
│   └── stream/           # 流式处理模块
│       ├── emitter.py    # 事件发射器
│       ├── tracker.py    # 工具调用追踪（支持增量 JSON）
│       ├── formatter.py  # 结果格式化器
│       └── utils.py      # 常量和工具函数
├── examples/                # 单元测试
│   ├── basic_usage.py
│   └── interactive_chat.py
└── .claude/skills/       # Skills
    └── payment-assistant/
        ├── SKILL.md
        └── scripts/payment_assistant.py
```