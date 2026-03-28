"""记忆系统 - Feature 3

短期记忆: 最近 N 帧的分析结果滑动窗口
长期记忆: 跨会话持久化的经验库（JSON 文件）
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Optional

from .config_manager import CONFIG_DIR
from .models_advanced import ExperienceEntry, FrameMemory

logger = logging.getLogger(__name__)


class ShortTermMemory:
    """短期记忆 - 最近 N 帧的滑动窗口

    让 AI 能回顾最近几帧做了什么，避免重复操作或遗忘上下文。
    """

    def __init__(self, capacity: int = 10) -> None:
        self._frames: deque[FrameMemory] = deque(maxlen=capacity)
        self._frame_counter: int = 0

    def add_frame(
        self,
        analysis: str,
        actions_taken: list[str],
        task: str = "",
        confidence: float = 0.5,
        action_succeeded: Optional[bool] = None,
    ) -> None:
        """记录一帧"""
        self._frame_counter += 1
        frame = FrameMemory(
            frame_index=self._frame_counter,
            timestamp=time.time(),
            analysis=analysis,
            actions_taken=actions_taken,
            task_at_frame=task,
            confidence=confidence,
            action_succeeded=action_succeeded,
        )
        self._frames.append(frame)

    def get_context_prompt(self, limit: int = 5) -> str:
        """生成最近 N 帧的上下文摘要"""
        if not self._frames:
            return ""

        recent = list(self._frames)[-limit:]
        lines = []
        for f in recent:
            actions_str = ", ".join(f.actions_taken) if f.actions_taken else "无操作"
            status = ""
            if f.action_succeeded is True:
                status = " [成功]"
            elif f.action_succeeded is False:
                status = " [失败]"

            line = f"  第{f.frame_index}帧: {f.analysis[:80]}... | 操作: {actions_str}{status}"
            lines.append(line)

        return "最近操作历史:\n" + "\n".join(lines)

    def get_recent_actions(self, n: int = 3) -> list[str]:
        """获取最近 N 帧的操作列表（扁平化）"""
        result = []
        for f in list(self._frames)[-n:]:
            result.extend(f.actions_taken)
        return result

    def detect_action_loop(self, window: int = 5) -> bool:
        """检测是否陷入操作循环（最近 window 帧的操作高度重复）"""
        recent = list(self._frames)[-window:]
        if len(recent) < window:
            return False

        action_sets = [tuple(sorted(f.actions_taken)) for f in recent]
        # 如果超过 80% 的帧操作相同，视为循环
        most_common = max(set(action_sets), key=action_sets.count)
        return action_sets.count(most_common) >= window * 0.8

    @property
    def frame_count(self) -> int:
        return self._frame_counter

    def clear(self) -> None:
        self._frames.clear()
        self._frame_counter = 0


class LongTermMemory:
    """长期记忆 - 跨会话持久化经验库

    将游戏中学到的经验（场景→操作→结果→教训）存储为 JSON，
    下次遇到类似场景时可以检索参考。
    """

    STORAGE_DIR = CONFIG_DIR / "experiences"

    def __init__(self, game_id: str) -> None:
        self.game_id = self._sanitize_id(game_id)
        self._experiences: list[ExperienceEntry] = []
        self._load()

    @staticmethod
    def _sanitize_id(game_id: str) -> str:
        """清理 game_id 使其可作为文件名"""
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in game_id)

    @property
    def _file_path(self) -> Path:
        return self.STORAGE_DIR / f"{self.game_id}.json"

    def _load(self) -> None:
        """从磁盘加载经验"""
        if not self._file_path.exists():
            return
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            self._experiences = [ExperienceEntry(**e) for e in data]
            logger.info(f"已加载 {len(self._experiences)} 条经验 ({self.game_id})")
        except Exception as e:
            logger.error(f"加载长期记忆失败: {e}")

    def save(self) -> None:
        """持久化到磁盘"""
        self.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            data = [e.model_dump() for e in self._experiences]
            self._file_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"保存长期记忆失败: {e}")

    def add_experience(self, entry: ExperienceEntry) -> None:
        """添加一条经验"""
        # 避免完全重复
        for existing in self._experiences:
            if existing.situation == entry.situation and existing.lesson == entry.lesson:
                existing.times_referenced += 1
                self.save()
                return

        entry.timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._experiences.append(entry)

        # 保持容量上限（保留最常引用的）
        if len(self._experiences) > 200:
            self._experiences.sort(key=lambda e: e.times_referenced, reverse=True)
            self._experiences = self._experiences[:150]

        self.save()
        logger.info(f"新增经验: {entry.lesson[:50]}...")

    def search(self, situation: str, limit: int = 3) -> list[ExperienceEntry]:
        """根据当前场景搜索相关经验

        v1 版本使用关键词匹配，后续可升级为 embedding 检索。
        """
        if not self._experiences or not situation:
            return []

        # 提取关键词
        keywords = set(situation.lower().replace("，", " ").replace("。", " ").split())
        keywords.discard("")

        scored = []
        for exp in self._experiences:
            # 简单的关键词匹配评分
            exp_text = (exp.situation + " " + exp.lesson).lower()
            matches = sum(1 for kw in keywords if kw in exp_text)
            if matches > 0:
                # 引用次数作为加权
                score = matches + exp.times_referenced * 0.1
                scored.append((score, exp))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [exp for _, exp in scored[:limit]]

        # 更新引用计数
        for exp in results:
            exp.times_referenced += 1
        if results:
            self.save()

        return results

    def get_relevant_context(self, current_analysis: str, limit: int = 3) -> str:
        """搜索相关经验并格式化为提示词"""
        results = self.search(current_analysis, limit)
        if not results:
            return ""

        lines = []
        for exp in results:
            lines.append(
                f"  - 场景: {exp.situation}\n"
                f"    操作: {exp.action_taken}\n"
                f"    结果: {exp.outcome}\n"
                f"    经验: {exp.lesson}"
            )
        return "过往经验参考:\n" + "\n".join(lines)

    @property
    def experience_count(self) -> int:
        return len(self._experiences)
