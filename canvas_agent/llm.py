"""The ONLY module that imports the Anthropic SDK.

Everything else depends on the `LLMClient` protocol, so tests inject a fake
and never need a key or the network.
"""

from __future__ import annotations

import os
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = os.environ.get("CANVAS_AGENT_MODEL", "claude-opus-5-5")


class LLMRefusal(RuntimeError):
    """The model declined the request (stop_reason == 'refusal')."""


class LLMClient(Protocol):
    def structured(self, system: str, prompt: str, schema: type[T]) -> T: ...


class ClaudeClient:
    """Real client. Constructed only at the app edge, never in tests."""

    def __init__(self, model: str = DEFAULT_MODEL, client=None):
        if client is None:
            import anthropic  # imported lazily so the package loads without the SDK configured

            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env / .env
        self._client = client
        self.model = model

    def structured(self, system: str, prompt: str, schema: type[T]) -> T:
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
        if response.stop_reason == "refusal":
            raise LLMRefusal(str(getattr(response, "stop_details", None)))
        return response.parsed_output


GEMINI_DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")


class GeminiClient:
    """Gemini via the google-genai SDK. Reads GEMINI_API_KEY from the environment."""

    def __init__(self, model: str = GEMINI_DEFAULT_MODEL, client=None):
        if client is None:
            from google import genai  # lazy import, same reason as above

            client = genai.Client()  # picks up GEMINI_API_KEY / GOOGLE_API_KEY
        self._client = client
        self.model = model

    def structured(self, system: str, prompt: str, schema: type[T]) -> T:
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={
                "system_instruction": system,
                "response_mime_type": "application/json",
                "response_schema": schema,
            },
        )
        parsed = getattr(response, "parsed", None)
        if parsed is None:
            raise LLMRefusal("Gemini returned no parsable structured output.")
        return parsed if isinstance(parsed, schema) else schema.model_validate(parsed)


def make_client(provider: str | None = None) -> LLMClient:
    """Pick the provider from LLM_PROVIDER (gemini | anthropic). Default: gemini."""
    provider = (provider or os.environ.get("LLM_PROVIDER", "gemini")).lower()
    if provider == "gemini":
        return GeminiClient()
    if provider in ("anthropic", "claude"):
        return ClaudeClient()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")
