"""Agents + tools on synthetic data with a scripted fake model. No network, no key."""

import json
from datetime import datetime, timezone

from canvas_agent.agents import AGENTS, AgentStep, Route, ask, ungrounded_numbers
from canvas_agent.tools import Store, build_tools

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
COURSE = {"name": "Synthetic Law", "weighted": True, "omit_from_final": [], "groups": [
    {"name": "Exercises", "weight": 10, "rules": {}, "items": [
        ["Class 01 Exercise", "2026-09-10T05:00:00Z", 5, "graded", 5, False],
        ["Class 02 Exercise", "2026-11-05T05:00:00Z", 5, "unsubmitted", None, False]]},
    {"name": "Midterm", "weight": 40, "rules": {}, "items": [["Midterm", None, 100, "graded", 80, False]]},
    {"name": "Final", "weight": 50, "rules": {}, "items": []}]}


class ScriptedLLM:
    """Returns the scripted responses in order; records prompts."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def structured(self, system, prompt, schema):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        r = self.responses.pop(0)
        assert isinstance(r, schema), f"expected {schema.__name__}, got {type(r).__name__}"
        return r


def store(tmp_path):
    (tmp_path / "canvas_snapshot.json").write_text(json.dumps({"courses": {"LAW101": COURSE}}))
    return Store(tmp_path)


def test_tools_are_deterministic_python(tmp_path):
    tools = build_tools(store(tmp_path), now=NOW)
    g = tools["course_grade"].fn("LAW101")
    # Exercises 100% (w10) + Midterm 80% (w40) -> (10 + 32) / 50 = 84%
    assert g["current_pct"] == 84.0 and g["basis"].startswith("Canvas setup")
    n = tools["needed_for_target"].fn("LAW101", 80)
    # grade(x) = (5 + 5x)/10*10 + 32 + 50x = 37 + 55x ; 80 -> x = 0.7818
    assert abs(n["needed_avg_pct_on_remaining"] - 78.18) < 0.01 and n["reachable"]
    up = tools["upcoming"].fn("LAW101", days=14)
    assert [u["item"] for u in up] == ["Class 02 Exercise"]


def test_agent_calls_a_tool_then_answers_with_grounded_numbers(tmp_path):
    tools = build_tools(store(tmp_path), now=NOW)
    llm = ScriptedLLM(
        Route(agent="grades", course_code="LAW101", reason="grade question"),
        AgentStep(action="call_tool", tool="course_grade", args_json='{"code": "LAW101"}'),
        AgentStep(action="answer", answer="Your current grade in LAW101 is 84%, with 50% of the course graded."),
    )
    r = ask("What's my grade in LAW101?", llm, tools)
    assert r.agent == "grades" and r.steps == 2
    assert r.trace[0]["tool"] == "router" and r.trace[1]["tool"] == "course_grade"
    assert r.ungrounded_numbers == []
    assert "every number you state must come from a tool result" in llm.calls[1]["system"]
    assert '"current_pct": 84.0' in llm.calls[2]["prompt"]  # tool result fed back to the model


def test_made_up_numbers_are_flagged(tmp_path):
    tools = build_tools(store(tmp_path), now=NOW)
    llm = ScriptedLLM(
        Route(agent="grades", course_code="LAW101", reason="x"),
        AgentStep(action="call_tool", tool="course_grade", args_json='{"code": "LAW101"}'),
        AgentStep(action="answer", answer="You have 84% now and will probably finish around 88%."),
    )
    assert ask("grade?", llm, tools).ungrounded_numbers == ["88"]


def test_agents_only_get_their_own_tools_and_bad_calls_return_errors(tmp_path):
    tools = build_tools(store(tmp_path), now=NOW)
    llm = ScriptedLLM(
        AgentStep(action="call_tool", tool="needed_for_target", args_json='{"code": "LAW101", "target_pct": 80}'),
        AgentStep(action="call_tool", tool="course_grade", args_json='{"code": "NOPE"}'),
        AgentStep(action="answer", answer="I can't answer that here."),
    )
    r = AGENTS["content"].run("anything", llm, tools)
    assert "not available to this agent" in r.trace[0]["result"]["error"]
    assert "not available" in r.trace[1]["result"]["error"]  # course_grade is not a content tool either


def test_unknown_course_error_reaches_the_model(tmp_path):
    tools = build_tools(store(tmp_path), now=NOW)
    llm = ScriptedLLM(AgentStep(action="call_tool", tool="course_grade", args_json='{"code": "NOPE"}'),
                      AgentStep(action="answer", answer="I don't see that course."))
    r = AGENTS["grades"].run("grade in NOPE?", llm, tools)
    assert "Unknown course" in r.trace[0]["result"]["error"]


def test_step_limit_stops_runaway_loops(tmp_path):
    tools = build_tools(store(tmp_path), now=NOW)
    step = AgentStep(action="call_tool", tool="list_courses", args_json="{}")
    r = AGENTS["grades"].run("loop", ScriptedLLM(*[step] * 5), tools)
    assert r.steps == 5 and "step limit" in r.answer


def test_rounded_numbers_and_course_codes_count_as_grounded():
    trace = [{"tool": "course_grade", "args": {}, "result": {"current_pct": 72.38}}]
    assert ungrounded_numbers("About 72.4% (72.38%) in LAW201", trace, "LAW201?") == []


def test_every_agent_tool_exists_and_roster_is_small(tmp_path):
    tools = build_tools(store(tmp_path), now=NOW)
    assert set(AGENTS) == {"grades", "planner", "content", "rules"}
    for a in AGENTS.values():
        assert set(a.tool_names) <= set(tools), (a.name, set(a.tool_names) - set(tools))


def test_planner_tools_return_views(tmp_path):
    tools = build_tools(store(tmp_path), now=NOW)
    assert tools["course_progress"].fn("LAW101")["grade"]["current_pct"] == 84.0
    assert [d["what"] for d in tools["deadlines"].fn("LAW101")] == ["Class 02 Exercise"]
    assert tools["study_plan"].fn("LAW101")["days"][0]["tasks"][0]["task"] == "Work on: Class 02 Exercise"
