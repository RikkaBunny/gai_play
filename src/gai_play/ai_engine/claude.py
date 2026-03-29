"""Claude 视觉分析引擎"""

from __future__ import annotations

import logging
import os

import anthropic

from .base import AIEngine

logger = logging.getLogger(__name__)


class ClaudeEngine(AIEngine):
    """使用 Claude API 进行游戏截图分析"""

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        super().__init__(model or "claude-sonnet-4-20250514")
        kwargs: dict = {"api_key": os.environ.get("ANTHROPIC_API_KEY")}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)

    def _make_image_block(self, b64: str) -> dict:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64,
            },
        }

    async def _call_api(
        self, screenshot_base64: str, user_prompt: str
    ) -> str:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=self.get_system_prompt(),
            messages=[
                {
                    "role": "user",
                    "content": [
                        self._make_image_block(screenshot_base64),
                        {"type": "text", "text": user_prompt},
                    ],
                }
            ],
        )
        if not response.content or not hasattr(response.content[0], "text"):
            raise RuntimeError(f"Claude 返回了意外的响应格式: {response.content}")
        return response.content[0].text

    async def analyze_pair(
        self, before_b64: str, after_b64: str, prompt: str
    ) -> str:
        """发送操作前后两张截图给 Claude 进行对比分析"""
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=512,
            system=self.get_system_prompt(),
            messages=[
                {
                    "role": "user",
                    "content": [
                        self._make_image_block(before_b64),
                        self._make_image_block(after_b64),
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        if not response.content or not hasattr(response.content[0], "text"):
            raise RuntimeError(f"Claude 返回了意外的响应格式: {response.content}")
        return response.content[0].text
