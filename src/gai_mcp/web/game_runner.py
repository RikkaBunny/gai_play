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
from ..local_analyzer import LocalAnalyzer
from ..memory import LongTermMemory, ShortTermMemory
from ..models import ActionType, GameConfig, GameSession, SessionStatus
from ..models_advanced import AdvancedConfig, ExperienceEntry
from ..reflection import ReflectionEngine
from ..skill_manager import SkillManager
from ..task_manager import TaskManager

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

        # --- 高级功能初始化 ---
        adv_raw = config.get("advanced", {})
        adv_cfg = AdvancedConfig(**adv_raw) if adv_raw else AdvancedConfig()
        any_advanced = (
            adv_cfg.task_inference_enabled
            or adv_cfg.reflection_enabled
            or adv_cfg.memory_enabled
            or adv_cfg.dynamic_skills_enabled
            or adv_cfg.layered_decision_enabled
        )

        task_mgr = None
        refl_engine = None
        stm = None
        ltm = None
        skill_mgr = None
        local_ana = None

        if any_advanced:
            engine.enable_advanced(True)

        if adv_cfg.task_inference_enabled:
            task_mgr = TaskManager()
        if adv_cfg.reflection_enabled:
            refl_engine = ReflectionEngine(
                diff_threshold=adv_cfg.reflection_diff_threshold,
                max_retries=adv_cfg.reflection_max_retries,
            )
        if adv_cfg.memory_enabled:
            stm = ShortTermMemory(capacity=adv_cfg.short_term_capacity)
            if adv_cfg.long_term_enabled:
                ltm = LongTermMemory(game_id=game_name)
        if adv_cfg.dynamic_skills_enabled:
            skill_mgr = SkillManager(game_id=game_name, max_dynamic_skills=adv_cfg.max_dynamic_skills)
            skill_mgr.set_static_skills(skills)
            engine.set_skills(skill_mgr.get_all_skills())
        if adv_cfg.layered_decision_enabled:
            local_ana = LocalAnalyzer(
                change_threshold=adv_cfg.local_cv_change_threshold,
                static_frame_patience=adv_cfg.static_frame_patience,
            )

        self.status = "running"
        self._task = asyncio.create_task(
            self._loop(
                capturer, engine, input_ctrl, hwnd, capture_interval,
                task_mgr=task_mgr, refl_engine=refl_engine,
                stm=stm, ltm=ltm, skill_mgr=skill_mgr, local_ana=local_ana,
            )
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
        # 高级功能组件
        task_mgr: Optional[TaskManager] = None,
        refl_engine: Optional[ReflectionEngine] = None,
        stm: Optional[ShortTermMemory] = None,
        ltm: Optional[LongTermMemory] = None,
        skill_mgr: Optional[SkillManager] = None,
        local_ana: Optional[LocalAnalyzer] = None,
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

                # 2. 分层决策检查 (Feature 5)
                if local_ana:
                    local_result = local_ana.analyze(img)
                    if not local_result.needs_llm and local_result.suggested_action:
                        decision = local_ana.create_local_decision(local_result)
                        logger.info(f"[Round {self.round_count}] [本地决策] {local_result.reason}")

                        rec.elapsed = 0.0
                        rec.analysis = decision.analysis
                        rec.confidence = decision.confidence
                        rec.actions = [{"action": a.action.value, "x": a.x, "y": a.y, "reason": a.reason or ""} for a in decision.actions]

                        if decision.actions and decision.confidence > 0.1:
                            executed = await input_ctrl.execute_actions(decision.actions, delay=0.5)
                            rec.executed = executed
                            self.total_actions += executed

                        if stm:
                            stm.add_frame(decision.analysis, [a.action.value for a in decision.actions], confidence=decision.confidence)

                        self.decisions.append(rec)
                        await asyncio.sleep(interval)
                        continue

                # 3. 注入高级上下文
                if task_mgr:
                    engine.set_task_context(task_mgr.get_context_prompt())
                if refl_engine:
                    engine.set_reflection_context(refl_engine.get_reflection_context())
                if stm:
                    ctx = stm.get_context_prompt()
                    if stm.detect_action_loop():
                        ctx += "\n⚠️ 检测到操作循环！请尝试完全不同的策略。"
                    engine.set_memory_context(ctx)
                if ltm and self.decisions:
                    last_analysis = ""
                    for prev in reversed(list(self.decisions)):
                        if prev.analysis:
                            last_analysis = prev.analysis
                            break
                    if last_analysis:
                        engine.set_experience_context(ltm.get_relevant_context(last_analysis))

                # 4. AI 分析
                screenshot_b64 = capturer.image_to_base64(img)
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
                if decision.current_task:
                    logger.info(f"[Round {self.round_count}] 当前任务: {decision.current_task}")
                for i, a in enumerate(decision.actions):
                    logger.info(
                        f"[Round {self.round_count}] 操作 {i+1}: "
                        f"{a.action.value} x={a.x} y={a.y} | {a.reason}"
                    )

                # 5. 更新任务状态 (Feature 1)
                if task_mgr:
                    task_mgr.update_from_decision(decision)

                # 6. 执行操作
                before_img = img
                executed = 0
                if decision.actions and decision.confidence > 0.1:
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

                # 7. 自我反思 (Feature 2)
                action_succeeded = None
                if refl_engine and executed > 0:
                    await asyncio.sleep(0.3)
                    after_img = capturer.capture(hwnd)
                    if after_img:
                        reflection = refl_engine.reflect(before_img, after_img, decision.actions)
                        action_succeeded = reflection.action_succeeded
                        if not reflection.action_succeeded:
                            logger.warning(f"[Round {self.round_count}] 反思: 操作可能失败")

                # 8. 更新记忆 (Feature 3)
                if stm:
                    task_name = task_mgr.state.current_task if task_mgr else ""
                    stm.add_frame(
                        analysis=decision.analysis,
                        actions_taken=[a.action.value for a in decision.actions],
                        task=task_name,
                        confidence=decision.confidence,
                        action_succeeded=action_succeeded,
                    )

                if ltm and decision.new_experience:
                    ltm.add_experience(ExperienceEntry(
                        game_id=ltm.game_id,
                        situation=decision.analysis[:100],
                        action_taken=", ".join(a.action.value for a in decision.actions),
                        outcome="成功" if action_succeeded else "待验证",
                        lesson=decision.new_experience,
                    ))

                # 9. 动态技能生成 (Feature 4)
                if skill_mgr and decision.new_skill:
                    new_skill = skill_mgr.add_skill(decision.new_skill)
                    if new_skill:
                        logger.info(f"[Round {self.round_count}] AI 生成新技能: {new_skill.name}")
                        engine.set_skills(skill_mgr.get_all_skills())

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
