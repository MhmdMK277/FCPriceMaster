"""Provider registry — maps provider IDs to provider classes."""

from __future__ import annotations

import asyncio

from .anthropic_provider import AnthropicProvider
from .nvidia_provider import (
    DeepSeekV4ProProvider,
    GPTOSSProvider,
    KimiK26Provider,
    MistralSmallProvider,
    NvidiaVisionProvider,
    Qwen3Provider,
)

PROVIDER_REGISTRY: dict[str, type] = {
    "haiku": AnthropicProvider,
    "deepseek-v4-pro": DeepSeekV4ProProvider,
    "kimi-k2-6": KimiK26Provider,
    "qwen3-80b": Qwen3Provider,
    "mistral-small": MistralSmallProvider,
    "gpt-oss-120b": GPTOSSProvider,
    "mistral-vision": NvidiaVisionProvider,
}

PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    pid: cls().provider_name for pid, cls in PROVIDER_REGISTRY.items()
}


def get_available_providers() -> list[str]:
    """Return provider IDs where the required API key is present."""
    return [pid for pid, cls in PROVIDER_REGISTRY.items() if cls().is_available()]


async def probe_all_providers() -> dict[str, bool]:
    """Probe every registered provider, in parallel. Returns {provider_id: healthy}.

    Anthropic is assumed healthy whenever its key is set (no probe — it costs
    money and is not the flaky side). NVIDIA providers get a real 1-token
    completion probe because their model list lies about availability.
    """
    providers = {pid: cls() for pid, cls in PROVIDER_REGISTRY.items()}

    async def _probe(pid: str, provider: object) -> tuple[str, bool]:
        if not provider.is_available():
            return pid, False
        health_check = getattr(provider, "health_check", None)
        if health_check is None:  # Anthropic: key present == assumed up
            return pid, True
        try:
            return pid, bool(await health_check())
        except Exception:
            return pid, False

    results = await asyncio.gather(*(_probe(pid, p) for pid, p in providers.items()))
    return dict(results)
