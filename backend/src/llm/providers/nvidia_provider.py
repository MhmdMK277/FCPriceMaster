"""NVIDIA NIM inference providers (OpenAI-compatible API, free tier)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from .base import BaseProvider, LLMVerdict
from src.llm.recommender import _parse_horizon

_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
_ENV_PATH = Path(__file__).parents[4] / ".env"


class NvidiaProvider(BaseProvider):
    """Base for NVIDIA NIM providers using the OpenAI-compatible API."""

    def _get_key(self) -> str | None:
        key = os.environ.get("NVIDIA_API_KEY")
        if key:
            return key.strip()
        try:
            content = _ENV_PATH.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("NVIDIA_API_KEY") and "=" in line:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
        return None

    def is_available(self) -> bool:
        key = self._get_key()
        return bool(key and key.startswith("nvapi-"))


class NvidiaTextProvider(NvidiaProvider):
    """Text-only NVIDIA NIM provider, parameterized by model_id."""

    def __init__(self, model_id: str | None = None, provider_name: str | None = None) -> None:
        if model_id is not None:
            self.model_id = model_id
        if provider_name is not None:
            self.provider_name = provider_name

    async def ask(self, system_prompt: str, user_message: str) -> LLMVerdict:
        key = self._get_key()
        if not key:
            raise RuntimeError("NVIDIA_API_KEY not set")

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{_NVIDIA_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    "model": self.model_id,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "max_tokens": 500,
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        raw = data["choices"][0]["message"]["content"].strip()
        # Strip markdown fences if model adds them despite prompt instructions
        if raw.startswith("```"):
            lines = raw.split("\n")
            start = 1 if lines[0].startswith("```") else 0
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            raw = "\n".join(lines[start:end]).strip()

        try:
            verdict = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"NVIDIA model returned non-JSON: {raw[:200]}") from exc

        return LLMVerdict(
            action=verdict.get("verdict", "hold"),
            confidence=int(verdict.get("confidence", 50)),
            horizon_hours=_parse_horizon(verdict.get("horizon", "medium (days)")),
            target_price=verdict.get("suggested_buy_price") or verdict.get("suggested_sell_price"),
            reasoning=verdict.get("reasoning", ""),
            model_id=self.model_id,
            cost_usd=0.0,
        )


class NvidiaVisionProvider(NvidiaProvider):
    provider_name = "Mistral Vision"
    model_id = "mistralai/mistral-small-4-119b-2603"

    async def ask(
        self,
        system_prompt: str,
        user_message: str,
        image_b64: str | None = None,
    ) -> LLMVerdict:
        raw = await self.complete(system_prompt, user_message, image_b64=image_b64)
        try:
            verdict = json.loads(_strip_fences(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"NVIDIA vision model returned non-JSON: {raw[:200]}") from exc

        return LLMVerdict(
            action=verdict.get("verdict", "hold"),
            confidence=int(verdict.get("confidence", 50)),
            horizon_hours=_parse_horizon(verdict.get("horizon", "medium (days)")),
            target_price=verdict.get("suggested_buy_price") or verdict.get("suggested_sell_price"),
            reasoning=verdict.get("reasoning", ""),
            model_id=self.model_id,
            cost_usd=0.0,
        )

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        image_b64: str | None = None,
    ) -> str:
        key = self._get_key()
        if not key:
            raise RuntimeError("NVIDIA_API_KEY not set")

        combined_text = f"{system_prompt}\n\n{user_message}" if system_prompt else user_message
        if image_b64:
            user_content: str | list[dict[str, object]] = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                },
                {"type": "text", "text": combined_text},
            ]
            messages = [{"role": "user", "content": user_content}]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{_NVIDIA_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    "model": self.model_id,
                    "messages": messages,
                    "max_tokens": 500,
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return data["choices"][0]["message"]["content"].strip()

    async def extract_json(self, prompt: str, image_b64: str) -> str:
        return _strip_fences(await self.complete("", prompt, image_b64=image_b64))


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end]).strip()
    return text


class DeepSeekV4ProProvider(NvidiaTextProvider):
    provider_name = "DeepSeek V4 Pro"
    model_id = "deepseek-ai/deepseek-v4-pro"


class KimiK26Provider(NvidiaTextProvider):
    provider_name = "Kimi K2.6"
    model_id = "moonshotai/kimi-k2.6"


class Qwen3Provider(NvidiaTextProvider):
    provider_name = "Qwen3 80B"
    model_id = "qwen/qwen3-next-80b-a3b-instruct"


class MistralSmallProvider(NvidiaTextProvider):
    provider_name = "Mistral Small"
    model_id = "mistralai/mistral-small-4-119b-2603"


class GPTOSSProvider(NvidiaTextProvider):
    provider_name = "GPT OSS 120B"
    model_id = "openai/gpt-oss-120b"
