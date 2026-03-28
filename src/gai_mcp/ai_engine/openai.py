"""OpenAI GPT-4o 视觉分析引擎"""

from __future__ import annotations

import logging
import os

import openai

from .base import AIEngine, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class OpenAIEngine(AIEngine):
    """使用 OpenAI GPT-4o 进行游戏截图分析"""

    def __init__(self, model: str | None = None, base_url: str | None = None) -> None:
        super().__init__(model or "gpt-4o")
        kwargs: dict = {"api_key": os.environ.get("OPENAI_API_KEY")}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.AsyncOpenAI(**kwargs)

    async def _call_api(
        self, screenshot_base64: str, user_prompt: str
    ) -> str:
        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{screenshot_base64}",
                                "detail": "high",
                            },
                        },
                        {
                            "type": "text",
                            "text": user_prompt,
                        },
                    ],
                },
            ],
        )
        if not response.choices:
            raise RuntimeError("OpenAI 返回了空响应")
        msg = response.choices[0].message
        # 优先取 content，兼容推理模型的 reasoning 字段
        content = msg.content or ""
        if not content and hasattr(msg, "reasoning") and msg.reasoning:
            content = msg.reasoning
        if not content:
            # 兜底：尝试从原始数据中提取
            raw = msg.model_dump() if hasattr(msg, "model_dump") else {}
            content = raw.get("reasoning") or raw.get("reasoning_content") or ""
        if not content:
            raise RuntimeError("OpenAI 返回了空响应")
        return content
