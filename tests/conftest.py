from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "courses"


class FakeLLM:
    """Returns a canned object; pass a {schema_class: object} dict to answer per schema. Records every call."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def structured(self, system, prompt, schema):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        if isinstance(self.response, dict):
            return self.response[schema]
        return self.response


@pytest.fixture
def fixtures_root():
    return FIXTURES
