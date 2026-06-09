"""Anthropic Claude Haiku provider."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from .base import BaseProvider, LLMVerdict
from src.llm.recommender import _parse_horizon, _strip_fences
from src.llm.ask import _INPUT_COST_PER_TOKEN, _OUTPUT_COST_PER_TOKEN

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None  # type: ignore[assignment]

_ENV_PATH = Path(__file__).parents[4] / ".env"


class AnthropicProvider(BaseProvider):
    provider_name = "Claude Haiku"
    model_id = "claude-haiku-4-5-20251001"

    def _get_key(self) -> str | None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:
            return key.strip()
        try:
            content = _ENV_PATH.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY") and "=" in line:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
        return None

    def is_available(self) -> bool:
        return _anthropic is not None and bool(self._get_key())

    async def ask(self, system_prompt: str, user_message: str) -> LLMVerdict:
        if _anthropic is None:
            raise ImportError("anthropic package not installed")
        key = self._get_key()
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        def _sync_call() -> tuple[dict, int, int]:
            client = _anthropic.Anthropic(api_key=key)
            response = client.messages.create(
                model=self.model_id,
                max_tokens=500,
                temperature=0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            raw = _strip_fences(response.content[0].text.strip())
            return json.loads(raw), response.usage.input_tokens, response.usage.output_tokens

        verdict, in_tok, out_tok = await asyncio.to_thread(_sync_call)
        cost = in_tok * _INPUT_COST_PER_TOKEN + out_tok * _OUTPUT_COST_PER_TOKEN

        return LLMVerdict(
            action=verdict.get("verdict", "hold"),
            confidence=int(verdict.get("confidence", 50)),
            horizon_hours=_parse_horizon(verdict.get("horizon", "medium (days)")),
            target_price=verdict.get("suggested_buy_price") or verdict.get("suggested_sell_price"),
            reasoning=verdict.get("reasoning", ""),
            model_id=self.model_id,
            cost_usd=round(cost, 6),
        )
