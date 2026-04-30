"""Runtime completion-evidence tracking for code/automation delivery claims.

This module is intentionally small and conservative: it records evidence from
actual tool results and evaluates whether a final assistant response is allowed
to claim code, automation, or integration work is done/fixed/working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Mapping


_MUTATING_TOOLS = {
    "patch",
    "write_file",
    "terminal",
    "execute_code",
    "cronjob",
    "skill_manage",
}

_READ_TOOLS = {"read_file", "search_files", "web_extract", "web_search"}

_COMPLETION_RE = re.compile(
    r"\b(done|fixed|complete(?:d)?|delivered|working|works|validated|end[- ]to[- ]end|e2e)\b",
    re.IGNORECASE,
)
_DELIVERY_NOUN_RE = re.compile(
    r"\b(code|automation|script|cron|job|service|integration|tool|feature|bug|fix|deployment|dashboard)\b",
    re.IGNORECASE,
)
_TEST_COMMAND_RE = re.compile(
    r"\b(pytest|unittest|tox|nox|npm\s+test|pnpm\s+test|yarn\s+test|cargo\s+test|go\s+test|make\s+test)\b",
    re.IGNORECASE,
)
_E2E_COMMAND_RE = re.compile(
    r"\b(e2e|end[- ]to[- ]end|dry[- ]run|smoke|health(check)?|curl|playwright|selenium|cypress|docker\s+run|hermes\s+cron\s+(run|list|status))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CompletionEvidenceDecision:
    """Result of evaluating a final response against recorded evidence."""

    allowed: bool
    status: str
    missing_gates: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class CompletionEvidenceTracker:
    """Collect evidence from tool results and gate delivery/completion claims."""

    mutation_seen: bool = False
    tests_passed: bool = False
    tests_failed: bool = False
    e2e_seen: bool = False
    artifact_read_seen: bool = False
    tool_events: list[dict[str, Any]] = field(default_factory=list)

    def record_tool_result(self, tool_name: str, args: Mapping[str, Any] | None, result: Any) -> None:
        """Record one executed tool result.

        The tracker trusts only observed tool calls/results, never claims in the
        assistant response. Terminal commands are classified from their command
        string plus exit status; file/search tools contribute artifact evidence.
        """

        args_dict = dict(args or {})
        result_text = _result_to_text(result)
        success = _result_success(result)
        event = {
            "tool_name": tool_name,
            "args": args_dict,
            "success": success,
            "result_preview": result_text[:500],
        }
        self.tool_events.append(event)

        if tool_name in _READ_TOOLS:
            if tool_name == "read_file" and success:
                self.artifact_read_seen = True
            return

        if tool_name in _MUTATING_TOOLS:
            if tool_name in {"patch", "write_file", "skill_manage", "cronjob"}:
                self.mutation_seen = True
            elif tool_name in {"terminal", "execute_code"}:
                command = str(args_dict.get("command") or args_dict.get("code") or "")
                lowered = command.lower()
                if _TEST_COMMAND_RE.search(command):
                    if success:
                        self.tests_passed = True
                    else:
                        self.tests_failed = True
                elif _E2E_COMMAND_RE.search(command):
                    if success:
                        self.e2e_seen = True
                elif _looks_mutating_command(lowered):
                    self.mutation_seen = True

    def evaluate_final_response(self, response_text: str) -> CompletionEvidenceDecision:
        """Return whether *response_text* may make its apparent completion claim."""

        response_text = response_text or ""
        claims_completion = bool(_COMPLETION_RE.search(response_text))
        claims_delivery = bool(_DELIVERY_NOUN_RE.search(response_text))
        applies = self.mutation_seen or (claims_completion and claims_delivery)

        if not applies:
            return CompletionEvidenceDecision(True, "not_applicable", [])

        if not claims_completion:
            return CompletionEvidenceDecision(True, "no_completion_claim", [])

        missing: list[str] = []
        if not self.tests_passed or self.tests_failed:
            missing.append("automated_tests")
        if not self.e2e_seen:
            missing.append("end_to_end_validation")
        if self.e2e_seen and not self.artifact_read_seen:
            missing.append("artifact_verification")

        if not missing:
            return CompletionEvidenceDecision(
                True,
                "validated_end_to_end",
                [],
                "completion claim backed by observed tests, E2E, and artifact read",
            )
        if missing == ["end_to_end_validation"]:
            status = "e2e_pending"
        elif missing == ["artifact_verification"]:
            status = "artifact_verification_pending"
        else:
            status = "blocked"
        return CompletionEvidenceDecision(False, status, missing)


def _result_success(result: Any) -> bool:
    if isinstance(result, Mapping):
        if "exit_code" in result:
            return result.get("exit_code") == 0
        if "success" in result:
            return bool(result.get("success"))
        if result.get("error"):
            return False
        return True
    text = _result_to_text(result).lower()
    if '"exit_code": 0' in text or "'exit_code': 0" in text:
        return True
    if '"exit_code": 1' in text or "'exit_code': 1" in text or "traceback" in text:
        return False
    return not ("failed" in text or "error" in text)


def _result_to_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, sort_keys=True, default=str)
    except TypeError:
        return str(result)


def _looks_mutating_command(command: str) -> bool:
    return bool(
        re.search(
            r"\b(git\s+(commit|push|merge|rebase|reset)|pip\s+install|npm\s+install|pnpm\s+install|uv\s+add|mkdir|rm\s+-|mv\s+|cp\s+|python\s+.*(--write|--fix))\b",
            command,
        )
    )
