"""Syllabus rules vs Canvas, per component. Synthetic data shaped like the real edge cases."""

from canvas_agent.component_check import check_components, map_items_to_components, syllabus_view_course
from canvas_agent.grades import grade_summary
from canvas_agent.reconcile import reconcile
from canvas_agent.syllabus_flow import ComponentRule, GradeComponent


def comp(name, w):
    return GradeComponent(name=name, weight_pct=w, chunk_id="c", source_quote=f"{name} {w}%")


def rule(component, kind, value=None, aggregation=None):
    return ComponentRule(component=component, kind=kind, value=value, aggregation=aggregation,
                         summary=kind, chunk_id="c", source_quote="q")


# 6 classes promised, Canvas has 5 exercises (Class 04 never created), 5 points each, drop lowest 2
EXERCISES = [[f"Class 0{i} Exercise", None, 5, "graded" if s is not None else "unsubmitted", s, False]
             for i, s in [(1, 0), (2, 5), (3, 4), (5, 0), (6, None)]]
COURSE = {"name": "Synthetic Law", "weighted": True, "omit_from_final": [], "groups": [
    {"name": "In-Class Exercises", "weight": 10, "rules": {"drop_lowest": 1}, "items": EXERCISES},
    {"name": "Midterm", "weight": 40, "rules": {}, "items": [["Midterm", None, 100, "graded", 80, False]]},
    {"name": "Final Exam", "weight": 50, "rules": {}, "items": []},
]}
COMPS = [comp("In-class exercises", 10), comp("Midterm exam", 40), comp("Final exam", 50)]
RULES = [rule("In-class exercises", "item_count", 6), rule("In-class exercises", "drop_lowest", 2),
         rule("In-class exercises", "completion_based")]


def checks():
    return {c.component: c for c in check_components(COMPS, RULES, COURSE, reconcile(COMPS, COURSE))}


def test_count_gap_drop_mismatch_and_completion_zeros_are_flagged():
    ex = checks()["In-class exercises"]
    assert ex.canvas_groups == ["In-Class Exercises"]
    assert (ex.expected_items, ex.items_in_canvas, ex.scored, ex.remaining_by_syllabus) == (6, 5, 4, 2)
    assert ex.numbering_gaps == [4]
    flags = " | ".join(ex.flags)
    assert "Syllabus says 6 items; Canvas has 5 (numbering gaps: [4])" in flags
    assert "Syllabus drops 2, Canvas is set to drop 1" in flags
    assert "2 zero score(s) on a completion-marked component" in flags


def test_component_average_under_both_rule_sets():
    ex = checks()["In-class exercises"]
    # scored: 0, 5, 4, 0 (of 5 each). Canvas drops 1 -> 9/15 = 60%. Syllabus drops 2 -> 9/10 = 90%.
    assert ex.avg_canvas_rules == 60.0
    assert ex.avg_syllabus_rules == 90.0
    dropped = {i.name for i in ex.items if i.dropped_syllabus}
    assert dropped == {"Class 01 Exercise", "Class 05 Exercise"}


def test_equal_weight_vs_points_when_items_differ_in_points():
    course = {"name": "Q", "weighted": True, "groups": [{"name": "Quizzes", "weight": 100, "rules": {}, "items": [
        ["Quiz 1", None, 10, "graded", 10, False], ["Quiz 2", None, 40, "graded", 20, False]]}]}
    comps = [comp("Quizzes", 100)]
    c = check_components(comps, [rule("Quizzes", "aggregation", aggregation="equal_weight")], course,
                         reconcile(comps, course))[0]
    # points: 30/50 = 60% ; equal weight: (100% + 50%) / 2 = 75%
    assert (c.avg_canvas_rules, c.avg_syllabus_rules) == (60.0, 75.0)
    assert any("weights items equally" in f for f in c.flags)


def test_syllabus_view_grade_adds_placeholders_for_items_not_yet_in_canvas():
    cks = list(checks().values())
    view = syllabus_view_course(COMPS, cks, COURSE)
    ex = view["groups"][0]
    assert len(ex["items"]) == 6 and ex["items"][-1][0].endswith("(not yet in Canvas) #1")
    assert ex["rules"] == {"drop_lowest": 2}
    s = grade_summary("SYN", view)
    # current: Ex 90% (w10) -> 9 ; Midterm 80% (w40) -> 32 ; (9 + 32) / 50 = 82%
    assert s.current_pct == 82.0
    # floor: Ex scored 0,5,4,0 + two remaining at 0 -> drop two zeros -> 9/20 = 45% -> 4.5 ; + 32 + 0 = 36.5
    assert s.floor_pct == 36.5


def test_unweighted_course_items_are_mapped_or_left_out_never_guessed():
    course = {"name": "Comms", "weighted": False, "omit_from_final": [], "groups": [{"name": "Assignments", "weight": 0, "items": [
        ["Market Research Assignment", None, 50, "graded", 40, False],
        ["Career Vision Speech", None, 70, "unsubmitted", None, False],
        ["Syllabus Quiz", None, 13, "graded", 13, False],
        ["Group Pitch Presentation", None, 70, "graded", 54, False],
    ]}]}
    comps = [comp("Market Research Proposal Assignment", 20), comp("Career Vision Presentation", 20),
             comp("Group Pitch Presentation", 15)]
    m = {x.item: x for x in map_items_to_components(comps, course)}
    assert m["Market Research Assignment"].component == "Market Research Proposal Assignment"
    assert m["Career Vision Speech"].component == "Career Vision Presentation"
    assert m["Group Pitch Presentation"].confidence == "high"
    assert m["Syllabus Quiz"].confidence == "unmapped" and m["Syllabus Quiz"].component is None


