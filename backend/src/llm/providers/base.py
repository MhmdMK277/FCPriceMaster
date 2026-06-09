"""Abstract base classes for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMVerdict:
    action: str           # buy / avoid / hold
    confidence: int       # 0-100
    horizon_hours: int
    target_price: int | None
    reasoning: str
    model_id: str
    cost_usd: float       # 0.0 for free providers


class BaseProvider(ABC):
    provider_name: str
    model_id: str

    @abstractmethod
    async def ask(self, system_prompt: str, user_message: str) -> LLMVerdict: ...

    @abstractmethod
    def is_available(self) -> bool:
        """True if the required API key is present and non-empty."""
        ...
