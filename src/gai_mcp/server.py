"""MCP Server - 暴露游戏自动游玩工具"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from mcp.server.fastmcp import FastMCP

from .ai_engine import ClaudeEngine, LocalEngine, OpenAIEngine
from .ai_engine.base import AIEngine
from .capturer import WindowCapturer
from .game_loop import GameLoop
from .input_controller import InputController
from .models import GameConfig, GameSession, SessionStatus
from .config_manager import apply_api_keys, load_config as load_user_config, setup_file_logging
from .virtual_desktop import VirtualDesktopManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── 全局状态 ──
mcp = FastMCP("gai-mcp", instructions="AI 自动游玩游戏的 MCP 工具")
_session: Optional[GameSession] = None
_game_loop: Optional[GameLoop] = None
_vd_manager: Optional[VirtualDesktopManager] = None
_capturer: Optional[WindowCapturer] = None


def _load_config() -> dict:
    """加载配置 (优先用户配置，回退到 config.yaml)"""
    user_cfg = load_user_config()
    if user_cfg and user_cfg.get("api_keys", {}).get("anthropic"):
        return user_cfg
    config_path = Path(__file__).parent.parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return user_cfg


def _create_ai_engine(
    provider: str, model: Optional[str], raw_config: dict
) -> AIEngine:
    """根据配置创建 AI 引擎"""
    api_keys = raw_config.get("api_keys", {})
    ai_cfg = raw_config.get("ai", {})
    match provider:
        case "claude":
            cfg = ai_cfg.get("claude", {})
            return ClaudeEngine(
                model=model or ai_cfg.get("claude_model") or cfg.get("model"),
                base_url=api_keys.get("claude_base_url") or None,
            )
        case "openai":
            cfg = ai_cfg.get("openai", {})
            return OpenAIEngine(
                model=model or ai_cfg.get("openai_model") or cfg.get("model"),
                base_url=api_keys.get("openai_base_url") or None,
            )
        case "local":
            cfg = ai_cfg.get("local", {})
            return LocalEngine(
                model=model or ai_cfg.get("local_model") or cfg.get("model"),
                base_url=api_keys.get("local_base_url") or cfg.get("base_url", "http://localhost:11434"),
            )
        case _:
            raise ValueError(f"不支持的 AI 提供者: {provider}")


def _load_skills(raw_config: dict) -> list[dict]:
    """加载技能文件"""
    # 尝试多个可能的 skills 目录位置
    candidates = [
        Path(__file__).parent.parent.parent / "skills",  # 开发环境
        Path(__file__).parent / "skills",                 # 打包后
    ]
    skills_dir = None
    for p in candidates:
        if p.is_dir():
            skills_dir = p
            break

    if not skills_dir:
        return []

    loaded = []
    for filepath in skills_dir.glob("*.md"):
        try:
            content = filepath.read_text(encoding="utf-8")
            lines = content.strip().split("\n")
            name = lines[0].lstrip("# ").strip() if lines else filepath.stem
            loaded.append({"name": name, "content": content})
        except Exception as e:
            logger.error(f"加载技能文件失败 {filepath.name}: {e}")
    return loaded


# ── MCP Tools ──


@mcp.tool()
async def list_windows() -> list[dict]:
    """列出所有可见窗口，用于查找游戏窗口标题

    Returns:
        窗口列表，每个包含 hwnd 和 title
    """
    capturer = WindowCapturer()
    return capturer.list_windows()


@mcp.tool()
async def start_game(
    window_title: str,
    ai_provider: str = "claude",
    ai_model: str | None = None,
    strategy_prompt: str = "",
    capture_interval: float = 2.0,
    action_delay: float = 0.5,
    use_virtual_desktop: bool = True,
) -> dict:
    """开始自动游玩游戏

    Args:
        window_title: 游戏窗口标题 (模糊匹配)
        ai_provider: AI 提供者 (claude/openai/local)
        ai_model: AI 模型名称 (可选，使用默认值)
        strategy_prompt: 游戏策略提示词，告诉 AI 怎么玩这个游戏
        capture_interval: 截图间隔秒数
        action_delay: 操作间延迟秒数
        use_virtual_desktop: 是否使用虚拟桌面隔离

    Returns:
        会话信息
    """
    global _session, _game_loop, _vd_manager, _capturer

    # 如果已有会话，先停止
    if _game_loop and _game_loop.is_running:
        await stop_game()

    raw_config = _load_config()
    loop_cfg = raw_config.get("game_loop", {})

    config = GameConfig(
        ai_provider=ai_provider,
        ai_model=ai_model,
        capture_interval=capture_interval,
        action_delay=action_delay,
        change_threshold=loop_cfg.get("change_threshold", 0.02),
        screenshot_quality=loop_cfg.get("screenshot_quality", 75),
        screenshot_max_width=loop_cfg.get("screenshot_max_width", 1280),
        virtual_desktop_enabled=use_virtual_desktop,
        strategy_prompt=strategy_prompt,
    )

    # 创建截图器并查找窗口
    _capturer = WindowCapturer(
        quality=config.screenshot_quality,
        max_width=config.screenshot_max_width,
        change_threshold=config.change_threshold,
    )
    hwnd = _capturer.find_window(window_title)
    if hwnd is None:
        return {"error": f"未找到窗口: {window_title}，请用 list_windows 查看可用窗口"}

    # 创建会话
    _session = GameSession(
        window_title=window_title,
        hwnd=hwnd,
        ai_provider=ai_provider,
        strategy_prompt=strategy_prompt,
    )

    # 虚拟桌面
    _vd_manager = VirtualDesktopManager()
    if use_virtual_desktop and _vd_manager.is_available:
        if _vd_manager.create_game_desktop():
            _vd_manager.move_window_to_game_desktop(hwnd)
        else:
            logger.warning("虚拟桌面创建失败，将使用当前桌面")

    # AI 引擎
    ai_engine = _create_ai_engine(ai_provider, ai_model, raw_config)
    if strategy_prompt:
        ai_engine.set_strategy(strategy_prompt)

    # 加载 skills
    skills = _load_skills(raw_config)
    if skills:
        ai_engine.set_skills(skills)
        logger.info(f"已加载 {len(skills)} 个技能: {[s['name'] for s in skills]}")

    # 输入控制器
    input_ctrl = InputController(
        vd_manager=_vd_manager, use_virtual_desktop=use_virtual_desktop
    )
    input_ctrl.set_target_window(hwnd)

    # 启动游戏循环
    _game_loop = GameLoop(
        session=_session,
        config=config,
        ai_engine=ai_engine,
        capturer=_capturer,
        input_ctrl=input_ctrl,
    )
    await _game_loop.start()

    return {
        "status": "running",
        "window_title": window_title,
        "hwnd": hwnd,
        "ai_provider": ai_provider,
        "virtual_desktop": _vd_manager.game_desktop_number
        if use_virtual_desktop
        else None,
    }


@mcp.tool()
async def stop_game() -> dict:
    """停止自动游玩"""
    global _session, _game_loop, _vd_manager, _capturer

    if _game_loop:
        await _game_loop.stop()

    if _vd_manager:
        _vd_manager.cleanup()

    result = {"status": "stopped"}
    if _session:
        result["total_decisions"] = _session.total_decisions
        result["total_actions"] = _session.total_actions

    _session = None
    _game_loop = None
    _vd_manager = None
    _capturer = None

    return result


@mcp.tool()
async def pause_game() -> dict:
    """暂停自动游玩"""
    if _game_loop and _game_loop.is_running:
        _game_loop.pause()
        return {"status": "paused"}
    return {"error": "没有正在运行的游戏会话"}


@mcp.tool()
async def resume_game() -> dict:
    """恢复自动游玩"""
    if _game_loop and _session and _session.status == SessionStatus.PAUSED:
        _game_loop.resume()
        return {"status": "running"}
    return {"error": "没有已暂停的游戏会话"}


@mcp.tool()
async def get_status() -> dict:
    """获取当前游戏会话状态"""
    if _session is None:
        return {"status": "idle", "message": "没有活跃的游戏会话"}

    return {
        "status": _session.status.value,
        "window_title": _session.window_title,
        "hwnd": _session.hwnd,
        "ai_provider": _session.ai_provider,
        "total_decisions": _session.total_decisions,
        "total_actions": _session.total_actions,
        "last_analysis": _session.last_analysis,
        "last_error": _session.last_error,
    }


@mcp.tool()
async def set_strategy(prompt: str) -> dict:
    """更新游戏策略提示词

    Args:
        prompt: 新的策略提示词，告诉 AI 应该怎么玩
    """
    if _game_loop is None:
        return {"error": "没有活跃的游戏会话"}

    _game_loop.ai_engine.set_strategy(prompt)
    if _session:
        _session.strategy_prompt = prompt
    return {"status": "ok", "strategy": prompt}


@mcp.tool()
async def screenshot() -> dict:
    """手动截取游戏窗口截图并返回分析

    Returns:
        截图的 base64 数据和窗口信息
    """
    if _session is None or _session.hwnd is None or _capturer is None:
        return {"error": "没有活跃的游戏会话"}

    img = _capturer.capture(_session.hwnd)
    if img is None:
        return {"error": "截图失败"}

    b64 = _capturer.image_to_base64(img)
    return {
        "image_base64": b64,
        "width": img.width,
        "height": img.height,
        "format": "jpeg",
    }


@mcp.tool()
async def execute_action(
    action_type: str,
    x: float | None = None,
    y: float | None = None,
    x2: float | None = None,
    y2: float | None = None,
    key: str | None = None,
    keys: list[str] | None = None,
    text: str | None = None,
    scroll_amount: int | None = None,
    duration: float | None = None,
) -> dict:
    """手动执行一个游戏操作

    Args:
        action_type: 操作类型 (click/right_click/double_click/key_press/key_combo/type_text/drag/scroll/wait)
        x: 鼠标 X 坐标 (0.0-1.0 归一化)
        y: 鼠标 Y 坐标 (0.0-1.0 归一化)
        x2: 拖拽终点 X
        y2: 拖拽终点 Y
        key: 按键名称
        keys: 组合键列表
        text: 输入文字
        scroll_amount: 滚轮量 (正上负下)
        duration: 持续时间/等待秒数
    """
    if _game_loop is None:
        return {"error": "没有活跃的游戏会话"}

    from .models import ActionType, GameAction

    try:
        action = GameAction(
            action=ActionType(action_type),
            x=x,
            y=y,
            x2=x2,
            y2=y2,
            key=key,
            keys=keys,
            text=text,
            scroll_amount=scroll_amount,
            duration=duration,
        )
        result = await _game_loop.input_ctrl.execute(action)
        return {"status": "ok" if result else "failed", "action": action_type}
    except ValueError as e:
        return {"error": f"无效的操作类型: {e}"}


def main() -> None:
    """启动 MCP Server"""
    # 从用户配置加载 API Key
    setup_file_logging()
    user_cfg = load_user_config()
    apply_api_keys(user_cfg)
    logger.info("MCP Server 启动，已加载用户配置")
    mcp.run()


if __name__ == "__main__":
    main()
