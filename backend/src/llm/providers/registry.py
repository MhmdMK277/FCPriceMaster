"""Provider registry — maps provider IDs to provider classes."""

from __future__ import annotations

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
