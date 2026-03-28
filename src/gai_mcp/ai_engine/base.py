"""AI 决策引擎抽象基类"""

from __future__ import annotations

import json
import logging
import re
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
            logger.debug(f"AI 原始返回 (前500字): {raw[:500]}")
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
        """解析 AI 返回的 JSON，多重容错"""
        if not raw or not raw.strip():
            raise ValueError("AI 返回了空内容")

        text = raw.strip()

        # 1. 提取 ```json ... ``` 代码块
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        # 2. 直接解析
        try:
            data = json.loads(text)
            return AIDecision(**data)
        except (json.JSONDecodeError, ValueError):
            pass

        # 3. 提取最外层 { ... } 再解析
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            fragment = text[start:end]
            try:
                data = json.loads(fragment)
                return AIDecision(**data)
            except (json.JSONDecodeError, ValueError):
                pass

            # 4. 修复常见 JSON 格式问题后再试
            fixed = self._fix_json(fragment)
            try:
                data = json.loads(fixed)
                logger.info("JSON 修复成功")
                return AIDecision(**data)
            except (json.JSONDecodeError, ValueError):
                pass

        # 5. 都失败了，尝试从文本中提取关键字段构造决策
        logger.warning(f"JSON 解析全部失败，尝试从文本提取。原始内容: {text[:300]}")
        return self._fallback_parse(text)

    @staticmethod
    def _fix_json(text: str) -> str:
        """尝试修复常见的 JSON 格式问题"""
        # 去掉行尾注释 // ...
        text = re.sub(r'//[^\n]*', '', text)
        # 去掉尾部多余逗号 ,} 或 ,]
        text = re.sub(r',\s*([}\]])', r'\1', text)
        # 单引号换双引号
        # (只处理 key: 'value' 这种简单情况，避免误伤)
        text = re.sub(r"(?<=[\[{,:])\s*'([^']*?)'\s*(?=[,\]}])", r'"\1"', text)
        # 修复没有逗号的换行 "key": "value"\n"key2"
        text = re.sub(r'"\s*\n\s*"', '",\n"', text)
        # 修复 } 后缺逗号接 {
        text = re.sub(r'}\s*{', '},{', text)
        return text

    @staticmethod
    def _fallback_parse(text: str) -> AIDecision:
        """最终兜底：从 AI 返回的文本中提取信息构造决策"""
        # 尝试提取 analysis
        analysis = text[:200] if text else "AI 返回了无法解析的内容"

        # 尝试匹配 click 坐标
        actions = []
        click_match = re.search(
            r'"action"\s*:\s*"(\w+)".*?"x"\s*:\s*([\d.]+).*?"y"\s*:\s*([\d.]+)',
            text, re.DOTALL
        )
        if click_match:
            try:
                actions.append(GameAction(
                    action=ActionType(click_match.group(1)),
                    x=float(click_match.group(2)),
                    y=float(click_match.group(3)),
                    reason="从损坏的 JSON 中提取",
                ))
            except (ValueError, KeyError):
                pass

        # 如果什么操作都没提取到，给一个等待
        if not actions:
            actions.append(GameAction(
                action=ActionType.WAIT, duration=1.0,
                reason="AI 返回格式异常，等待重试",
            ))

        # 尝试提取 confidence
        conf_match = re.search(r'"confidence"\s*:\s*([\d.]+)', text)
        confidence = float(conf_match.group(1)) if conf_match else 0.1

        return AIDecision(
            analysis=analysis,
            actions=actions,
            confidence=min(confidence, 0.5),  # 兜底解析的结果降低置信度
        )
