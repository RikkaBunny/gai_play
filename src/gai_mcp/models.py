"""数据模型定义"""

from __future__ import annotations

import enum
from typing import Optional

from pydantic import BaseModel, Field


class ActionType(str, enum.Enum):
    """操作类型"""

    CLICK = "click"
    RIGHT_CLICK = "right_click"
    DOUBLE_CLICK = "double_click"
    KEY_PRESS = "key_press"
    KEY_COMBO = "key_combo"
    TYPE_TEXT = "type_text"
    DRAG = "drag"
    SCROLL = "scroll"
    WAIT = "wait"


class GameAction(BaseModel):
    """AI 返回的单个游戏操作"""

    action: ActionType
    # 鼠标操作的坐标 (相对于截图的归一化坐标 0.0-1.0)
    x: Optional[float] = None
    y: Optional[float] = None
    # 拖拽终点
    x2: Optional[float] = None
    y2: Optional[float] = None
    # 按键
    key: Optional[str] = None
    keys: Optional[list[str]] = None
    # 输入文字
    text: Optional[str] = None
    # 滚轮方向 (正数向上, 负数向下)
    scroll_amount: Optional[int] = None
    # 等待秒数
    duration: Optional[float] = None
    # 操作说明 (AI 解释为什么做这个操作)
    reason: Optional[str] = None


class AIDecision(BaseModel):
    """AI 的完整决策结果"""

    # AI 对当前游戏状态的分析
    analysis: str
    # 要执行的操作列表 (按顺序)
    actions: list[GameAction]
    # AI 的置信度 (0.0-1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class SessionStatus(str, enum.Enum):
    """游戏会话状态"""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class GameSession(BaseModel):
    """游戏会话信息"""

    window_title: str
    hwnd: Optional[int] = None
    status: SessionStatus = SessionStatus.IDLE
    ai_provider: str = "claude"
    strategy_prompt: str = ""
    total_decisions: int = 0
    total_actions: int = 0
    last_analysis: Optional[str] = None
    last_error: Optional[str] = None


class GameConfig(BaseModel):
    """游戏配置"""

    # AI 配置
    ai_provider: str = "claude"
    ai_model: Optional[str] = None

    # 游戏循环配置
    capture_interval: float = 2.0
    action_delay: float = 0.5
    change_threshold: float = 0.02
    screenshot_quality: int = 75
    screenshot_max_width: int = 1280

    # 虚拟桌面
    virtual_desktop_enabled: bool = True

    # 游戏策略 prompt
    strategy_prompt: str = ""

    # 截图感兴趣区域 (归一化坐标)
    roi: Optional[tuple[float, float, float, float]] = None
