"""AI provider abstraction with a zero-cost-first routing policy."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Protocol

import requests

from .repository import WinstonRepository


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class GenerationResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0


class AIProvider(Protocol):
    name: str
    model: str
    paid: bool

    def available(self) -> bool: ...
    def generate(self, prompt: str, *, system: str = "", max_tokens: int = 400) -> GenerationResult: ...


class GeminiProvider:
    name = "gemini"
    paid = False

    def __init__(self, api_key: str = "", model: str = "gemini-2.5-flash-lite",
                 endpoint: str = "https://generativelanguage.googleapis.com/v1beta") -> None:
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint.rstrip("/")

    def available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, *, system: str = "", max_tokens: int = 400) -> GenerationResult:
        if not self.available():
            raise ProviderError("Gemini is not configured")
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.65},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        response = requests.post(
            f"{self.endpoint}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
            json=payload, timeout=45,
        )
        if response.status_code >= 400:
            raise ProviderError(f"Gemini HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        try:
            text = "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Gemini returned no text") from exc
        usage = data.get("usageMetadata", {})
        return GenerationResult(text, self.name, self.model,
                                int(usage.get("promptTokenCount", 0)),
                                int(usage.get("candidatesTokenCount", 0)), 0)


class OllamaProvider:
    name = "ollama"
    paid = False

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen3:8b") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def available(self) -> bool:
        return self.base_url.startswith(("http://localhost", "http://127.0.0.1"))

    def generate(self, prompt: str, *, system: str = "", max_tokens: int = 400) -> GenerationResult:
        response = requests.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt, "system": system, "stream": False,
                  "think": False,
                  "options": {"num_predict": max_tokens, "temperature": 0.65}},
            timeout=90,
        )
        if response.status_code >= 400:
            raise ProviderError(f"Ollama HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        text = str(data.get("response", "")).strip()
        if not text:
            raise ProviderError("Ollama returned no text")
        return GenerationResult(text, self.name, self.model,
                                int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0)), 0)


class ClaudeProvider:
    name = "claude"
    paid = True

    def __init__(self, api_key: str = "", model: str = "claude-sonnet-4-6", enabled: bool = False) -> None:
        self.api_key = api_key
        self.model = model
        self.enabled = enabled

    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def generate(self, prompt: str, *, system: str = "", max_tokens: int = 400) -> GenerationResult:
        if not self.available():
            raise ProviderError("Claude paid-provider access is disabled")
        import anthropic
        response = anthropic.Anthropic(api_key=self.api_key).messages.create(
            model=self.model, max_tokens=max_tokens, system=system or "You are a helpful assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if hasattr(block, "text")).strip()
        input_tokens = int(getattr(response.usage, "input_tokens", 0))
        output_tokens = int(getattr(response.usage, "output_tokens", 0))
        # Sonnet 4.6 standard text estimate: $3/M input, $15/M output.
        estimated = input_tokens * 3 / 1_000_000 + output_tokens * 15 / 1_000_000
        return GenerationResult(text, self.name, self.model, input_tokens, output_tokens, estimated)


class AIService:
    def __init__(self, repository: WinstonRepository, providers: list[AIProvider], *, zero_cost_mode: bool = True) -> None:
        self.repository = repository
        self.providers = providers
        self.zero_cost_mode = zero_cost_mode

    @classmethod
    def from_environment(cls, repository: WinstonRepository) -> "AIService":
        enabled = os.getenv("WINSTON_ENABLE_CLAUDE", "false").strip().casefold() == "true"
        zero_cost = os.getenv("WINSTON_ZERO_COST_MODE", "true").strip().casefold() != "false"
        providers: list[AIProvider] = [
            GeminiProvider(os.getenv("GEMINI_API_KEY", ""), os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")),
            OllamaProvider(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"), os.getenv("OLLAMA_MODEL", "qwen3:8b")),
            ClaudeProvider(os.getenv("ANTHROPIC_KEY", ""), os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"), enabled),
        ]
        return cls(repository, providers, zero_cost_mode=zero_cost)

    def generate(self, prompt: str, *, system: str = "", max_tokens: int = 400,
                 purpose: str = "general") -> GenerationResult:
        errors = []
        for provider in self.providers:
            if not provider.available() or (self.zero_cost_mode and provider.paid):
                continue
            started = time.monotonic()
            try:
                result = provider.generate(prompt, system=system, max_tokens=max_tokens)
                latency = int((time.monotonic() - started) * 1000)
                self.repository.record_provider_usage(
                    provider=result.provider, model=result.model, purpose=purpose, success=True,
                    latency_ms=latency, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    estimated_cost_usd=result.estimated_cost_usd,
                )
                return result
            except Exception as exc:
                latency = int((time.monotonic() - started) * 1000)
                error = f"{type(exc).__name__}: {exc}"[:500]
                self.repository.record_provider_usage(provider=provider.name, model=provider.model,
                                                      purpose=purpose, success=False, latency_ms=latency, error=error)
                errors.append(f"{provider.name}: {exc}")
        mode = "zero-cost" if self.zero_cost_mode else "configured"
        raise ProviderError(f"No {mode} AI provider completed the request. " + "; ".join(errors))

    def status(self) -> dict:
        return {
            "mode": "zero-cost" if self.zero_cost_mode else "paid-opt-in",
            "providers": [{"name": p.name, "model": p.model, "available": p.available(),
                           "paid": p.paid, "eligible": p.available() and not (self.zero_cost_mode and p.paid)}
                          for p in self.providers],
            "usage": self.repository.provider_summary(),
        }
