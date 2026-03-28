"""AI 决策引擎抽象基类"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

from ..models import AIDecision, ActionType, GameAction

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是一个游戏 AI 玩家。你通过观察游戏截图来理解游戏状态，然后自主决定下一步操作。

你拥有完全的自主判断权。没有人会告诉你具体该点哪里、该按什么键——你需要自己观察、思考、行动。

## 你的思维方式
1. 观察: 仔细看截图中的每个细节——文字、按钮、角色、场景、UI 元素
2. 理解: 判断当前游戏处于什么状态，发生了什么
3. 思考: 基于你的知识和经验，决定最合理的下一步
4. 行动: 给出精确的操作指令

## 坐标系统
- 所有坐标使用归一化值 (0.0 到 1.0)
- (0.0, 0.0) = 截图左上角
- (1.0, 1.0) = 截图右下角
- 请尽量瞄准目标的中心位置

## 可用操作
- click: 鼠标左键点击 (需要 x, y)
- right_click: 鼠标右键点击 (需要 x, y)
- double_click: 鼠标双击 (需要 x, y)
- key_press: 按键 (需要 key, 如 "enter", "space", "escape")
- key_combo: 组合键 (需要 keys 列表, 如 ["ctrl", "s"])
- type_text: 输入文字 (需要 text)
- drag: 拖拽 (需要 x, y, x2, y2)
- scroll: 滚轮 (需要 scroll_amount, 正数向上负数向下, 可选 x, y)
- wait: 等待 (需要 duration 秒数)

## 输出格式
你必须只返回一个 JSON 对象:
```json
{
  "analysis": "你对当前画面的观察和判断 (用中文)",
  "actions": [
    {
      "action": "操作类型",
      "x": 0.5,
      "y": 0.5,
      "reason": "为什么执行这个操作"
    }
  ],
  "confidence": 0.8
}
```

只返回 JSON，不要包含其他文字。
"""


class AIEngine(ABC):
    """AI 决策引擎基类"""

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self._strategy_prompt: str = ""
        self._skills: list[dict] = []

    def set_strategy(self, prompt: str) -> None:
        """设置游戏策略目标"""
        self._strategy_prompt = prompt

    def set_skills(self, skills: list[dict]) -> None:
        """设置技能参考手册

        Args:
            skills: 技能列表，每个技能是一个 dict:
                {"name": "技能名", "description": "描述", "content": "详细内容"}
        """
        self._skills = skills

    def _build_user_prompt(self, game_context: str = "") -> str:
        """构建用户提示词"""
        parts = []

        # 策略目标
        if self._strategy_prompt:
            parts.append(f"## 游戏信息\n{self._strategy_prompt}")

        # 技能参考手册
        if self._skills:
            skill_text = "## 参考手册\n以下是一些你可以参考的知识，但你不必机械地遵循它们——根据实际画面自行判断:\n"
            for s in self._skills:
                name = s.get("name", "未命名")
                content = s.get("content", s.get("description", ""))
                skill_text += f"\n### {name}\n{content}\n"
            parts.append(skill_text)

        # 上下文
        if game_context:
            parts.append(f"## 上下文\n{game_context}")

        parts.append("观察这张游戏截图，自主判断并决定下一步操作。返回 JSON。")
        return "\n\n".join(parts)

    @abstractmethod
    async def _call_api(
        self, screenshot_base64: str, user_prompt: str
    ) -> str:
        """调用 AI API，返回原始文本响应"""
        ...

    async def analyze(
        self, screenshot_base64: str, game_context: str = ""
    ) -> AIDecision:
        """分析截图并返回决策"""
        user_prompt = self._build_user_prompt(game_context)

        try:
            raw = await self._call_api(screenshot_base64, user_prompt)
            return self._parse_response(raw)
        except Exception as e:
            logger.error(f"AI 分析失败: {e}")
            return AIDecision(
                analysis=f"AI 分析出错: {e}",
                actions=[
                    GameAction(action=ActionType.WAIT, duration=2.0, reason="AI 分析出错，等待重试")
                ],
                confidence=0.0,
            )

    def _parse_response(self, raw: str) -> AIDecision:
        """解析 AI 返回的 JSON"""
        text = raw.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(text)
            return AIDecision(**data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"JSON 解析失败，尝试宽松解析: {e}")
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                return AIDecision(**data)
            raise
