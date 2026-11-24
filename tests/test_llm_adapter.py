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


class FakeModels:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_gemini_structured_passes_schema_and_returns_parsed():
    from canvas_agent.llm import GeminiClient

    parsed = SyllabusExtraction(exams=[])
    models = FakeModels(SimpleNamespace(parsed=parsed))
    llm = GeminiClient(client=SimpleNamespace(models=models))
    assert llm.structured("sys", "hi", SyllabusExtraction) is parsed
    assert models.kwargs["config"]["response_schema"] is SyllabusExtraction
    assert models.kwargs["config"]["system_instruction"] == "sys"


def test_gemini_without_parsed_output_raises():
    from canvas_agent.llm import GeminiClient

    llm = GeminiClient(client=SimpleNamespace(models=FakeModels(SimpleNamespace(parsed=None))))
    with pytest.raises(LLMRefusal):
        llm.structured("sys", "hi", SyllabusExtraction)


def test_make_client_rejects_unknown_provider():
    from canvas_agent.llm import make_client

    with pytest.raises(ValueError):
        make_client("mystery")
