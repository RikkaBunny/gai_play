"""任务推断与子目标管理 - Feature 1

让 AI 不再每帧独立思考，而是维护一个 "当前在做什么" 的状态，
类似 Cradle 的 Task Inference 模块。
"""

from __future__ import annotations

import logging
from typing import Optional

from .models import AIDecision
from .models_advanced import SubGoal, SubGoalStatus, TaskState

logger = logging.getLogger(__name__)

# 任务切换时保留的历史数
MAX_TASK_HISTORY = 20


class TaskManager:
    """任务状态管理器

    维护当前任务、子目标列表，并在每次 AI 决策后更新状态。
    将任务上下文注入 AI 提示词，让 AI 知道自己 "处于哪个阶段"。
    """

    def __init__(self) -> None:
        self.state = TaskState()
        self._stuck_threshold = 10  # 连续相同任务超过此数视为卡住

    def update_from_decision(self, decision: AIDecision) -> None:
        """根据 AI 决策更新任务状态

        AI 在返回的 JSON 中可以包含:
        - current_task: 当前认为自己在做什么
        - sub_goals: 子目标列表（可选）
        """
        new_task = getattr(decision, "current_task", None) or ""

        if not new_task:
            return

        # 任务切换检测
        if new_task != self.state.current_task:
            if self.state.current_task:
                self.state.task_history.append(self.state.current_task)
                # 裁剪历史
                if len(self.state.task_history) > MAX_TASK_HISTORY:
                    self.state.task_history = self.state.task_history[-MAX_TASK_HISTORY:]
                logger.info(
                    f"任务切换: '{self.state.current_task}' → '{new_task}'"
                )
            self.state.current_task = new_task
            self.state.consecutive_same_task = 1
        else:
            self.state.consecutive_same_task += 1

        # 更新子目标（如果 AI 提供了）
        new_sub_goals = getattr(decision, "sub_goals", None)
        if new_sub_goals and isinstance(new_sub_goals, list):
            parsed = []
            for sg in new_sub_goals:
                try:
                    if isinstance(sg, dict):
                        # 安全解析 status 枚举
                        try:
                            status = SubGoalStatus(str(sg.get("status", "pending")))
                        except ValueError:
                            status = SubGoalStatus.PENDING
                        parsed.append(SubGoal(
                            name=str(sg.get("name", "")),
                            description=str(sg.get("description", "")),
                            status=status,
                        ))
                    else:
                        parsed.append(SubGoal(name=str(sg)))
                except Exception as e:
                    logger.warning(f"跳过异常 sub_goal: {sg} ({e})")
            self.state.sub_goals = parsed

    @property
    def is_stuck(self) -> bool:
        """检测 AI 是否卡住（连续很多帧做同一件事）"""
        return self.state.consecutive_same_task > self._stuck_threshold

    def get_context_prompt(self) -> str:
        """生成任务状态上下文，注入 AI 提示词"""
        parts = []

        if self.state.current_task:
            parts.append(f"当前任务: {self.state.current_task}")
            parts.append(f"已持续 {self.state.consecutive_same_task} 轮")

        if self.state.sub_goals:
            goals_text = []
            for i, sg in enumerate(self.state.sub_goals):
                status_icon = {
                    SubGoalStatus.PENDING: "⬜",
                    SubGoalStatus.ACTIVE: "🔵",
                    SubGoalStatus.COMPLETED: "✅",
                    SubGoalStatus.FAILED: "❌",
                }.get(sg.status, "⬜")
                goals_text.append(f"  {status_icon} {sg.name}")
            parts.append("子目标:\n" + "\n".join(goals_text))

        if self.state.task_history:
            recent = self.state.task_history[-5:]
            parts.append("最近完成的任务: " + " → ".join(recent))

        if self.is_stuck:
            parts.append(
                f"⚠️ 注意: 你已经在 '{self.state.current_task}' 上卡了 "
                f"{self.state.consecutive_same_task} 轮，请考虑换一种策略或推进到下一步。"
            )

        return "\n".join(parts) if parts else ""

    def reset(self) -> None:
        """重置任务状态"""
        self.state = TaskState()
