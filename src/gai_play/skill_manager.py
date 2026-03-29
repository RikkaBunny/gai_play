"""动态技能管理器 - Feature 4

将静态 .md 技能文件与 AI 动态生成的技能统一管理。
AI 在游玩过程中可以生成新技能，存储到磁盘，后续可检索复用。
类似 Cradle 的 Skill Curation 模块。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from .config_manager import CONFIG_DIR
from .models_advanced import SkillEntry, SkillSource

logger = logging.getLogger(__name__)


class SkillManager:
    """技能管理器

    合并管理静态技能（.md 文件）和动态技能（AI 生成的），
    根据当前游戏场景检索最相关的技能提供给 AI 参考。
    """

    STORAGE_DIR = CONFIG_DIR / "dynamic_skills"

    def __init__(
        self,
        game_id: str,
        max_dynamic_skills: int = 50,
    ) -> None:
        self.game_id = self._sanitize_id(game_id)
        self.max_dynamic_skills = max_dynamic_skills
        self._static_skills: list[dict] = []
        self._dynamic_skills: list[SkillEntry] = []
        self._load_dynamic()

    @staticmethod
    def _sanitize_id(game_id: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in game_id)

    @property
    def _file_path(self) -> Path:
        return self.STORAGE_DIR / f"{self.game_id}.json"

    def set_static_skills(self, skills: list[dict]) -> None:
        """设置静态技能（从 .md 文件加载的）"""
        self._static_skills = skills

    def _load_dynamic(self) -> None:
        """从磁盘加载动态技能"""
        if not self._file_path.exists():
            return
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            self._dynamic_skills = [SkillEntry(**s) for s in data]
            logger.info(
                f"已加载 {len(self._dynamic_skills)} 个动态技能 ({self.game_id})"
            )
        except Exception as e:
            logger.error(f"加载动态技能失败: {e}")

    def save_dynamic(self) -> None:
        """持久化动态技能"""
        self.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            data = [s.model_dump() for s in self._dynamic_skills]
            self._file_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"保存动态技能失败: {e}")

    def add_skill(self, skill_data: dict) -> Optional[SkillEntry]:
        """添加 AI 生成的动态技能

        Args:
            skill_data: AI 返回的技能字典，格式:
                {"name": "...", "trigger_condition": "...", "steps": "..."}

        Returns:
            创建的 SkillEntry 或 None
        """
        name = skill_data.get("name", "").strip()
        if not name:
            return None

        # AI 返回的 steps/content 可能是 list，统一转为 str
        raw_content = skill_data.get("steps", skill_data.get("content", ""))
        if isinstance(raw_content, list):
            raw_content = "\n".join(str(item) for item in raw_content)
        raw_content = str(raw_content)

        raw_trigger = skill_data.get("trigger_condition", "")
        if isinstance(raw_trigger, list):
            raw_trigger = ", ".join(str(item) for item in raw_trigger)
        raw_trigger = str(raw_trigger)

        # 检查是否已存在同名技能
        for existing in self._dynamic_skills:
            if existing.name == name:
                existing.content = raw_content
                existing.trigger_condition = raw_trigger
                self.save_dynamic()
                logger.info(f"更新动态技能: {name}")
                return existing

        entry = SkillEntry(
            name=name,
            trigger_condition=raw_trigger,
            content=raw_content,
            source=SkillSource.GENERATED,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._dynamic_skills.append(entry)

        # 容量限制：淘汰最差的技能
        if len(self._dynamic_skills) > self.max_dynamic_skills:
            self._prune_worst(keep=self.max_dynamic_skills)

        self.save_dynamic()
        logger.info(f"新增动态技能: {name}")
        return entry

    def get_all_skills(self) -> list[dict]:
        """获取所有技能（静态 + 动态），格式与 AIEngine.set_skills() 兼容"""
        result = list(self._static_skills)

        for skill in self._dynamic_skills:
            result.append({
                "name": f"[AI生成] {skill.name}",
                "content": (
                    f"触发条件: {skill.trigger_condition}\n\n{skill.content}"
                    if skill.trigger_condition
                    else skill.content
                ),
                "_source": "dynamic",
                "_success_rate": skill.success_rate,
            })

        return result

    def get_relevant_skills(self, context: str, limit: int = 5) -> list[dict]:
        """根据当前场景检索最相关的技能"""
        all_skills = self.get_all_skills()
        if not context or not all_skills:
            return all_skills[:limit]

        keywords = set(context.lower().replace("，", " ").replace("。", " ").split())
        keywords.discard("")

        scored = []
        for skill in all_skills:
            text = (
                skill.get("name", "") + " " +
                skill.get("content", "")
            ).lower()
            matches = sum(1 for kw in keywords if kw in text)

            # 动态技能根据成功率加权
            success_bonus = skill.get("_success_rate", 0.5) * 0.5 if skill.get("_source") == "dynamic" else 0
            score = matches + success_bonus

            scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    def update_skill_stats(self, skill_name: str, succeeded: bool) -> None:
        """更新动态技能的成功/失败计数"""
        # 去掉 [AI生成] 前缀
        clean_name = skill_name.replace("[AI生成] ", "")
        for skill in self._dynamic_skills:
            if skill.name == clean_name:
                if succeeded:
                    skill.success_count += 1
                else:
                    skill.fail_count += 1
                self.save_dynamic()
                return

    def prune_bad_skills(
        self,
        min_attempts: int = 5,
        max_fail_rate: float = 0.8,
    ) -> int:
        """淘汰表现差的动态技能"""
        before = len(self._dynamic_skills)
        self._dynamic_skills = [
            s for s in self._dynamic_skills
            if (s.success_count + s.fail_count < min_attempts)
            or (s.success_rate >= (1.0 - max_fail_rate))
        ]
        removed = before - len(self._dynamic_skills)
        if removed:
            self.save_dynamic()
            logger.info(f"淘汰了 {removed} 个表现差的动态技能")
        return removed

    def _prune_worst(self, keep: int) -> None:
        """保留 keep 个最好的动态技能"""
        # 优先保留成功率高、使用次数多的
        self._dynamic_skills.sort(
            key=lambda s: (s.success_rate, s.success_count + s.fail_count),
            reverse=True,
        )
        self._dynamic_skills = self._dynamic_skills[:keep]

    @property
    def total_skills(self) -> int:
        return len(self._static_skills) + len(self._dynamic_skills)

    @property
    def dynamic_count(self) -> int:
        return len(self._dynamic_skills)
