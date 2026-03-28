"""本地模型引擎 (Ollama 等)"""

from __future__ import annotations

import logging

import httpx

from .base import AIEngine, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class LocalEngine(AIEngine):
    """使用本地模型 (Ollama) 进行游戏截图分析"""

    def __init__(
        self,
        model: str | None = None,
        base_url: str = "http://localhost:11434",
    ) -> None:
        super().__init__(model or "llava")
        self.base_url = base_url.rstrip("/")

    async def _call_api(
        self, screenshot_base64: str, user_prompt: str
    ) -> str:
        prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "images": [screenshot_base64],
                    "stream": False,
                },
            )
            response.raise_for_status()
            data = response.json()
            if "response" not in data:
                raise RuntimeError(f"Ollama 返回了意外的响应格式: {list(data.keys())}")
            return data["response"]
