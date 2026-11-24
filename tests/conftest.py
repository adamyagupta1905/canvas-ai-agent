from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "courses"


class FakeLLM:
    """Returns a canned object for the requested schema; records every call."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def structured(self, system, prompt, schema):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        return self.response


@pytest.fixture
def fixtures_root():
    return FIXTURES
