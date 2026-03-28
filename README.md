# GAI MCP

AI 自动游玩游戏的 MCP 工具。通过截图分析 + AI 决策 + 模拟操作，让 AI 自主玩游戏。

## 工作原理

```
截图 → AI 视觉分析 → 生成操作指令 → 模拟键鼠执行 → 循环
```

- 对游戏窗口截图（支持后台窗口）
- AI 分析画面内容，自主决定下一步操作
- 模拟鼠标点击、键盘输入等操作
- 虚拟桌面隔离，不干扰日常使用

## 功能特性

- **多 AI 引擎** — 支持 Claude、OpenAI、本地模型（Ollama）
- **MCP 协议** — 作为 MCP Server 运行，可被 Claude 等客户端调用
- **Web 控制台** — 浏览器管理游戏配置、实时查看 AI 决策链
- **技能系统** — 通过 Markdown 文件注入游戏知识，提升 AI 表现
- **虚拟桌面** — 自动创建独立桌面运行游戏，操作完切回

## 安装

```bash
# 克隆仓库
git clone https://github.com/RikkaBunny/gai-mcp.git
cd gai-mcp

# 安装依赖
pip install -e .
```

> 需要 Python 3.11+，仅支持 Windows。

## 使用方式

### 1. Web 控制台

```bash
gai-mcp-web
```

浏览器自动打开 `http://localhost:9966`，在页面上：

1. 配置 API 密钥（API Keys 页面）
2. 添加游戏（游戏管理页面）
3. 选择游戏，点击「开始游玩」

### 2. MCP Server

```bash
gai-mcp
```

在支持 MCP 的客户端（如 Claude Desktop）中添加此工具，即可通过对话控制游戏。

**MCP 工具列表：**

| 工具 | 说明 |
|------|------|
| `list_windows` | 列出所有可见窗口 |
| `start_game` | 开始自动游玩 |
| `stop_game` | 停止游玩 |
| `pause_game` | 暂停 |
| `resume_game` | 恢复 |
| `get_status` | 获取当前状态 |
| `set_strategy` | 更新游戏策略提示词 |
| `screenshot` | 手动截图 |
| `execute_action` | 手动执行操作 |

### 3. Claude Desktop 配置

在 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "gai-mcp": {
      "command": "gai-mcp"
    }
  }
}
```

## 配置说明

首次运行后，配置文件保存在 `~/.gai_mcp/user_config.json`。

```yaml
# API 密钥
api_keys:
  anthropic: "sk-ant-..."
  openai: "sk-..."
  claude_base_url: ""       # 自定义地址（可选）
  openai_base_url: ""       # 第三方中转地址（可选）
  local_base_url: "http://localhost:11434"

# AI 模型
ai:
  provider: claude           # claude / openai / local
  claude_model: claude-sonnet-4-20250514
  openai_model: gpt-4o
  local_model: llava         # 必须支持视觉

# 游戏循环
game_loop:
  capture_interval: 2.0      # 截图间隔（秒）
  action_delay: 0.5          # 操作间延迟（秒）
  screenshot_quality: 75     # JPEG 质量
  screenshot_max_width: 1280 # 最大宽度
```

## 技能系统

在 `skills/` 目录下创建 `.md` 文件，为 AI 提供游戏知识：

```markdown
# 扫雷攻略

## 基本规则
- 数字表示周围 8 格中地雷的数量
- 左键点击揭开格子，右键标记地雷
...
```

在游戏配置中指定技能文件：

```json
{
  "skills": ["galgame.md", "minesweeper.md"]
}
```

AI 会将这些知识作为参考，但保留自主判断权。

## 项目结构

```
src/gai_mcp/
├── server.py           # MCP Server 入口
├── game_loop.py        # 核心循环（截图→分析→操作）
├── capturer.py         # 窗口截图（Win32 API）
├── input_controller.py # 键鼠模拟（pyautogui）
├── virtual_desktop.py  # 虚拟桌面管理
├── models.py           # 数据模型
├── config_manager.py   # 配置管理
├── ai_engine/
│   ├── base.py         # AI 引擎基类 + 系统提示词
│   ├── claude.py       # Claude 引擎
│   ├── openai.py       # OpenAI 引擎
│   └── local.py        # 本地模型引擎（Ollama）
└── web/
    ├── app.py          # Web API 路由
    ├── game_runner.py  # Web 游戏运行器
    └── static/         # 前端页面
```

## 注意事项

- 本地模型必须支持视觉（如 `llava`、`qwen2.5-vl`），纯文本模型无法分析截图
- 虚拟桌面功能依赖 Windows 10/11 的多桌面特性
- AI 操作使用归一化坐标（0.0-1.0），自动适配不同分辨率

## License

MIT
