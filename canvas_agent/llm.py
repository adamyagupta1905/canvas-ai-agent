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
