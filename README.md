# Aether（以太）— 通用 AI Agent 框架

> 博采众长，无所不达 — Windows 11 / Linux 双平台原生支持

---

## 目录

1. [快速开始](#一快速开始)
2. [配置大模型 API Key](#二配置大模型-api-key)
3. [CLI 命令行使用](#三cli-命令行使用)
4. [Web API 使用](#四web-api-使用)
5. [项目结构](#五项目结构)
6. [运行测试](#六运行测试)
7. [常见问题](#七常见问题)

---

## 一、快速开始

### Windows 11

```powershell
# 1. 克隆项目
git clone https://github.com/lw24-1936/aether.git
cd aether

# 2. 安装（二选一）
pip install -e .                # 仅核心依赖
pip install -e ".[dev]"         # 含开发工具（pytest/ruff/mypy）

# 3. 启动
aether
```

### Linux (Ubuntu/Debian)

```bash
# 1. 克隆项目
git clone https://github.com/lw24-1936/aether.git
cd aether

# 2. 创建虚拟环境（推荐）
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装
pip install -e ".[dev]"

# 4. 启动
aether
```

### 启动界面

```
╭──────────────────────────────────────────────────────────╮
│ Aether v0.1.0 — The Universal AI Agent Framework         │
│ Platform: windows | Shell: pwsh                          │
│ Model:  openai/gpt-4o                                    │
╰──────────────────────────────────────────────────────────╯
Type /help for commands, /quit to exit. Start chatting!

❯
```

---

## 二、配置大模型 API Key

Aether 支持所有兼容 OpenAI API 格式的大模型提供商。

### 方式一：环境变量（推荐）

```powershell
# Windows PowerShell
$env:OPENAI_API_KEY = "sk-your-key-here"

# Linux / macOS
export OPENAI_API_KEY="sk-your-key-here"
```

### 方式二：配置文件

创建 `~/.config/aether/config.yaml`（Linux）或 `%APPDATA%/aether/config.yaml`（Windows）：

```yaml
model:
  provider: openai          # 提供商名称
  model: gpt-4o             # 模型名称
  api_key: "sk-your-key"    # API Key
  temperature: 0.7
  max_tokens: 4096
```

### 各平台配置示例

#### OpenAI

```bash
export OPENAI_API_KEY="sk-proj-xxxxxxxxxxxxx"
aether --model openai/gpt-4o
```

#### DeepSeek

```bash
export DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxx"
aether --model deepseek/deepseek-chat
```

配置文件方式：

```yaml
model:
  provider: deepseek
  model: deepseek-chat
  api_key: "sk-xxxxxxxxxxxxx"
  api_base: "https://api.deepseek.com/v1"
```

#### 阿里通义千问 (Qwen / DashScope)

```bash
export DASHSCOPE_API_KEY="sk-xxxxxxxxxxxxx"
```

配置文件：

```yaml
model:
  provider: openai           # 通义千问兼容 OpenAI 格式
  model: qwen-plus
  api_key: "sk-xxxxxxxxxxxxx"
  api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1"
```

#### 百度文心一言 (ERNIE)

```bash
export BAIDU_API_KEY="your-api-key"
export BAIDU_SECRET_KEY="your-secret-key"
```

#### 讯飞星火 (Spark)

```bash
export SPARK_API_KEY="your-app-id:your-api-key:your-api-secret"
```

#### 月之暗面 (Moonshot / Kimi)

```bash
export MOONSHOT_API_KEY="sk-xxxxxxxxxxxxx"
```

配置文件：

```yaml
model:
  provider: openai
  model: moonshot-v1-8k
  api_key: "sk-xxxxxxxxxxxxx"
  api_base: "https://api.moonshot.cn/v1"
```

#### 智谱 AI (GLM / ChatGLM)

```bash
export ZHIPUAI_API_KEY="xxxxxxxxxxxxx.xxxxxxxxxxxxx"
```

配置文件：

```yaml
model:
  provider: openai
  model: glm-4
  api_key: "xxxxxxxxxxxxx.xxxxxxxxxxxxx"
  api_base: "https://open.bigmodel.cn/api/paas/v4"
```

#### 本地模型 (Ollama / vLLM)

```bash
# Ollama
ollama pull llama3

# 启动 Aether 连接本地 Ollama
aether --model ollama/llama3
```

配置文件：

```yaml
model:
  provider: openai
  model: llama3
  api_key: "ollama"              # Ollama 不需要真实 key
  api_base: "http://localhost:11434/v1"
```

### API Key 优先级

```
环境变量  >  配置文件  >  默认值
```

---

## 三、CLI 命令行使用

### 基本命令

| 命令 | 说明 |
|------|------|
| `aether` | 启动交互式对话（Textual TUI） |
| `aether --simple` | 启动简化版（Rich REPL） |
| `aether --model openai/gpt-4o` | 指定模型 |
| `aether --debug` | 调试模式 |
| `aether --workdir /path/to/project` | 指定工作目录 |
| `aether --version` | 查看版本 |

### 对话中命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/quit` `/exit` `/q` | 退出 |
| `/clear` | 清屏 |
| `/tools` | 列出可用工具 |
| `/skills` | 列出已加载技能 |
| `/skill <name>` | 查看技能详情 |
| `/memory` | 查看记忆统计 |
| `/remember <内容>` | 保存一条记忆 |
| `/forget <id>` | 删除一条记忆 |
| `/breakers` | 查看断路保护状态 |
| `/config` | 查看当前配置 |

### 内置工具

Aether 可以自主使用以下工具：

| 工具 | 功能 | 权限级别 |
|------|------|----------|
| `terminal` | 执行 Shell 命令（跨平台） | EXECUTE |
| `read_file` | 读取文件 | READ_ONLY |
| `write_file` | 写入文件 | WRITE |
| `search_files` | 正则搜索文件 | READ_ONLY |
| `patch_file` | 查找替换文件内容 | WRITE |

工具调用需要审批（Level 2 以上），你可以选择：
- `A` — 本次通过
- `D` — 拒绝
- `S` — 本次会话全部通过

### 对话示例

```
❯ 帮我创建一个 Python Flask 项目

🔧 terminal: mkdir my-flask-app && cd my-flask-app
✓ terminal done

🔧 write_file: app.py
✓ write_file done

我已经创建了 Flask 项目，包含 app.py ...
```

---

## 四、Web API 使用

### 启动 API 服务器

```bash
# 直接启动
python -m aether.api

# 或指定端口
uvicorn aether.api:create_app --host 0.0.0.0 --port 8420
```

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `POST` | `/chat` | 发送消息 |
| `GET` | `/sessions` | 列出会话 |
| `DELETE` | `/sessions/{id}` | 删除会话 |
| `POST` | `/memory` | 添加记忆 |
| `GET` | `/memory?q=关键词` | 搜索记忆 |
| `GET` | `/memory/stats` | 记忆统计 |
| `GET` | `/skills` | 列出技能 |
| `POST` | `/skills` | 创建技能 |
| `GET` | `/skills/{name}` | 查看技能 |
| `GET` | `/cron` | 列出定时任务 |
| `POST` | `/cron` | 创建定时任务 |
| `GET` | `/breakers` | 断路保护状态 |
| `WS` | `/ws/{session_id}` | WebSocket 流式对话 |

### REST API 示例

```python
import httpx

# 发送消息
resp = httpx.post("http://localhost:8420/chat", json={
    "message": "Hello, what can you do?",
    "session_id": "my-session"
})
print(resp.json()["response"])

# 搜索记忆
resp = httpx.get("http://localhost:8420/memory", params={"q": "preference"})
print(resp.json()["results"])
```

### WebSocket 示例

```python
import asyncio
import json
import websockets

async def chat():
    async with websockets.connect("ws://localhost:8420/ws/my-session") as ws:
        await ws.send(json.dumps({"message": "列出当前目录文件"}))
        while True:
            event = json.loads(await ws.recv())
            if event["type"] == "text_delta":
                print(event["data"]["content"], end="")
            elif event["type"] == "done":
                break

asyncio.run(chat())
```

---

## 五、项目结构

```
aether-project/
├── pyproject.toml          # 项目配置（依赖/构建/工具）
├── requirements.txt        # 精简依赖列表
├── README.md               # 本文件
├── .gitignore
├── .gitattributes          # 跨平台行尾处理
├── skills/                 # 内置技能库
│   └── coding/
│       └── python-debugging/SKILL.md
├── tests/                  # 测试（163 个）
│   ├── test_core.py
│   ├── test_phase1.py
│   ├── test_phase2.py
│   ├── ...
│   └── test_phase9.py
└── src/aether/
    ├── __init__.py
    ├── api.py              # FastAPI Web API
    ├── cli/
    │   ├── main.py         # CLI 入口
    │   ├── tui.py          # Textual 终端 UI
    │   └── rich_cli.py     # 简化版 Rich REPL
    ├── core/
    │   ├── models.py       # 数据模型（12 个 Pydantic）
    │   ├── config.py       # 配置系统
    │   ├── llm.py          # LLM 客户端（httpx）
    │   ├── loop.py         # Agent 主循环
    │   ├── circuit_breaker.py  # 断路保护
    │   ├── security.py     # 安全审批
    │   ├── sandbox.py      # 沙箱执行
    │   ├── audit.py        # 审计日志
    │   ├── orchestrator.py # 多代理编排
    │   ├── cron.py         # 定时任务引擎
    │   └── code_intel.py   # 代码智能
    ├── memory/
    │   ├── store.py        # SQLite FTS5 存储
    │   └── manager.py      # 记忆管理器
    ├── skills/
    │   ├── parser.py       # SKILL.md 解析器
    │   └── manager.py      # 技能管理器
    ├── tools/
    │   ├── terminal.py     # 跨平台终端工具
    │   └── file.py         # 文件操作工具
    ├── protocols/
    │   ├── mcp_client.py   # MCP 客户端
    │   ├── mcp_server.py   # MCP 服务端
    │   ├── a2a_client.py   # A2A 客户端
    │   └── tool_registry.py # 统一工具注册表
    └── platform/
        └── __init__.py     # 跨平台层
```

---

## 六、运行测试

```bash
# 运行全部测试
pytest

# 只运行特定模块
pytest tests/test_core.py
pytest tests/test_phase2.py

# 详细输出
pytest -v

# 跳过慢速测试
pytest -k "not test_execute"
```

当前测试覆盖：**163 个测试，0 失败**。

---

## 七、常见问题

### Q: 启动时提示 `No module named 'xxxx'`

```bash
pip install -e ".[dev]"
```

确保所有依赖已安装。

### Q: `aether` 命令找不到

```bash
# 确认安装成功
pip show aether-agent

# 如果安装在虚拟环境中，确保已激活
source .venv/bin/activate    # Linux
.venv\Scripts\activate       # Windows
```

### Q: API 调用返回 401/403

检查 API Key 是否正确设置：

```bash
echo $OPENAI_API_KEY         # Linux
echo $env:OPENAI_API_KEY     # Windows PowerShell
```

### Q: 工具调用拒绝执行

安全引擎默认拦截危险命令。可以在配置中调整：

```yaml
security:
  auto_approve_level: 2      # Level 0-2 自动通过（需要谨慎）
```

### Q: Windows 终端中文乱码

```powershell
chcp 65001
```

或者使用 Windows Terminal（推荐）。

### Q: 如何添加自定义技能

在 `~/.aether/skills/` 下创建目录，编写 `SKILL.md`：

```markdown
---
name: my-skill
description: 我的自定义技能
triggers: ["关键词1", "关键词2"]
category: custom
---

# 技能步骤

1. 第一步
2. 第二步

## 注意事项
- ...
```

然后 `/skills` 查看，对话中自动匹配触发。

---

## 许可证

MIT License
