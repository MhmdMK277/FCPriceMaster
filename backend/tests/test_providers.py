"""Tests for the LLM provider registry, NVIDIA provider, and verdict parsing."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.providers.base import LLMVerdict
from src.llm.providers.registry import PROVIDER_REGISTRY, get_available_providers
from src.llm.providers.nvidia_provider import MistralSmallProvider, NvidiaProvider, NvidiaVisionProvider


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def test_provider_registry_has_all_expected_ids():
    expected = {
        "haiku", "deepseek-v4-pro", "kimi-k2-6", "qwen3-80b",
        "mistral-small", "gpt-oss-120b", "mistral-vision",
    }
    assert set(PROVIDER_REGISTRY.keys()) == expected


def test_get_available_providers_empty_env(monkeypatch):
    """With no API keys set, no providers are available."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    # Ensure .env file lookup also returns nothing by patching _get_key
    with patch.object(NvidiaProvider, "_get_key", return_value=None):
        with patch("src.llm.providers.anthropic_provider.AnthropicProvider._get_key", return_value=None):
            available = get_available_providers()
    assert available == []


def test_get_available_providers_with_anthropic_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with patch("src.llm.providers.anthropic_provider._anthropic", MagicMock()):
        with patch.object(NvidiaProvider, "_get_key", return_value=None):
            available = get_available_providers()
    assert "haiku" in available
    assert "mistral-small" not in available


def test_get_available_providers_with_nvidia_key(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-testkey123")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("src.llm.providers.anthropic_provider.AnthropicProvider._get_key", return_value=None):
        available = get_available_providers()
    nvidia_ids = {"deepseek-v4-pro", "kimi-k2-6", "qwen3-80b", "mistral-small", "gpt-oss-120b", "mistral-vision"}
    assert nvidia_ids.issubset(set(available))
    assert "haiku" not in available


def test_nvidia_is_available_requires_nvapi_prefix(monkeypatch):
    provider = MistralSmallProvider()
    monkeypatch.setenv("NVIDIA_API_KEY", "invalid-key")
    assert provider.is_available() is False
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-validkey")
    assert provider.is_available() is True


# ---------------------------------------------------------------------------
# NvidiaProvider.ask — httpx mock tests
# ---------------------------------------------------------------------------

def _make_nvidia_response(verdict_dict: dict) -> dict:
    return {
        "choices": [
            {"message": {"content": json.dumps(verdict_dict)}}
        ]
    }


@pytest.mark.asyncio
async def test_nvidia_provider_ask_success(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-testkey")
    provider = MistralSmallProvider()

    verdict_payload = {
        "verdict": "buy",
        "confidence": 75,
        "reasoning": "Price near 7d low, SBC incoming.",
        "price_context": "Currently at week floor.",
        "risk": "low",
        "suggested_buy_price": 50000,
        "suggested_sell_price": None,
        "horizon": "medium (days)",
    }
    mock_response = MagicMock()
    mock_response.json.return_value = _make_nvidia_response(verdict_payload)
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("src.llm.providers.nvidia_provider.httpx.AsyncClient", return_value=mock_client):
        result = await provider.ask("system prompt", "user message")

    assert isinstance(result, LLMVerdict)
    assert result.action == "buy"
    assert result.confidence == 75
    assert result.cost_usd == 0.0
    assert result.model_id == "mistralai/mistral-small-4-119b-2603"


@pytest.mark.asyncio
async def test_nvidia_provider_uses_correct_base_url_and_model(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-testkey")
    provider = MistralSmallProvider()

    verdict_payload = {"verdict": "hold", "confidence": 50, "reasoning": ".",
                       "price_context": ".", "risk": "medium",
                       "suggested_buy_price": None, "suggested_sell_price": None,
                       "horizon": "medium (days)"}
    mock_response = MagicMock()
    mock_response.json.return_value = _make_nvidia_response(verdict_payload)
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("src.llm.providers.nvidia_provider.httpx.AsyncClient", return_value=mock_client):
        await provider.ask("sys", "user")

    call_args = mock_client.post.call_args
    assert "integrate.api.nvidia.com/v1/chat/completions" in call_args.args[0]
    payload = call_args.kwargs["json"]
    assert payload["model"] == "mistralai/mistral-small-4-119b-2603"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"


@pytest.mark.asyncio
async def test_nvidia_provider_strips_markdown_fences(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-testkey")
    provider = MistralSmallProvider()

    fenced = '```json\n{"verdict":"avoid","confidence":80,"reasoning":"test","price_context":"test","risk":"high","suggested_buy_price":null,"suggested_sell_price":null,"horizon":"short (hours)"}\n```'
    mock_response = MagicMock()
    mock_response.json.return_value = {"choices": [{"message": {"content": fenced}}]}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("src.llm.providers.nvidia_provider.httpx.AsyncClient", return_value=mock_client):
        result = await provider.ask("sys", "user")

    assert result.action == "avoid"
    assert result.confidence == 80


@pytest.mark.asyncio
async def test_nvidia_provider_handles_missing_fields_gracefully(monkeypatch):
    """If model omits optional fields, defaults are applied."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-testkey")
    provider = MistralSmallProvider()

    # Minimal response missing several fields
    minimal = {"verdict": "hold", "confidence": 45, "reasoning": "Unclear."}
    mock_response = MagicMock()
    mock_response.json.return_value = _make_nvidia_response(minimal)
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("src.llm.providers.nvidia_provider.httpx.AsyncClient", return_value=mock_client):
        result = await provider.ask("sys", "user")

    assert result.action == "hold"
    assert result.confidence == 45
    assert result.target_price is None
    assert result.cost_usd == 0.0


@pytest.mark.asyncio
async def test_nvidia_provider_raises_on_non_json(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-testkey")
    provider = MistralSmallProvider()

    mock_response = MagicMock()
    mock_response.json.return_value = {"choices": [{"message": {"content": "Sorry, I can't help with that."}}]}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("src.llm.providers.nvidia_provider.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="non-JSON"):
            await provider.ask("sys", "user")


@pytest.mark.asyncio
async def test_nvidia_provider_raises_without_key(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    provider = MistralSmallProvider()
    with patch.object(provider, "_get_key", return_value=None):
        with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
            await provider.ask("sys", "user")


@pytest.mark.asyncio
async def test_nvidia_vision_provider_sends_image_content(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-testkey")
    provider = NvidiaVisionProvider()

    mock_response = MagicMock()
    mock_response.json.return_value = {"choices": [{"message": {"content": '{"player_name": "Mbappe"}'}}]}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("src.llm.providers.nvidia_provider.httpx.AsyncClient", return_value=mock_client):
        raw = await provider.extract_json("extract", "abc123")

    assert json.loads(raw)["player_name"] == "Mbappe"
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["model"] == "mistralai/mistral-small-4-119b-2603"
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"] == "data:image/jpeg;base64,abc123"
    assert content[1]["type"] == "text"


# ---------------------------------------------------------------------------
# LLMVerdict dataclass
# ---------------------------------------------------------------------------

def test_llm_verdict_fields():
    v = LLMVerdict(
        action="buy",
        confidence=85,
        horizon_hours=72,
        target_price=45000,
        reasoning="Good entry.",
        model_id="test-model",
        cost_usd=0.0,
    )
    assert v.action == "buy"
    assert v.cost_usd == 0.0