def _course_with_modules():
    c = dict(COURSE)
    c["modules"] = [["Week A", ["P:Class 01: Intro", "A:Class 01 Exercise", "P:Class 02: Sources", "A:Class 02 Exercise",
                                "P:Class 03: Courts", "A:Class 03 Exercise"]],
                    ["Week B", ["P:Class 04: Exclusion Clauses: Prep",  # class with no exercise
                                "P:Class 05: Sale", "A:Class 05 Exercise", "P:Class 06: Torts", "A:Class 06 Exercise"]]]
    return c


def test_session_count_comes_from_canvas_modules_with_provenance():
    from canvas_agent.component_check import count_sessions

    s = count_sessions(_course_with_modules())
    assert (s.total, s.with_assessed_item) == (6, [1, 2, 3, 5, 6])
    course = _course_with_modules()
    ex = {c.component: c for c in check_components(COMPS, [rule("In-class exercises", "drop_lowest", 2)], course,
                                                   reconcile(COMPS, course))}["In-class exercises"]
    assert ex.expected_items == 5 and ex.sessions_total == 6
    assert ex.expected_source.startswith("counted by Python from Canvas Modules")
    flags = " | ".join(ex.flags)
    assert "No item for class(es) [4]" in flags and "by design" in flags
    assert "Syllabus says" not in flags  # 5 expected, 5 in Canvas: consistent once the design gap is explained


def test_stated_count_beats_the_module_count():
    course = _course_with_modules()
    ex = {c.component: c for c in check_components(COMPS, RULES, course, reconcile(COMPS, course))}["In-class exercises"]
    assert ex.expected_items == 6 and ex.expected_source.startswith("syllabus")


def test_attendance_check_compares_possible_absences_with_syllabus_thresholds():
    from canvas_agent.component_check import attendance_check

    course = _course_with_modules()
    cks = check_components(COMPS, RULES, course, reconcile(COMPS, course))
    quotes = ["Students who miss more than 15% of classes will be contacted.",
              "missing more than 20% of classes may be excluded from the final exam",
              "Late submissions are not accepted."]
    a = attendance_check(cks, quotes, course)
    assert a.thresholds_pct == [15.0, 20.0]
    assert a.possible_absences == ["Class 01 Exercise", "Class 05 Exercise"]
    assert a.possible_absence_pct == 33.3  # 2 of 6 classes
    assert a.exceeds == [15.0, 20.0]
    assert "wrong or incomplete answer" in a.caveat


def test_class_counts_as_covered_when_canvas_has_its_item_even_if_modules_do_not_list_it():
    course = _course_with_modules()
    # Module for class 6 lists no assignment, but Canvas has "Class 06 Exercise": still covered.
    course["modules"] = [[n, [i for i in items if i != "A:Class 06 Exercise"]] for n, items in course["modules"]]
    ex = {c.component: c for c in check_components(COMPS, [], course, reconcile(COMPS, course))}["In-class exercises"]
    assert ex.expected_items == 5  # 6 classes, only class 4 has no item anywhere


def test_exam_date_hints_find_dates_the_extraction_missed():
    from canvas_agent.component_check import exam_date_hints

    exams = [{"kind": "midterm", "exam_date": None}, {"kind": "final", "exam_date": "2026-12-21"},
             {"kind": "oral", "exam_date": None}]
    text = ("The midterm will be held on the assigned date. Midterm Examination Date: October 29, 7-8:30pm. "
            "Week of Nov. 16: Viva Voce (Oral Examination). Final Examination: December 21.")
    course = {"modules": [["FINAL EXAM", ["S:FINAL EXAM: Saturday, Dec 12, 2026, 12:00 p.m. ROOM TBA"]]]}
    hints = {(h.kind, h.found_date, h.source) for h in exam_date_hints(exams, text, course, year=2026)}
    assert ("midterm", "2026-10-29", "syllabus") in hints
    assert ("oral", "2026-11-16", "syllabus") in hints
    assert not any(k == "final" for k, _, _ in hints)  # final already has a date: no hint needed


def test_duplicate_rules_are_kept_once():
    from canvas_agent.retrieval import Chunk
    from canvas_agent.syllabus_flow import ComponentRules, validate_rules

    chunks = [Chunk(id="X-p1-c1", page=1, text="Your two lowest scores will be dropped.")]
    r = rule("In-class exercises", "drop_lowest", 2).model_copy(update={"chunk_id": "X-p1-c1",
                                                                      "source_quote": "Your two lowest scores will be dropped."})
    kept, _ = validate_rules(ComponentRules(rules=[r, r]), chunks, [comp("In-class exercises", 10)])
    assert len(kept) == 1


def test_confirmed_absences_replace_the_zero_score_guess_and_show_limits():
    from canvas_agent.component_check import attendance_check

    course = _course_with_modules()
    cks = check_components(COMPS, RULES, course, reconcile(COMPS, course))
    quotes = ["Students who miss more than 15% of classes will be contacted.",
              "missing more than 20% of classes may be excluded from the final exam"]
    a = attendance_check(cks, quotes, course, confirmed_absences=["Class 01 Exercise"])
    assert a.status == "confirmed" and a.possible_absences == ["Class 01 Exercise"]
    assert a.possible_absence_pct == 16.7  # 1 of 6
    assert a.allowed_absences == {"15": 0, "20": 1}  # 15% of 6 = 0.9 -> 0 allowed; 20% of 6 = 1.2 -> 1 allowed
    assert a.exceeds == [15.0]
    assert (a.sessions_held, a.sessions_remaining) == (5, 1)  # last class with a released mark is Class 05
    assert a.caveat == "Confirmed by you."
