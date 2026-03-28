"""本地轻量 CV 分析器 - Feature 5

用零成本的像素分析处理常规帧（如对话推进、静态画面），
只在需要战略决策时才调用 LLM，大幅降低 API 成本。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PIL import Image

from .models import AIDecision, ActionType, GameAction
from .models_advanced import LocalAnalysis

logger = logging.getLogger(__name__)


class LocalAnalyzer:
    """本地轻量 CV 分析器

    不调用任何 AI API，纯靠像素分析做简单判断:
    - 画面是否变化
    - 变化量级和区域
    - 是否需要 LLM 介入
    - 对简单场景给出本地操作建议
    """

    def __init__(
        self,
        change_threshold: float = 0.01,
        static_frame_patience: int = 3,
    ) -> None:
        self.change_threshold = change_threshold
        self.static_frame_patience = static_frame_patience
        self._prev_frame: Optional[np.ndarray] = None
        self._static_frame_count: int = 0
        self._total_local_decisions: int = 0
        self._total_llm_deferred: int = 0

    def analyze(self, img: Image.Image) -> LocalAnalysis:
        """对当前帧进行本地分析"""
        current = np.array(img.convert("L"), dtype=np.float32)

        if self._prev_frame is None:
            self._prev_frame = current
            # 第一帧总是需要 LLM
            return LocalAnalysis(
                has_significant_change=True,
                change_magnitude=1.0,
                needs_llm=True,
                reason="首帧，需要 LLM 建立初始认知",
            )

        # 尺寸不同视为大变化
        if current.shape != self._prev_frame.shape:
            self._prev_frame = current
            self._static_frame_count = 0
            return LocalAnalysis(
                has_significant_change=True,
                change_magnitude=1.0,
                needs_llm=True,
                reason="画面尺寸变化，可能切换了场景",
            )

        # 计算像素差异
        diff = np.abs(current - self._prev_frame)
        change_magnitude = float(np.mean(diff) / 255.0)
        self._prev_frame = current

        # 分析变化区域
        change_regions = self._analyze_regions(diff)

        if change_magnitude < self.change_threshold:
            # 画面几乎没变
            self._static_frame_count += 1

            if self._static_frame_count >= self.static_frame_patience:
                # 静止太久了，可能需要 LLM 判断是否卡住
                self._static_frame_count = 0
                return LocalAnalysis(
                    has_significant_change=False,
                    change_magnitude=change_magnitude,
                    change_regions=change_regions,
                    needs_llm=True,
                    reason=f"画面连续 {self.static_frame_patience} 帧静止，可能卡住",
                )

            # 静止帧：本地给一个简单的点击操作
            self._total_local_decisions += 1
            return LocalAnalysis(
                has_significant_change=False,
                change_magnitude=change_magnitude,
                change_regions=change_regions,
                needs_llm=False,
                suggested_action="click_center",
                reason=f"画面静止 ({self._static_frame_count}/{self.static_frame_patience})，本地尝试点击推进",
            )
        else:
            # 画面有变化
            self._static_frame_count = 0

            # 判断变化是否显著到需要 LLM
            if change_magnitude > 0.1:
                # 大幅变化（场景切换、战斗等）→ 需要 LLM
                self._total_llm_deferred += 1
                return LocalAnalysis(
                    has_significant_change=True,
                    change_magnitude=change_magnitude,
                    change_regions=change_regions,
                    needs_llm=True,
                    reason=f"画面大幅变化 ({change_magnitude:.3f})，需要 LLM 分析",
                )

            # 检查是否只是底部文本区域变化（对话推进的典型特征）
            if self._is_dialogue_change(change_regions):
                self._total_local_decisions += 1
                return LocalAnalysis(
                    has_significant_change=True,
                    change_magnitude=change_magnitude,
                    change_regions=change_regions,
                    needs_llm=False,
                    suggested_action="click_center",
                    reason="疑似对话文本变化，本地点击推进",
                )

            # 中等变化 → LLM 分析
            self._total_llm_deferred += 1
            return LocalAnalysis(
                has_significant_change=True,
                change_magnitude=change_magnitude,
                change_regions=change_regions,
                needs_llm=True,
                reason=f"画面中等变化 ({change_magnitude:.3f})，需要 LLM 判断",
            )

    def create_local_decision(self, analysis: LocalAnalysis) -> AIDecision:
        """根据本地分析结果生成一个简单决策（不调用 LLM）"""
        if analysis.suggested_action == "click_center":
            return AIDecision(
                analysis=f"[本地决策] {analysis.reason}",
                actions=[
                    GameAction(
                        action=ActionType.CLICK,
                        x=0.5, y=0.5,
                        reason="本地 CV 判断：点击画面中心推进",
                    )
                ],
                confidence=0.3,  # 本地决策置信度较低
            )
        elif analysis.suggested_action == "wait":
            return AIDecision(
                analysis=f"[本地决策] {analysis.reason}",
                actions=[
                    GameAction(action=ActionType.WAIT, duration=1.0, reason="本地等待")
                ],
                confidence=0.3,
            )
        else:
            # 默认等待
            return AIDecision(
                analysis=f"[本地决策] {analysis.reason}",
                actions=[
                    GameAction(action=ActionType.WAIT, duration=0.5, reason="未知操作")
                ],
                confidence=0.1,
            )

    @staticmethod
    def _analyze_regions(diff: np.ndarray, grid: int = 3) -> list[str]:
        """分析变化发生在哪些区域"""
        h, w = diff.shape
        cell_h, cell_w = h // grid, w // grid
        region_names = [
            ["左上", "中上", "右上"],
            ["左中", "正中", "右中"],
            ["左下", "中下", "右下"],
        ]

        active_regions = []
        for row in range(grid):
            for col in range(grid):
                y1, y2 = row * cell_h, (row + 1) * cell_h
                x1, x2 = col * cell_w, (col + 1) * cell_w
                region_diff = float(np.mean(diff[y1:y2, x1:x2]) / 255.0)
                if region_diff > 0.02:
                    active_regions.append(region_names[row][col])

        return active_regions

    @staticmethod
    def _is_dialogue_change(change_regions: list[str]) -> bool:
        """判断变化是否是对话推进的典型模式

        对话通常只在底部区域变化（文字框）。
        """
        if not change_regions:
            return False

        bottom_regions = {"左下", "中下", "右下"}
        # 如果变化只在底部区域
        return all(r in bottom_regions for r in change_regions)

    def get_stats(self) -> dict:
        """获取分层决策统计"""
        total = self._total_local_decisions + self._total_llm_deferred
        return {
            "local_decisions": self._total_local_decisions,
            "llm_deferred": self._total_llm_deferred,
            "local_ratio": self._total_local_decisions / max(total, 1),
            "total": total,
        }

    def reset(self) -> None:
        """重置状态"""
        self._prev_frame = None
        self._static_frame_count = 0
