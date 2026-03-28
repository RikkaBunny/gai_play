"""游戏主循环引擎 - 截屏→AI分析→操作的核心循环

集成 5 大高级功能:
1. 任务推断 + 子目标管理 (TaskManager)
2. 自我反思 (ReflectionEngine)
3. 短期记忆 + 长期经验 (ShortTermMemory, LongTermMemory)
4. 动态技能生成 (SkillManager)
5. 分层决策 (LocalAnalyzer)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .ai_engine.base import AIEngine
from .capturer import WindowCapturer
from .input_controller import InputController
from .local_analyzer import LocalAnalyzer
from .memory import LongTermMemory, ShortTermMemory
from .models import GameConfig, GameSession, SessionStatus
from .models_advanced import AdvancedConfig, ExperienceEntry
from .reflection import ReflectionEngine
from .skill_manager import SkillManager
from .task_manager import TaskManager

logger = logging.getLogger(__name__)


class GameLoop:
    """游戏自动游玩主循环"""

    def __init__(
        self,
        session: GameSession,
        config: GameConfig,
        ai_engine: AIEngine,
        capturer: WindowCapturer,
        input_ctrl: InputController,
        # --- 高级功能组件 (均可选) ---
        task_manager: Optional[TaskManager] = None,
        reflection_engine: Optional[ReflectionEngine] = None,
        short_term_memory: Optional[ShortTermMemory] = None,
        long_term_memory: Optional[LongTermMemory] = None,
        skill_manager: Optional[SkillManager] = None,
        local_analyzer: Optional[LocalAnalyzer] = None,
    ) -> None:
        self.session = session
        self.config = config
        self.ai_engine = ai_engine
        self.capturer = capturer
        self.input_ctrl = input_ctrl
        self._task: Optional[asyncio.Task] = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # 初始为非暂停状态
        self._stop_flag = False

        # 高级功能组件
        self.task_manager = task_manager
        self.reflection_engine = reflection_engine
        self.short_term_memory = short_term_memory
        self.long_term_memory = long_term_memory
        self.skill_manager = skill_manager
        self.local_analyzer = local_analyzer

        # 解析高级配置
        self._adv = AdvancedConfig(**(config.advanced or {})) if config.advanced else AdvancedConfig()

    async def start(self) -> None:
        """启动游戏循环"""
        if self.session.hwnd is None:
            raise RuntimeError("未设置游戏窗口句柄")

        self._stop_flag = False
        self._pause_event.set()
        self.session.status = SessionStatus.RUNNING
        self.capturer.reset()

        self._task = asyncio.create_task(self._loop())
        logger.info(f"游戏循环已启动: {self.session.window_title}")

        # 日志输出已启用的高级功能
        features = []
        if self.task_manager:
            features.append("任务推断")
        if self.reflection_engine:
            features.append("自我反思")
        if self.short_term_memory:
            features.append("短期记忆")
        if self.long_term_memory:
            features.append("长期记忆")
        if self.skill_manager:
            features.append("动态技能")
        if self.local_analyzer:
            features.append("分层决策")
        if features:
            logger.info(f"已启用高级功能: {', '.join(features)}")

    async def stop(self) -> None:
        """停止游戏循环"""
        self._stop_flag = True
        self._pause_event.set()  # 解除暂停以便退出

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # 输出分层决策统计
        if self.local_analyzer:
            stats = self.local_analyzer.get_stats()
            logger.info(
                f"分层决策统计: 本地={stats['local_decisions']}, "
                f"LLM={stats['llm_deferred']}, "
                f"本地占比={stats['local_ratio']:.0%}"
            )

        self.session.status = SessionStatus.IDLE
        logger.info("游戏循环已停止")

    def pause(self) -> None:
        """暂停游戏循环"""
        self._pause_event.clear()
        self.session.status = SessionStatus.PAUSED
        logger.info("游戏循环已暂停")

    def resume(self) -> None:
        """恢复游戏循环"""
        self._pause_event.set()
        self.session.status = SessionStatus.RUNNING
        logger.info("游戏循环已恢复")

    @property
    def is_running(self) -> bool:
        return (
            self._task is not None
            and not self._task.done()
            and not self._stop_flag
        )

    async def _loop(self) -> None:
        """核心循环 — 集成全部高级功能"""
        while not self._stop_flag:
            try:
                # 等待暂停恢复
                await self._pause_event.wait()
                if self._stop_flag:
                    break

                # ==========================================
                # 1. 截图（操作前）
                # ==========================================
                img = self.capturer.capture(
                    self.session.hwnd, roi=self.config.roi
                )
                if img is None:
                    logger.warning("截图失败，跳过本轮")
                    await asyncio.sleep(self.config.capture_interval)
                    continue

                # ==========================================
                # 2. 分层决策检查 (Feature 5)
                # ==========================================
                if self.local_analyzer:
                    local_result = self.local_analyzer.analyze(img)
                    if not local_result.needs_llm and local_result.suggested_action:
                        # 本地决策，不调 LLM
                        decision = self.local_analyzer.create_local_decision(local_result)
                        logger.info(
                            f"[本地决策] {local_result.reason} → "
                            f"{local_result.suggested_action}"
                        )

                        # 执行本地决策
                        if decision.actions and decision.confidence > 0.1:
                            await self.input_ctrl.execute_actions(
                                decision.actions, delay=self.config.action_delay
                            )
                            self.session.total_actions += len(decision.actions)

                        self.session.total_decisions += 1
                        self.session.last_analysis = decision.analysis

                        # 记录到短期记忆
                        if self.short_term_memory:
                            self.short_term_memory.add_frame(
                                analysis=decision.analysis,
                                actions_taken=[a.action.value for a in decision.actions],
                                confidence=decision.confidence,
                            )

                        await asyncio.sleep(self.config.capture_interval)
                        continue

                # ==========================================
                # 3. 注入高级上下文到 AI 引擎
                # ==========================================
                self._inject_advanced_context()

                # ==========================================
                # 4. AI 分析
                # ==========================================
                screenshot_b64 = self.capturer.image_to_base64(img)
                context = ""
                if self.session.last_analysis:
                    context = f"上一次分析: {self.session.last_analysis}"

                t0 = time.monotonic()
                decision = await self.ai_engine.analyze(screenshot_b64, context)
                elapsed = time.monotonic() - t0

                logger.info(
                    f"AI 决策完成 ({elapsed:.1f}s): "
                    f"置信度={decision.confidence:.0%}, "
                    f"操作数={len(decision.actions)}"
                )
                if decision.current_task:
                    logger.info(f"当前任务: {decision.current_task}")

                self.session.last_analysis = decision.analysis
                self.session.total_decisions += 1

                # ==========================================
                # 5. 更新任务状态 (Feature 1)
                # ==========================================
                if self.task_manager:
                    self.task_manager.update_from_decision(decision)

                # ==========================================
                # 6. 执行操作
                # ==========================================
                before_img = img  # 保存操作前截图用于反思
                executed = 0
                if decision.actions and decision.confidence > 0.1:
                    executed = await self.input_ctrl.execute_actions(
                        decision.actions, delay=self.config.action_delay
                    )
                    self.session.total_actions += executed
                    logger.info(f"执行了 {executed}/{len(decision.actions)} 个操作")

                # ==========================================
                # 7. 自我反思 (Feature 2)
                # ==========================================
                action_succeeded = None
                if self.reflection_engine and executed > 0:
                    # 短暂等待让游戏响应
                    await asyncio.sleep(0.3)

                    # 操作后截图
                    after_img = self.capturer.capture(
                        self.session.hwnd, roi=self.config.roi
                    )
                    if after_img:
                        reflection = self.reflection_engine.reflect(
                            before_img, after_img, decision.actions
                        )
                        action_succeeded = reflection.action_succeeded

                        if not reflection.action_succeeded:
                            logger.warning(
                                f"反思: 操作可能失败 | {reflection.actual_change}"
                            )
                            if reflection.adjustment:
                                logger.info(f"调整建议: {reflection.adjustment}")

                # ==========================================
                # 8. 更新记忆 (Feature 3)
                # ==========================================
                if self.short_term_memory:
                    task_name = ""
                    if self.task_manager:
                        task_name = self.task_manager.state.current_task
                    self.short_term_memory.add_frame(
                        analysis=decision.analysis,
                        actions_taken=[a.action.value for a in decision.actions],
                        task=task_name,
                        confidence=decision.confidence,
                        action_succeeded=action_succeeded,
                    )

                    # 检测操作循环
                    if self.short_term_memory.detect_action_loop():
                        logger.warning("检测到操作循环，下一轮将提醒 AI 调整策略")

                # Feature 3: 长期记忆 — 存储 AI 提出的经验
                if self.long_term_memory and decision.new_experience:
                    self.long_term_memory.add_experience(ExperienceEntry(
                        game_id=self.long_term_memory.game_id,
                        situation=decision.analysis[:100],
                        action_taken=", ".join(a.action.value for a in decision.actions),
                        outcome="成功" if action_succeeded else "待验证",
                        lesson=decision.new_experience,
                    ))

                # ==========================================
                # 9. 动态技能生成 (Feature 4)
                # ==========================================
                if self.skill_manager and decision.new_skill:
                    new_skill = self.skill_manager.add_skill(decision.new_skill)
                    if new_skill:
                        logger.info(f"AI 生成了新技能: {new_skill.name}")
                        # 刷新 AI 引擎的技能列表
                        self.ai_engine.set_skills(self.skill_manager.get_all_skills())

                # ==========================================
                # 10. 等待下一轮
                # ==========================================
                await asyncio.sleep(self.config.capture_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"游戏循环异常: {e}", exc_info=True)
                self.session.status = SessionStatus.ERROR
                self.session.last_error = str(e)
                # 出错后等待更长时间再重试
                await asyncio.sleep(self.config.capture_interval * 3)
                self.session.status = SessionStatus.RUNNING

    def _inject_advanced_context(self) -> None:
        """将所有高级功能的上下文注入 AI 引擎"""
        # Feature 1: 任务状态
        if self.task_manager:
            self.ai_engine.set_task_context(
                self.task_manager.get_context_prompt()
            )

        # Feature 2: 反思反馈
        if self.reflection_engine:
            self.ai_engine.set_reflection_context(
                self.reflection_engine.get_reflection_context()
            )

        # Feature 3: 短期记忆
        if self.short_term_memory:
            ctx = self.short_term_memory.get_context_prompt()
            # 如果检测到操作循环，追加警告
            if self.short_term_memory.detect_action_loop():
                ctx += "\n⚠️ 检测到操作循环！你最近的操作高度重复，请尝试完全不同的策略。"
            self.ai_engine.set_memory_context(ctx)

        # Feature 3: 长期经验
        if self.long_term_memory and self.session.last_analysis:
            self.ai_engine.set_experience_context(
                self.long_term_memory.get_relevant_context(
                    self.session.last_analysis
                )
            )
