"""Tests for completion-evidence classification and gating."""

from agent.completion_evidence import CompletionEvidenceTracker


def test_mutation_completion_claim_without_tests_or_e2e_is_blocked():
    tracker = CompletionEvidenceTracker()
    tracker.record_tool_result("write_file", {"path": "app.py"}, {"success": True})

    decision = tracker.evaluate_final_response("Done — the automation is working now.")

    assert decision.allowed is False
    assert decision.status == "blocked"
    assert "automated_tests" in decision.missing_gates
    assert "end_to_end_validation" in decision.missing_gates


def test_tests_without_e2e_downgrades_completion_claim():
    tracker = CompletionEvidenceTracker()
    tracker.record_tool_result("patch", {"path": "app.py"}, "diff --git ...")
    tracker.record_tool_result("terminal", {"command": "pytest tests/test_app.py -q"}, {"exit_code": 0, "output": "1 passed"})

    decision = tracker.evaluate_final_response("Fixed and working.")

    assert decision.allowed is False
    assert decision.status == "e2e_pending"
    assert decision.missing_gates == ["end_to_end_validation"]


def test_failed_tests_do_not_count_as_evidence():
    tracker = CompletionEvidenceTracker()
    tracker.record_tool_result("write_file", {"path": "app.py"}, {"success": True})
    tracker.record_tool_result("terminal", {"command": "pytest tests/test_app.py -q"}, {"exit_code": 1, "output": "FAILED"})
    tracker.record_tool_result("terminal", {"command": "python app.py --dry-run"}, {"exit_code": 0, "output": "OK"})

    decision = tracker.evaluate_final_response("Done, fixed, and validated end-to-end.")

    assert decision.allowed is False
    assert "automated_tests" in decision.missing_gates
    assert "end_to_end_validation" not in decision.missing_gates


def test_full_tests_e2e_and_artifact_read_allows_completion_claim():
    tracker = CompletionEvidenceTracker()
    tracker.record_tool_result("patch", {"path": "app.py"}, "diff --git ...")
    tracker.record_tool_result("terminal", {"command": "pytest tests/test_app.py -q"}, {"exit_code": 0, "output": "1 passed"})
    tracker.record_tool_result("terminal", {"command": "python app.py --dry-run"}, {"exit_code": 0, "output": "OK"})
    tracker.record_tool_result("read_file", {"path": "output.json"}, {"content": "{\"ok\": true}"})

    decision = tracker.evaluate_final_response("Validated end-to-end: done and working.")

    assert decision.allowed is True
    assert decision.status == "validated_end_to_end"
    assert decision.missing_gates == []


def test_non_delivery_chat_is_allowed_without_evidence():
    tracker = CompletionEvidenceTracker()

    decision = tracker.evaluate_final_response("The capital of France is Paris.")

    assert decision.allowed is True
    assert decision.status == "not_applicable"
    assert decision.missing_gates == []


def test_explicit_evidence_cannot_invent_unseen_commands():
    tracker = CompletionEvidenceTracker()
    tracker.record_tool_result("write_file", {"path": "app.py"}, {"success": True})

    decision = tracker.evaluate_final_response(
        "Done. Evidence: pytest passed and E2E dry-run passed."
    )

    assert decision.allowed is False
    assert "automated_tests" in decision.missing_gates
    assert "end_to_end_validation" in decision.missing_gates
