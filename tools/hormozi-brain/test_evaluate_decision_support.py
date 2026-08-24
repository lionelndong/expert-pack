import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("evaluate_decision_support.py")
SPEC = importlib.util.spec_from_file_location("hormozi_decision_eval", MODULE_PATH)
assert SPEC and SPEC.loader
EVALUATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATOR)


def test_contract_spec_has_twenty_cases():
    cases = EVALUATOR.load_spec(Path(__file__).parents[2] / "config" / "decision-support-evaluations.yaml")
    assert len(cases) == 20


def test_missing_response_is_explicitly_pending(tmp_path):
    spec = Path(__file__).parents[2] / "config" / "decision-support-evaluations.yaml"
    report = EVALUATOR.evaluate(spec, None, tmp_path / "report.json")
    assert report["overall_status"] == "pending_agent_responses"
    assert report["expected_cases"] == 20
    assert report["evaluated_cases"] == 0


def test_case_checks_required_markers():
    case = {
        "id": "case-1",
        "expect": {
            "disposition": "answer",
            "must_include": ["Sourced", "source-1", "p. 4"],
            "must_not_include": ["I am Alex"],
        },
    }
    result = EVALUATOR.evaluate_case(case, {"id": "case-1", "disposition": "answer", "response": "Sourced: source-1 — p. 4"})
    assert result["status"] == "pass"
