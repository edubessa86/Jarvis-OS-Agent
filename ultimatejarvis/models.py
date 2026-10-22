"""Provider-neutral model gateway with local-first ordering and bounded requests.

No model SDK is imported by this module. Providers are injected explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class ModelResponse:
    text: str
    provider: str
    model: str
    is_local: bool


class ModelProvider(Protocol):
    name: str
    model: str
    is_local: bool

    def generate(self, prompt: str, *, timeout_seconds: float, max_output_tokens: int) -> str:
        """Generate text or raise an exception on failure."""


class ModelGatewayError(RuntimeError):
    """Raised when no configured provider can answer the request."""


class ModelGateway:
    """Try local providers first, then optional remote providers as fallback."""

    def __init__(
        self,
        providers: Sequence[ModelProvider] = (),
        *,
        max_prompt_chars: int = 20_000,
        max_output_tokens: int = 512,
        timeout_seconds: float = 45.0,
    ) -> None:
        if max_prompt_chars < 1 or max_output_tokens < 1 or timeout_seconds <= 0:
            raise ValueError("Gateway limits must be positive")
        self._providers = tuple(sorted(providers, key=lambda provider: not provider.is_local))
        self.max_prompt_chars = max_prompt_chars
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds

    def generate(self, prompt: str, *, max_output_tokens: int | None = None) -> ModelResponse:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be a non-empty string")
        if len(prompt) > self.max_prompt_chars:
            raise ValueError("Prompt exceeds the configured size limit")
        requested_tokens = self.max_output_tokens if max_output_tokens is None else max_output_tokens
        if requested_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        token_limit = min(requested_tokens, self.max_output_tokens)

        failures: list[str] = []
        for provider in self._providers:
            try:
                text = provider.generate(
                    prompt,
                    timeout_seconds=self.timeout_seconds,
                    max_output_tokens=token_limit,
                )
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Provider returned an empty response")
                return ModelResponse(
                    text=text,
                    provider=provider.name,
                    model=provider.model,
                    is_local=provider.is_local,
                )
            except Exception as exc:  # provider failures must not crash the orchestrator
                failures.append(f"{provider.name}: {type(exc).__name__}")

        details = "; ".join(failures) if failures else "No providers are configured"
        raise ModelGatewayError(details)
