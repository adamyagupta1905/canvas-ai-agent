from types import SimpleNamespace

import pytest

from canvas_agent.llm import ClaudeClient, LLMRefusal
from canvas_agent.models import SyllabusExtraction


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _client(response):
    msgs = FakeMessages(response)
    return ClaudeClient(client=SimpleNamespace(messages=msgs)), msgs


def test_structured_passes_schema_and_returns_parsed():
    parsed = SyllabusExtraction(exams=[])
    llm, msgs = _client(SimpleNamespace(stop_reason="end_turn", parsed_output=parsed))
    assert llm.structured("sys", "hi", SyllabusExtraction) is parsed
    assert msgs.kwargs["output_format"] is SyllabusExtraction
    assert msgs.kwargs["model"] == llm.model


def test_refusal_raises():
    llm, _ = _client(SimpleNamespace(stop_reason="refusal", stop_details=None, parsed_output=None))
    with pytest.raises(LLMRefusal):
        llm.structured("sys", "hi", SyllabusExtraction)
