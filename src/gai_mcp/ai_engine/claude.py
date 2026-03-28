"""Claude 视觉分析引擎"""

from __future__ import annotations

import logging
import os

import anthropic

from .base import AIEngine, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ClaudeEngine(AIEngine):
    """使用 Claude API 进行游戏截图分析"""

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        super().__init__(model or "claude-sonnet-4-20250514")
        kwargs: dict = {"api_key": os.environ.get("ANTHROPIC_API_KEY")}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)

    async def _call_api(
        self, screenshot_base64: str, user_prompt: str
    ) -> str:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": screenshot_base64,
                            },
                        },
                        {
                            "type": "text",
                            "text": user_prompt,
                        },
                    ],
                }
            ],
        )
        if not response.content or not hasattr(response.content[0], "text"):
            raise RuntimeError(f"Claude 返回了意外的响应格式: {response.content}")
        return response.content[0].text
