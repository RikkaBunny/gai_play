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
  "confidence": 0.8,
  "current_task": "当前你认为自己正在做的任务 (可选)",
  "new_experience": "如果你学到了重要经验，写在这里 (可选)",
  "new_skill": {
    "name": "技能名称",
    "trigger_condition": "什么场景触发",
    "steps": "具体步骤"
  },
  "visible_text": ["画面中的文字1", "文字2"]
}
```

注意:
- 只返回 JSON，不要包含其他文字
- current_task, new_experience, new_skill, visible_text 是可选字段，只在有必要时返回
- current_task: 简短描述你当前的任务阶段，如 "对话中"、"战斗中"、"探索地图"、"选择菜单"
- new_experience: 当你发现了重要的游戏机制或技巧时记录下来
- new_skill: 当你总结出可复用的操作策略时生成技能
- visible_text: 列出截图中所有可读的文字（按钮文本、对话内容、菜单项等）
"""

# 高级功能的额外系统提示词
ADVANCED_SYSTEM_ADDENDUM = """\

## 高级能力
你拥有记忆和学习能力。你可以:
1. **记住自己在做什么** — 在 current_task 中持续报告任务阶段，保持连贯性
2. **从经验中学习** — 当发现重要规律时，通过 new_experience 记录
3. **生成可复用技能** — 当总结出通用策略时，通过 new_skill 分享给未来的自己
4. **参考过往经验** — 下方会提供你之前积累的经验，请善用

关于任务追踪:
- 不要每帧都切换 current_task，保持任务的连贯性
- 只在真正进入新阶段时才更新任务
- 如果卡在同一个任务太久，主动尝试不同策略
"""


class AIEngine(ABC):
    """AI 决策引擎基类"""

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self._strategy_prompt: str = ""
        self._skills: list[dict] = []
        self._advanced_enabled: bool = False
        # 高级功能注入的上下文
        self._task_context: str = ""
        self._memory_context: str = ""
        self._reflection_context: str = ""
        self._experience_context: str = ""
        # Cradle 借鉴: 上一帧 AI 提取的可见文字
        self._visible_text_context: str = ""

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

    def enable_advanced(self, enabled: bool = True) -> None:
        """启用高级功能提示词"""
        self._advanced_enabled = enabled

    def set_task_context(self, context: str) -> None:
        """注入任务推断上下文 (Feature 1)"""
        self._task_context = context

    def set_memory_context(self, context: str) -> None:
        """注入短期记忆上下文 (Feature 3)"""
        self._memory_context = context

    def set_reflection_context(self, context: str) -> None:
        """注入反思反馈上下文 (Feature 2)"""
        self._reflection_context = context

    def set_experience_context(self, context: str) -> None:
        """注入长期经验上下文 (Feature 3)"""
        self._experience_context = context

    def set_visible_text_context(self, texts: list[str]) -> None:
        """注入上一帧 AI 提取的可见文字 (Cradle 借鉴)"""
        self._visible_text_context = str(texts) if texts else ""

    def get_system_prompt(self) -> str:
        """获取完整的系统提示词"""
        if self._advanced_enabled:
            return SYSTEM_PROMPT + ADVANCED_SYSTEM_ADDENDUM
        return SYSTEM_PROMPT

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

        # --- 高级功能上下文 ---
        # Feature 1: 任务状态
        if self._task_context:
            parts.append(f"## 当前任务状态\n{self._task_context}")

        # Feature 2: 反思反馈
        if self._reflection_context:
            parts.append(f"## 操作反馈\n{self._reflection_context}")

        # Feature 3: 短期记忆
        if self._memory_context:
            parts.append(f"## 近期记忆\n{self._memory_context}")

        # Feature 3: 长期经验
        if self._experience_context:
            parts.append(f"## 历史经验\n{self._experience_context}")

        # Cradle 借鉴: 上一帧可见文字
        if self._visible_text_context:
            parts.append(f"## 上一帧可见文字\n{self._visible_text_context}")

        # 基础上下文
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

    async def analyze_pair(
        self, before_b64: str, after_b64: str, prompt: str
    ) -> str:
        """Cradle 借鉴: 发送操作前后两张截图给 AI，返回原始文本

        默认实现降级为只发后图；子类可覆盖以支持真正的双图对比。
        """
        return await self._call_api(after_b64, prompt)

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
        confidence = 0.1
        conf_match = re.search(r'"confidence"\s*:\s*([\d.]+)', text)
        if conf_match:
            try:
                val = float(conf_match.group(1))
                if 0.0 <= val <= 1.0:
                    confidence = val
            except (ValueError, OverflowError):
                pass

        return AIDecision(
            analysis=analysis,
            actions=actions,
            confidence=min(confidence, 0.5),  # 兜底解析的结果降低置信度
        )
