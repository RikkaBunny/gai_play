"""自我反思引擎 - Feature 2

操作执行后，对比"操作前"和"操作后"截图，
判断操作是否成功，如果失败则建议调整策略。
类似 Cradle 的 Self-Reflection 模块。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PIL import Image

from .models import AIDecision, ActionType, GameAction
from .models_advanced import ReflectionResult

logger = logging.getLogger(__name__)


class ReflectionEngine:
    """自我反思引擎

    两层检查:
    1. 快速像素 diff（零成本）→ 判断画面是否变化
    2. 可选 LLM 反思（仅在快速检查可疑时）→ 深度分析成败
    """

    def __init__(
        self,
        diff_threshold: float = 0.005,
        max_retries: int = 2,
    ) -> None:
        self.diff_threshold = diff_threshold
        self.max_retries = max_retries
        self.consecutive_failures: int = 0
        self._last_reflection: Optional[ReflectionResult] = None

    def reflect(
        self,
        before_img: Image.Image,
        after_img: Image.Image,
        actions_taken: list[GameAction],
    ) -> ReflectionResult:
        """对比操作前后截图，判断操作是否生效

        Args:
            before_img: 操作前截图
            after_img: 操作后截图
            actions_taken: 已执行的操作列表

        Returns:
            ReflectionResult
        """
        # 计算整体像素差异
        diff_ratio = self._compute_diff_ratio(before_img, after_img)

        # 分析变化区域
        region_changes = self._compute_region_changes(before_img, after_img)

        # 判断是否为纯等待操作（等待操作不期望画面变化）
        is_wait_only = all(a.action == ActionType.WAIT for a in actions_taken)

        if is_wait_only:
            # 等待操作：画面无变化是正常的
            result = ReflectionResult(
                action_succeeded=True,
                pixel_diff_ratio=diff_ratio,
                expected_change="等待操作，不期望画面变化",
                actual_change=f"像素差异: {diff_ratio:.4f}",
            )
        elif diff_ratio < self.diff_threshold:
            # 执行了操作但画面几乎没变 → 操作可能失败
            self.consecutive_failures += 1

            # 生成调整建议
            adjustment = self._suggest_adjustment(actions_taken)

            result = ReflectionResult(
                action_succeeded=False,
                pixel_diff_ratio=diff_ratio,
                expected_change="执行操作后画面应有变化",
                actual_change=f"像素差异仅 {diff_ratio:.4f}，画面几乎未变",
                adjustment=adjustment,
                should_retry=self.consecutive_failures <= self.max_retries,
            )
            logger.warning(
                f"反思: 操作可能失败 (diff={diff_ratio:.4f}, "
                f"连续失败={self.consecutive_failures})"
            )
        else:
            # 画面有变化 → 操作可能成功
            self.consecutive_failures = 0
            change_desc = self._describe_changes(region_changes)

            result = ReflectionResult(
                action_succeeded=True,
                pixel_diff_ratio=diff_ratio,
                expected_change="操作后画面应变化",
                actual_change=f"像素差异: {diff_ratio:.4f}。{change_desc}",
            )

        self._last_reflection = result
        return result

    def get_reflection_context(self) -> str:
        """将最近一次反思结果格式化为 AI 上下文"""
        if not self._last_reflection:
            return ""

        r = self._last_reflection
        if r.action_succeeded:
            return f"上次操作反馈: 操作成功，画面已变化 (diff={r.pixel_diff_ratio:.4f})"
        else:
            parts = [
                f"上次操作反馈: ⚠️ 操作可能失败，画面未变化 (diff={r.pixel_diff_ratio:.4f})",
            ]
            if r.adjustment:
                parts.append(f"建议调整: {r.adjustment}")
            parts.append(f"连续失败次数: {self.consecutive_failures}")
            return "\n".join(parts)

    @staticmethod
    def _compute_diff_ratio(img_a: Image.Image, img_b: Image.Image) -> float:
        """计算两张图的像素差异比例 (0.0-1.0)"""
        # 统一尺寸
        if img_a.size != img_b.size:
            img_b = img_b.resize(img_a.size, Image.LANCZOS)

        arr_a = np.array(img_a.convert("L"), dtype=np.float32)
        arr_b = np.array(img_b.convert("L"), dtype=np.float32)

        diff = np.abs(arr_a - arr_b)
        return float(np.mean(diff) / 255.0)

    @staticmethod
    def _compute_region_changes(
        img_a: Image.Image, img_b: Image.Image, grid: int = 3
    ) -> list[dict]:
        """将画面分为 grid×grid 的区域，计算每个区域的变化程度"""
        if img_a.size != img_b.size:
            img_b = img_b.resize(img_a.size, Image.LANCZOS)

        arr_a = np.array(img_a.convert("L"), dtype=np.float32)
        arr_b = np.array(img_b.convert("L"), dtype=np.float32)

        h, w = arr_a.shape
        cell_h, cell_w = h // grid, w // grid

        regions = []
        region_names = [
            ["左上", "中上", "右上"],
            ["左中", "正中", "右中"],
            ["左下", "中下", "右下"],
        ]

        for row in range(grid):
            for col in range(grid):
                y1, y2 = row * cell_h, (row + 1) * cell_h
                x1, x2 = col * cell_w, (col + 1) * cell_w

                region_diff = np.abs(arr_a[y1:y2, x1:x2] - arr_b[y1:y2, x1:x2])
                diff_ratio = float(np.mean(region_diff) / 255.0)

                name = region_names[row][col] if row < grid and col < grid else f"({row},{col})"
                regions.append({
                    "name": name,
                    "diff": diff_ratio,
                })

        return regions

    @staticmethod
    def _describe_changes(region_changes: list[dict]) -> str:
        """描述变化区域"""
        if not region_changes:
            return ""

        significant = [r for r in region_changes if r["diff"] > 0.02]
        if not significant:
            return "各区域变化均较小"

        significant.sort(key=lambda r: r["diff"], reverse=True)
        top = significant[:3]
        desc = "、".join(f"{r['name']}({r['diff']:.3f})" for r in top)
        return f"主要变化区域: {desc}"

    @staticmethod
    def _suggest_adjustment(actions: list[GameAction]) -> str:
        """根据失败的操作类型，建议调整方向"""
        if not actions:
            return "尝试执行其他操作"

        action_types = set(a.action for a in actions)

        if ActionType.CLICK in action_types or ActionType.DOUBLE_CLICK in action_types:
            return "点击可能未命中目标，尝试: 1) 微调坐标 2) 先移动再点击 3) 改用其他操作"
        elif ActionType.KEY_PRESS in action_types:
            return "按键可能未生效，尝试: 1) 先点击窗口激活 2) 换一个按键 3) 使用鼠标操作"
        elif ActionType.TYPE_TEXT in action_types:
            return "文字输入可能未生效，尝试: 1) 先点击输入框 2) 检查输入法状态"
        elif ActionType.DRAG in action_types:
            return "拖拽可能未生效，尝试: 1) 调整起止坐标 2) 分解为多次点击"
        else:
            return "操作未生效，请尝试其他策略"

    def reset(self) -> None:
        """重置状态"""
        self.consecutive_failures = 0
        self._last_reflection = None
