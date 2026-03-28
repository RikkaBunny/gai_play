"""游戏主循环引擎 - 截屏→AI分析→操作的核心循环"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .ai_engine.base import AIEngine
from .capturer import WindowCapturer
from .input_controller import InputController
from .models import GameConfig, GameSession, SessionStatus

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
        """核心循环"""
        while not self._stop_flag:
            try:
                # 等待暂停恢复
                await self._pause_event.wait()
                if self._stop_flag:
                    break

                # 1. 截图
                img = self.capturer.capture(
                    self.session.hwnd, roi=self.config.roi
                )
                if img is None:
                    logger.warning("截图失败，跳过本轮")
                    await asyncio.sleep(self.config.capture_interval)
                    continue

                # 2. AI 分析
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

                self.session.last_analysis = decision.analysis
                self.session.total_decisions += 1

                # 4. 执行操作
                if decision.actions and decision.confidence > 0.1:
                    success = await self.input_ctrl.execute_actions(
                        decision.actions, delay=self.config.action_delay
                    )
                    self.session.total_actions += success
                    logger.info(f"执行了 {success}/{len(decision.actions)} 个操作")

                # 5. 等待下一轮
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
