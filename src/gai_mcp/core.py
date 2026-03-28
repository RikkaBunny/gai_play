"""核心业务逻辑 - MCP Server 和独立 CLI 共用"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .ai_engine import ClaudeEngine, LocalEngine, OpenAIEngine
from .ai_engine.base import AIEngine
from .capturer import WindowCapturer
from .game_loop import GameLoop
from .input_controller import InputController
from .local_analyzer import LocalAnalyzer
from .memory import LongTermMemory, ShortTermMemory
from .models import ActionType, GameAction, GameConfig, GameSession, SessionStatus
from .models_advanced import AdvancedConfig
from .config_manager import apply_api_keys, load_config, setup_file_logging
from .reflection import ReflectionEngine
from .skill_manager import SkillManager
from .task_manager import TaskManager
from .virtual_desktop import VirtualDesktopManager

logger = logging.getLogger(__name__)


class GameController:
    """游戏控制器 - 管理游戏会话的核心类"""

    def __init__(self) -> None:
        self.session: Optional[GameSession] = None
        self.game_loop: Optional[GameLoop] = None
        self.vd_manager: Optional[VirtualDesktopManager] = None
        self.capturer: Optional[WindowCapturer] = None
        self._config: dict = {}

    def load_config(self) -> dict:
        """加载配置"""
        self._config = load_config()
        apply_api_keys(self._config)
        return self._config

    @staticmethod
    def list_windows() -> list[dict]:
        """列出所有可见窗口"""
        capturer = WindowCapturer()
        return capturer.list_windows()

    async def start_game(
        self,
        window_title: str,
        ai_provider: str = "local",
        ai_model: str | None = None,
        strategy_prompt: str = "",
        capture_interval: float = 2.0,
        action_delay: float = 0.5,
        use_virtual_desktop: bool = True,
    ) -> dict:
        """开始自动游玩"""
        # 如果已有会话，先停止
        if self.game_loop and self.game_loop.is_running:
            await self.stop_game()

        raw_config = self._config or self.load_config()
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
        self.capturer = WindowCapturer(
            quality=config.screenshot_quality,
            max_width=config.screenshot_max_width,
            change_threshold=config.change_threshold,
        )
        hwnd = self.capturer.find_window(window_title)
        if hwnd is None:
            return {"error": f"未找到窗口: {window_title}"}

        # 创建会话
        self.session = GameSession(
            window_title=window_title,
            hwnd=hwnd,
            ai_provider=ai_provider,
            strategy_prompt=strategy_prompt,
        )

        # 虚拟桌面
        self.vd_manager = VirtualDesktopManager()
        if use_virtual_desktop and self.vd_manager.is_available:
            if self.vd_manager.create_game_desktop():
                self.vd_manager.move_window_to_game_desktop(hwnd)
            else:
                logger.warning("虚拟桌面创建失败，将使用当前桌面")

        # AI 引擎
        ai_engine = create_ai_engine(ai_provider, ai_model, raw_config)
        if strategy_prompt:
            ai_engine.set_strategy(strategy_prompt)

        # 加载 skills
        skills = load_skills(raw_config)

        # --- 高级功能初始化 ---
        adv_cfg = AdvancedConfig(**(config.advanced or {}))
        any_advanced = (
            adv_cfg.task_inference_enabled
            or adv_cfg.reflection_enabled
            or adv_cfg.memory_enabled
            or adv_cfg.dynamic_skills_enabled
            or adv_cfg.layered_decision_enabled
        )

        task_manager = None
        reflection_engine = None
        short_term_memory = None
        long_term_memory = None
        skill_manager = None
        local_analyzer = None
        game_id = window_title

        if any_advanced:
            ai_engine.enable_advanced(True)

        # Feature 1: 任务推断
        if adv_cfg.task_inference_enabled:
            task_manager = TaskManager()
            logger.info("高级功能: 任务推断已启用")

        # Feature 2: 自我反思
        if adv_cfg.reflection_enabled:
            reflection_engine = ReflectionEngine(
                diff_threshold=adv_cfg.reflection_diff_threshold,
                max_retries=adv_cfg.reflection_max_retries,
            )
            logger.info("高级功能: 自我反思已启用")

        # Feature 3: 记忆系统
        if adv_cfg.memory_enabled:
            short_term_memory = ShortTermMemory(
                capacity=adv_cfg.short_term_capacity
            )
            logger.info("高级功能: 短期记忆已启用")
            if adv_cfg.long_term_enabled:
                long_term_memory = LongTermMemory(game_id=game_id)
                logger.info(
                    f"高级功能: 长期记忆已启用 "
                    f"({long_term_memory.experience_count} 条历史经验)"
                )

        # Feature 4: 动态技能
        if adv_cfg.dynamic_skills_enabled:
            skill_manager = SkillManager(
                game_id=game_id,
                max_dynamic_skills=adv_cfg.max_dynamic_skills,
            )
            skill_manager.set_static_skills(skills)
            ai_engine.set_skills(skill_manager.get_all_skills())
            logger.info(
                f"高级功能: 动态技能已启用 "
                f"(静态={len(skills)}, 动态={skill_manager.dynamic_count})"
            )
        elif skills:
            ai_engine.set_skills(skills)
            logger.info(f"已加载 {len(skills)} 个技能: {[s['name'] for s in skills]}")

        # Feature 5: 分层决策
        if adv_cfg.layered_decision_enabled:
            local_analyzer = LocalAnalyzer(
                change_threshold=adv_cfg.local_cv_change_threshold,
                static_frame_patience=adv_cfg.static_frame_patience,
            )
            logger.info("高级功能: 分层决策已启用")

        # 输入控制器 (传入 capturer 以获取截图坐标元信息)
        input_ctrl = InputController(
            vd_manager=self.vd_manager,
            use_virtual_desktop=use_virtual_desktop,
            capturer=self.capturer,
        )
        input_ctrl.set_target_window(hwnd)

        # 启动游戏循环
        self.game_loop = GameLoop(
            session=self.session,
            config=config,
            ai_engine=ai_engine,
            capturer=self.capturer,
            input_ctrl=input_ctrl,
            task_manager=task_manager,
            reflection_engine=reflection_engine,
            short_term_memory=short_term_memory,
            long_term_memory=long_term_memory,
            skill_manager=skill_manager,
            local_analyzer=local_analyzer,
        )
        await self.game_loop.start()

        return {
            "status": "running",
            "window_title": window_title,
            "hwnd": hwnd,
            "ai_provider": ai_provider,
            "virtual_desktop": self.vd_manager.game_desktop_number
            if use_virtual_desktop
            else None,
        }

    async def stop_game(self) -> dict:
        """停止自动游玩"""
        if self.game_loop:
            await self.game_loop.stop()

        if self.vd_manager:
            self.vd_manager.cleanup()

        result = {"status": "stopped"}
        if self.session:
            result["total_decisions"] = self.session.total_decisions
            result["total_actions"] = self.session.total_actions

        self.session = None
        self.game_loop = None
        self.vd_manager = None
        self.capturer = None

        return result

    def pause_game(self) -> dict:
        """暂停自动游玩"""
        if self.game_loop and self.game_loop.is_running:
            self.game_loop.pause()
            return {"status": "paused"}
        return {"error": "没有正在运行的游戏会话"}

    def resume_game(self) -> dict:
        """恢复自动游玩"""
        if self.game_loop and self.session and self.session.status == SessionStatus.PAUSED:
            self.game_loop.resume()
            return {"status": "running"}
        return {"error": "没有已暂停的游戏会话"}

    def get_status(self) -> dict:
        """获取当前状态"""
        if self.session is None:
            return {"status": "idle", "message": "没有活跃的游戏会话"}

        return {
            "status": self.session.status.value,
            "window_title": self.session.window_title,
            "hwnd": self.session.hwnd,
            "ai_provider": self.session.ai_provider,
            "total_decisions": self.session.total_decisions,
            "total_actions": self.session.total_actions,
            "last_analysis": self.session.last_analysis,
            "last_error": self.session.last_error,
        }

    def set_strategy(self, prompt: str) -> dict:
        """更新策略提示词"""
        if self.game_loop is None:
            return {"error": "没有活跃的游戏会话"}

        self.game_loop.ai_engine.set_strategy(prompt)
        if self.session:
            self.session.strategy_prompt = prompt
        return {"status": "ok", "strategy": prompt}

    def take_screenshot(self) -> dict:
        """手动截图"""
        if self.session is None or self.session.hwnd is None or self.capturer is None:
            return {"error": "没有活跃的游戏会话"}

        img = self.capturer.capture(self.session.hwnd)
        if img is None:
            return {"error": "截图失败"}

        b64 = self.capturer.image_to_base64(img)
        return {
            "image_base64": b64,
            "width": img.width,
            "height": img.height,
            "format": "jpeg",
        }

    async def execute_action(
        self,
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
        """手动执行操作"""
        if self.game_loop is None:
            return {"error": "没有活跃的游戏会话"}

        try:
            action = GameAction(
                action=ActionType(action_type),
                x=x, y=y, x2=x2, y2=y2,
                key=key, keys=keys, text=text,
                scroll_amount=scroll_amount, duration=duration,
            )
            result = await self.game_loop.input_ctrl.execute(action)
            return {"status": "ok" if result else "failed", "action": action_type}
        except ValueError as e:
            return {"error": f"无效的操作类型: {e}"}


def create_ai_engine(
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


def load_skills(raw_config: dict) -> list[dict]:
    """加载技能文件"""
    import sys
    candidates = [
        Path(__file__).parent.parent.parent / "skills",
        Path(__file__).parent / "skills",
    ]
    # PyInstaller 打包后
    if getattr(sys, 'frozen', False):
        candidates.insert(0, Path(sys._MEIPASS) / "skills")
        candidates.insert(1, Path(sys.executable).parent / "skills")
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
