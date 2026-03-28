"""高级功能数据模型 - 任务推断、自我反思、记忆、动态技能、分层决策"""

from __future__ import annotations

import enum
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# Feature 1: 任务推断 + 子目标管理
# ============================================================

class SubGoalStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class SubGoal(BaseModel):
    """子目标"""
    name: str
    description: str = ""
    status: SubGoalStatus = SubGoalStatus.PENDING


class TaskState(BaseModel):
    """当前任务状态"""
    current_task: str = ""
    sub_goals: list[SubGoal] = Field(default_factory=list)
    active_sub_goal_index: int = 0
    task_history: list[str] = Field(default_factory=list)
    consecutive_same_task: int = 0  # 连续相同任务计数，用于检测卡住


# ============================================================
# Feature 2: 自我反思
# ============================================================

class ReflectionResult(BaseModel):
    """操作反思结果"""
    action_succeeded: bool = True
    pixel_diff_ratio: float = 0.0
    expected_change: str = ""
    actual_change: str = ""
    adjustment: str = ""  # 如果失败，建议的调整
    should_retry: bool = False


# ============================================================
# Feature 3: 记忆系统
# ============================================================

class FrameMemory(BaseModel):
    """单帧记忆（短期记忆用）"""
    frame_index: int
    timestamp: float
    analysis: str
    actions_taken: list[str] = Field(default_factory=list)
    task_at_frame: str = ""
    confidence: float = 0.5
    action_succeeded: Optional[bool] = None


class ExperienceEntry(BaseModel):
    """长期经验条目"""
    game_id: str
    situation: str  # 遇到的场景描述
    action_taken: str  # 采取的操作
    outcome: str  # 结果（成功/失败/描述）
    lesson: str  # 总结的经验教训
    timestamp: str = ""
    times_referenced: int = 0  # 被引用次数（用于排序）


# ============================================================
# Feature 4: 动态技能
# ============================================================

class SkillSource(str, enum.Enum):
    STATIC = "static"  # 来自 .md 文件
    GENERATED = "generated"  # AI 在游玩中生成


class SkillEntry(BaseModel):
    """技能条目（含动态生成的）"""
    name: str
    trigger_condition: str = ""  # 什么场景下触发
    content: str = ""  # 技能内容/步骤
    source: SkillSource = SkillSource.GENERATED
    created_at: str = ""
    success_count: int = 0
    fail_count: int = 0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        return self.success_count / total if total > 0 else 0.5


# ============================================================
# Feature 5: 分层决策 - 本地分析结果
# ============================================================

class LocalAnalysis(BaseModel):
    """本地 CV 轻量分析结果"""
    has_significant_change: bool = True
    change_magnitude: float = 0.0  # 0.0-1.0
    change_regions: list[str] = Field(default_factory=list)  # 变化区域描述
    needs_llm: bool = True  # 是否需要调用 LLM
    suggested_action: Optional[str] = None  # 本地建议的操作（如有）
    reason: str = ""


# ============================================================
# 高级配置
# ============================================================

class AdvancedConfig(BaseModel):
    """高级功能配置（所有新功能的开关和参数）"""

    # Feature 1: 任务推断
    task_inference_enabled: bool = False

    # Feature 2: 自我反思
    reflection_enabled: bool = False
    reflection_diff_threshold: float = 0.005  # 像素差异低于此值视为操作可能失败
    reflection_max_retries: int = 2  # 最大重试次数

    # Feature 3: 记忆
    memory_enabled: bool = False
    short_term_capacity: int = 10  # 短期记忆容量（帧数）
    long_term_enabled: bool = False

    # Feature 4: 动态技能
    dynamic_skills_enabled: bool = False
    max_dynamic_skills: int = 50  # 每个游戏最多存储的动态技能数
    skill_prune_min_attempts: int = 5  # 至少使用多少次才考虑淘汰
    skill_prune_max_fail_rate: float = 0.8  # 失败率超过此值淘汰

    # Feature 5: 分层决策
    layered_decision_enabled: bool = False
    local_cv_change_threshold: float = 0.01  # 变化低于此值走本地决策
    static_frame_patience: int = 3  # 连续静态帧数超过此值才调 LLM
