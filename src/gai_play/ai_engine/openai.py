"""OpenAI GPT-4o 视觉分析引擎"""

from __future__ import annotations

import logging
import os

import openai

from .base import AIEngine

logger = logging.getLogger(__name__)


class OpenAIEngine(AIEngine):
    """使用 OpenAI GPT-4o 进行游戏截图分析"""

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        super().__init__(model or "gpt-4o")
        kwargs: dict = {"api_key": os.environ.get("OPENAI_API_KEY")}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.AsyncOpenAI(**kwargs)

    def _make_image_block(self, b64: str) -> dict:
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{b64}",
                "detail": "high",
            },
        }

    def _extract_content(self, response) -> str:
        if not response.choices:
            raise RuntimeError("OpenAI 返回了空响应")
        msg = response.choices[0].message
        content = msg.content or ""
        if not content and hasattr(msg, "reasoning") and msg.reasoning:
            content = msg.reasoning
        if not content:
            raw = msg.model_dump() if hasattr(msg, "model_dump") else {}
            content = raw.get("reasoning") or raw.get("reasoning_content") or ""
        if not content:
            raise RuntimeError("OpenAI 返回了空响应")
        return content

    async def _call_api(
        self, screenshot_base64: str, user_prompt: str
    ) -> str:
        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {
                    "role": "user",
                    "content": [
                        self._make_image_block(screenshot_base64),
                        {"type": "text", "text": user_prompt},
                    ],
                },
            ],
        )
        return self._extract_content(response)

    async def analyze_pair(
        self, before_b64: str, after_b64: str, prompt: str
    ) -> str:
        """发送操作前后两张截图给 OpenAI 进行对比分析"""
        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=512,
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {
                    "role": "user",
                    "content": [
                        self._make_image_block(before_b64),
                        self._make_image_block(after_b64),
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
        )
        return self._extract_content(response)
