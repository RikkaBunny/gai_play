"""游戏运行管理器 - 在 Web 后台中控制游戏循环并记录决策链"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import subprocess
import time
from collections import deque
from typing import Any, Optional

from ..ai_engine.base import AIEngine
from ..ai_engine.openai import OpenAIEngine
from ..ai_engine.claude import ClaudeEngine
from ..ai_engine.local import LocalEngine
from ..capturer import WindowCapturer
from ..config_manager import apply_api_keys, load_config
from ..input_controller import InputController
from ..models import ActionType, GameConfig, GameSession, SessionStatus

logger = logging.getLogger(__name__)

# 决策记录最大条数
MAX_DECISIONS = 50


class DecisionRecord:
    """一次决策的完整记录"""

    def __init__(self) -> None:
        self.round_id: int = 0
        self.timestamp: str = ""
        self.screenshot_b64: str = ""  # 缩略图
        self.analysis: str = ""
        self.confidence: float = 0.0
        self.actions: list[dict] = []
        self.executed: int = 0
        self.elapsed: float = 0.0
        self.error: str = ""
        self.skipped: str = ""  # 跳过原因

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "round": self.round_id,
            "time": self.timestamp,
            "analysis": self.analysis,
            "confidence": self.confidence,
            "actions": self.actions,
            "executed": self.executed,
            "elapsed_s": round(self.elapsed, 1),
        }
        if self.screenshot_b64:
            d["screenshot"] = self.screenshot_b64
        if self.error:
            d["error"] = self.error
        if self.skipped:
            d["skipped"] = self.skipped
        return d


class GameRunner:
    """单例游戏运行管理器"""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_flag = False
        self._pause_flag = False
        self.status: str = "idle"  # idle / running / paused / error
        self.game_name: str = ""
        self.window_title: str = ""
        self.round_count: int = 0
        self.total_actions: int = 0
        self.decisions: deque[DecisionRecord] = deque(maxlen=MAX_DECISIONS)
        self._hwnd: Optional[int] = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def get_status(self) -> dict:
        return {
            "status": self.status,
            "game_name": self.game_name,
            "window_title": self.window_title,
            "round_count": self.round_count,
            "total_actions": self.total_actions,
        }

    def get_decisions(self, limit: int = 20) -> list[dict]:
        items = list(self.decisions)[-limit:]
        return [d.to_dict() for d in items]

    async def start(self, game_name: str) -> dict:
        """启动游戏"""
        if self.is_running:
            return {"error": "已有游戏在运行，请先停止"}

        config = load_config()
        apply_api_keys(config)
        games = config.get("games", {})
        game = games.get(game_name)
        if not game:
            return {"error": f"未找到游戏配置: {game_name}"}

        self.game_name = game_name
        self.window_title = game.get("window_title", "")
        self.round_count = 0
        self.total_actions = 0
        self.decisions.clear()
        self._stop_flag = False
        self._pause_flag = False

        # 查找窗口（先检查是否已在运行）
        capturer = WindowCapturer(
            quality=config.get("game_loop", {}).get("screenshot_quality", 75),
            max_width=config.get("game_loop", {}).get("screenshot_max_width", 1280),
            change_threshold=config.get("game_loop", {}).get("change_threshold", 0.02),
        )
        hwnd = capturer.find_window(self.window_title)

        # 窗口未找到时尝试启动游戏进程
        if not hwnd:
            game_path = game.get("path", "")
            if game_path and os.path.exists(game_path):
                logger.info(f"启动游戏: {game_path}")
                try:
                    subprocess.Popen(
                        [game_path],
                        cwd=os.path.dirname(game_path),
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    )
                    # 等待窗口出现
                    for _ in range(10):
                        await asyncio.sleep(1)
                        hwnd = capturer.find_window(self.window_title)
                        if hwnd:
                            break
                except Exception as e:
                    logger.error(f"启动游戏失败: {e}")

        if not hwnd:
            self.status = "error"
            return {"error": f"未找到窗口: {self.window_title}"}
        self._hwnd = hwnd

        # 创建 AI 引擎
        ai_provider = game.get("ai_provider", config.get("ai", {}).get("provider", "openai"))
        api_keys = config.get("api_keys", {})
        ai_cfg = config.get("ai", {})
        engine = self._create_engine(ai_provider, ai_cfg, api_keys)

        strategy = game.get("strategy", "")
        if strategy:
            engine.set_strategy(strategy)

        # 加载 skill 文件
        skills = self._load_skills(game.get("skills", []))
        if skills:
            engine.set_skills(skills)
            logger.info(f"已加载 {len(skills)} 个技能: {[s['name'] for s in skills]}")

        # 输入控制器
        input_ctrl = InputController(use_virtual_desktop=False)
        input_ctrl.set_target_window(hwnd)

        capture_interval = game.get("capture_interval", 2.0)

        self.status = "running"
        self._task = asyncio.create_task(
            self._loop(capturer, engine, input_ctrl, hwnd, capture_interval)
        )
        logger.info(f"游戏循环已启动: {game_name} (hwnd={hwnd})")
        return {"status": "running", "hwnd": hwnd, "game": game_name}

    async def stop(self) -> dict:
        self._stop_flag = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.status = "idle"
        logger.info("游戏循环已停止")
        return {"status": "stopped", "rounds": self.round_count, "actions": self.total_actions}

    def pause(self) -> dict:
        if self.status == "running":
            self._pause_flag = True
            self.status = "paused"
            return {"status": "paused"}
        return {"error": "游戏未在运行"}

    def resume(self) -> dict:
        if self.status == "paused":
            self._pause_flag = False
            self.status = "running"
            return {"status": "running"}
        return {"error": "游戏未在暂停"}

    def _create_engine(self, provider: str, ai_cfg: dict, api_keys: dict) -> AIEngine:
        # 确保 API key 在环境变量中
        if api_keys.get("anthropic"):
            os.environ["ANTHROPIC_API_KEY"] = api_keys["anthropic"]
        if api_keys.get("openai"):
            os.environ["OPENAI_API_KEY"] = api_keys["openai"]

        match provider:
            case "claude":
                return ClaudeEngine(
                    model=ai_cfg.get("claude_model"),
                    base_url=api_keys.get("claude_base_url") or None,
                )
            case "openai":
                return OpenAIEngine(
                    model=ai_cfg.get("openai_model"),
                    base_url=api_keys.get("openai_base_url") or None,
                )
            case "local":
                return LocalEngine(
                    model=ai_cfg.get("local_model"),
                    base_url=api_keys.get("local_base_url", "http://localhost:11434"),
                )
            case _:
                return OpenAIEngine(
                    model=ai_cfg.get("openai_model"),
                    base_url=api_keys.get("openai_base_url") or None,
                )

    def _load_skills(self, skill_files: list[str]) -> list[dict]:
        """从 skills 目录加载技能文件"""
        from pathlib import Path

        # 尝试多个可能的 skills 目录位置
        candidates = [
            Path(__file__).parent.parent.parent.parent / "skills",  # 开发环境
            Path(__file__).parent.parent / "skills",                 # 打包后
        ]
        skills_dir = None
        for p in candidates:
            if p.is_dir():
                skills_dir = p
                break
        if skills_dir is None:
            logger.warning("未找到 skills 目录")
            return []
        loaded = []
        for filename in skill_files:
            filepath = skills_dir / filename
            if filepath.exists():
                try:
                    content = filepath.read_text(encoding="utf-8")
                    # 从文件第一行提取标题作为 name
                    lines = content.strip().split("\n")
                    name = lines[0].lstrip("# ").strip() if lines else filename
                    loaded.append({
                        "name": name,
                        "content": content,
                    })
                    logger.info(f"已加载技能文件: {filename} -> {name}")
                except Exception as e:
                    logger.error(f"加载技能文件失败 {filename}: {e}")
            else:
                logger.warning(f"技能文件不存在: {filepath}")
        return loaded

    def _make_thumbnail(self, img) -> str:
        """生成小缩略图 base64，用于前端展示"""
        from PIL import Image
        thumb = img.copy()
        thumb.thumbnail((320, 180), Image.LANCZOS)
        buf = io.BytesIO()
        thumb.save(buf, format="JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    async def _loop(
        self,
        capturer: WindowCapturer,
        engine: AIEngine,
        input_ctrl: InputController,
        hwnd: int,
        interval: float,
    ) -> None:
        try:
            import win32gui, win32con
        except ImportError:
            logger.error("缺少 pywin32，无法运行游戏循环")
            self.status = "error"
            return

        capturer.reset()

        while not self._stop_flag:
            try:
                # 暂停检查
                if self._pause_flag:
                    await asyncio.sleep(0.5)
                    continue

                self.round_count += 1
                rec = DecisionRecord()
                rec.round_id = self.round_count
                rec.timestamp = time.strftime("%H:%M:%S")

                logger.info(f"{'='*60}")
                logger.info(f"[Round {self.round_count}] 开始新一轮决策")

                # 1. 截图
                img = capturer.capture(hwnd)
                if img is None:
                    rec.skipped = "截图失败"
                    rec.error = "截图失败"
                    logger.warning(f"[Round {self.round_count}] 截图失败，跳过")
                    self.decisions.append(rec)
                    await asyncio.sleep(interval)
                    continue

                rec.screenshot_b64 = self._make_thumbnail(img)
                logger.info(f"[Round {self.round_count}] 截图完成: {img.width}x{img.height}")

                # 2. 每轮都交给 AI 分析，AI 自行判断是否需要操作
                screenshot_b64 = capturer.image_to_base64(img)
                # 构建上下文：最近一次有效分析
                context = ""
                for prev in reversed(list(self.decisions)):
                    if prev.analysis:
                        context = f"上一次分析: {prev.analysis}"
                        break

                logger.info(f"[Round {self.round_count}] 发送截图给 AI 分析...")
                t0 = time.monotonic()
                decision = await engine.analyze(screenshot_b64, context)
                elapsed = time.monotonic() - t0

                rec.elapsed = elapsed
                rec.analysis = decision.analysis
                rec.confidence = decision.confidence
                rec.actions = [
                    {
                        "action": a.action.value,
                        "x": a.x,
                        "y": a.y,
                        "reason": a.reason or "",
                        "key": a.key,
                        "text": a.text,
                    }
                    for a in decision.actions
                ]

                logger.info(f"[Round {self.round_count}] AI 分析完成 ({elapsed:.1f}s)")
                logger.info(f"[Round {self.round_count}] 分析结果: {decision.analysis}")
                logger.info(f"[Round {self.round_count}] 置信度: {decision.confidence:.0%}")
                for i, a in enumerate(decision.actions):
                    logger.info(
                        f"[Round {self.round_count}] 操作 {i+1}: "
                        f"{a.action.value} x={a.x} y={a.y} | {a.reason}"
                    )

                # 4. 执行操作
                if decision.actions and decision.confidence > 0.1:
                    # 后台消息模式，不需要激活窗口
                    executed = await input_ctrl.execute_actions(
                        decision.actions, delay=0.5
                    )
                    rec.executed = executed
                    self.total_actions += executed
                    logger.info(
                        f"[Round {self.round_count}] 执行完成: "
                        f"{executed}/{len(decision.actions)} 个操作"
                    )
                else:
                    logger.info(f"[Round {self.round_count}] 置信度过低或无操作，跳过执行")

                self.decisions.append(rec)
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Round {self.round_count}] 异常: {e}", exc_info=True)
                rec = DecisionRecord()
                rec.round_id = self.round_count
                rec.timestamp = time.strftime("%H:%M:%S")
                rec.error = str(e)
                self.decisions.append(rec)
                self.status = "error"
                await asyncio.sleep(interval * 3)
                self.status = "running"


# 全局单例
runner = GameRunner()
