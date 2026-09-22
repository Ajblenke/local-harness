"""Fail-closed controller for the three-party continuation contract."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

WORK_ORDER_SCHEMA = "local-harness/work-order/v1"
COMPLETION_SCHEMA = "local-harness/completion/v1"
OBSERVATION_SCHEMA = "local-harness/observation/v1"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class Decision:
    outcome: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "reasons": list(self.reasons)}


def load_document(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def document_sha256(document: dict[str, Any]) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _named_statuses(value: Any, statuses: set[str]) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item.get("name")
        and item.get("status") in statuses
        for item in value
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_git_oid(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) in {40, 64}
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_claims(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item, dict)
            and set(item) == {"statement", "evidence_refs"}
            and isinstance(item.get("statement"), str)
            and bool(item["statement"])
            and _strings(item.get("evidence_refs"))
            and bool(item["evidence_refs"])
            for item in value
        )
    )


def _valid_risks(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and set(item) == {"level", "detail"}
        and item.get("level") in {"low", "medium", "high", "critical"}
        and isinstance(item.get("detail"), str)
        and bool(item["detail"])
        for item in value
    )


def _valid_changes(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(change, dict)
        and set(change) == {"path", "kind"}
        and isinstance(change.get("path"), str)
        and bool(change["path"])
        and change.get("kind") in {"added", "modified", "deleted", "renamed"}
        for change in value
    )


def _unexpected(document: dict[str, Any], allowed: set[str]) -> list[str]:
    return sorted(set(document) - allowed)


def _safe_relative(path: str) -> bool:
    parsed = PurePosixPath(path)
    return bool(path) and "\0" not in path and not parsed.is_absolute() and ".." not in parsed.parts


def validate_work_order(work: dict[str, Any]) -> list[str]:
    """Validate a work order before any actuator or observer side effect."""
    problems: list[str] = []
    if work.get("schema") != WORK_ORDER_SCHEMA:
        problems.append("work order: unsupported or missing schema")
    if not isinstance(work.get("run_id"), str) or not RUN_ID_PATTERN.fullmatch(work["run_id"]):
        problems.append("work order: invalid run_id")
    fields = _unexpected(
        work,
        {
            "schema",
            "run_id",
            "objective",
            "base_commit",
            "scope",
            "required_checks",
            "limits",
            "approval",
            "next_step",
        },
    )
    if fields:
        problems.append(f"work order: unexpected fields {fields}")
    for key in ("objective", "base_commit", "next_step"):
        if not isinstance(work.get(key), str) or not work[key]:
            problems.append(f"work order: missing {key}")
    if not _is_git_oid(work.get("base_commit")):
        problems.append("work order: base_commit must be a full Git object id")
    scope = work.get("scope")
    if (
        not isinstance(scope, dict)
        or set(scope) != {"read", "write", "deny"}
        or not all(_strings(scope.get(key)) for key in ("read", "write", "deny"))
        or not scope.get("read")
        or not scope.get("write")
        or not all(_safe_relative(pattern) for key in ("read", "write", "deny") for pattern in scope[key])
    ):
        problems.append("work order: scope must contain non-empty read/write and a deny string array")
    if not _strings(work.get("required_checks")) or not work["required_checks"]:
        problems.append("work order: required_checks must be a string array")
    elif len(work["required_checks"]) != len(set(work["required_checks"])):
        problems.append("work order: duplicate required checks")
    limits = work.get("limits")
    if (
        not isinstance(limits, dict)
        or set(limits) != {"min_changed_files", "max_changed_files", "max_tool_calls", "max_wall_seconds"}
        or not all(
            isinstance(limits.get(key), int)
            and not isinstance(limits[key], bool)
            and limits[key] >= (0 if key == "min_changed_files" else 1)
            for key in ("min_changed_files", "max_changed_files", "max_tool_calls", "max_wall_seconds")
        )
        or (
            isinstance(limits, dict)
            and isinstance(limits.get("min_changed_files"), int)
            and isinstance(limits.get("max_changed_files"), int)
            and limits["min_changed_files"] > limits["max_changed_files"]
        )
    ):
        problems.append("work order: invalid limits")
    if work.get("approval") != "human":
        problems.append("work order: v1 requires human approval")
    return problems


def _validate_documents(
    work: dict[str, Any], completion: dict[str, Any], observation: dict[str, Any]
) -> list[str]:
    problems = validate_work_order(work)
    for label, document, schema in (
        ("completion", completion, COMPLETION_SCHEMA),
        ("observation", observation, OBSERVATION_SCHEMA),
    ):
        if document.get("schema") != schema:
            problems.append(f"{label}: unsupported or missing schema")
        if not isinstance(document.get("run_id"), str) or not RUN_ID_PATTERN.fullmatch(document["run_id"]):
            problems.append(f"{label}: invalid run_id")

    unexpected = {
        "completion": _unexpected(
            completion,
            {
                "schema",
                "run_id",
                "work_order_sha256",
                "agent_id",
                "status",
                "summary",
                "changes",
                "checks",
                "claims",
                "risks",
                "uncertainties",
                "requested_action",
            },
        ),
        "observation": _unexpected(
            observation,
            {
                "schema",
                "run_id",
                "work_order_sha256",
                "runner_id",
                "base_commit",
                "changes",
                "checks",
                "checks_isolated",
                "sandbox",
                "sandbox_violations",
                "telemetry_ref",
                "telemetry_complete",
                "evidence",
                "tool_calls",
                "wall_seconds",
            },
        ),
    }
    for label, fields in unexpected.items():
        if fields:
            problems.append(f"{label}: unexpected fields {fields}")

    changes = completion.get("changes")
    if not _valid_changes(changes):
        problems.append("completion: invalid changes")
    elif len({change["path"] for change in changes}) != len(changes):
        problems.append("completion: duplicate changed paths")
    if not _named_statuses(completion.get("checks"), {"pass", "fail", "skipped"}) or not all(
        set(check) == {"name", "status"} for check in completion.get("checks", [])
    ):
        problems.append("completion: invalid checks")
    elif len({check["name"] for check in completion["checks"]}) != len(completion["checks"]):
        problems.append("completion: duplicate checks")
    if not _valid_claims(completion.get("claims")):
        problems.append("completion: invalid claims")
    if not _valid_risks(completion.get("risks")):
        problems.append("completion: invalid risks")
    if not _strings(completion.get("uncertainties")):
        problems.append("completion: uncertainties must be a string array")
    if completion.get("status") not in {"completed", "blocked", "needs_review"}:
        problems.append("completion: invalid status")
    if completion.get("requested_action") not in {"advance", "double_check"}:
        problems.append("completion: invalid requested_action")
    for key in ("work_order_sha256", "agent_id", "summary"):
        if not isinstance(completion.get(key), str) or not completion[key]:
            problems.append(f"completion: missing {key}")
    if not _is_sha256(completion.get("work_order_sha256")):
        problems.append("completion: invalid work_order_sha256")

    observed_changes = observation.get("changes")
    if not _valid_changes(observed_changes):
        problems.append("observation: invalid changes")
    elif len({change["path"] for change in observed_changes}) != len(observed_changes):
        problems.append("observation: duplicate changed paths")
    if not _named_statuses(observation.get("checks"), {"pass", "fail"}):
        problems.append("observation: invalid checks")
    else:
        names = [check["name"] for check in observation["checks"]]
        if len(names) != len(set(names)):
            problems.append("observation: duplicate checks")
        for check in observation["checks"]:
            if set(check) != {"name", "status", "exit_code", "output_sha256", "output_ref"}:
                problems.append("observation: invalid check fields")
            if not isinstance(check.get("exit_code"), int) or isinstance(check.get("exit_code"), bool):
                problems.append("observation: check exit_code must be an integer")
            elif (check["status"] == "pass") != (check["exit_code"] == 0):
                problems.append("observation: check status contradicts exit_code")
            digest = check.get("output_sha256")
            if not _is_sha256(digest):
                problems.append("observation: check output digest is invalid")
            if (
                not isinstance(check.get("output_ref"), str)
                or not check["output_ref"]
                or not _safe_relative(check["output_ref"])
            ):
                problems.append("observation: check output_ref is invalid")
    if not isinstance(observation.get("checks_isolated"), bool):
        problems.append("observation: checks_isolated must be a boolean")
    if not _strings(observation.get("sandbox_violations")):
        problems.append("observation: sandbox_violations must be a string array")
    sandbox = observation.get("sandbox")
    if (
        not isinstance(sandbox, dict)
        or set(sandbox) != {"kind", "manifest_sha256", "network", "read_scope_enforced"}
        or sandbox.get("kind") not in {"docker", "host"}
        or sandbox.get("network") not in {"internal", "none", "host"}
        or not isinstance(sandbox.get("read_scope_enforced"), bool)
        or (sandbox.get("manifest_sha256") is not None and not _is_sha256(sandbox.get("manifest_sha256")))
    ):
        problems.append("observation: invalid sandbox evidence")
    elif sandbox["kind"] == "docker":
        digest = sandbox["manifest_sha256"]
        if not _is_sha256(digest):
            problems.append("observation: invalid sandbox manifest digest")
    if not _strings(observation.get("evidence")) or not observation["evidence"]:
        problems.append("observation: evidence must be a string array")
    elif len(observation["evidence"]) != len(set(observation["evidence"])):
        problems.append("observation: duplicate evidence")
    if (
        not isinstance(observation.get("tool_calls"), int)
        or isinstance(observation.get("tool_calls"), bool)
        or observation["tool_calls"] < 0
    ):
        problems.append("observation: tool_calls must be a non-negative integer")
    if (
        not isinstance(observation.get("wall_seconds"), (int, float))
        or isinstance(observation.get("wall_seconds"), bool)
        or observation["wall_seconds"] < 0
    ):
        problems.append("observation: wall_seconds must be non-negative")
    if not isinstance(observation.get("telemetry_ref"), str) or not observation.get("telemetry_ref"):
        problems.append("observation: missing telemetry_ref")
    if not isinstance(observation.get("telemetry_complete"), bool):
        problems.append("observation: telemetry_complete must be a boolean")
    for key in ("work_order_sha256", "runner_id", "base_commit"):
        if not isinstance(observation.get(key), str) or not observation[key]:
            problems.append(f"observation: missing {key}")
    if not _is_sha256(observation.get("work_order_sha256")):
        problems.append("observation: invalid work_order_sha256")
    return problems


def _matches(path: str, patterns: list[str]) -> bool:
    # fnmatch treats ``**/`` as requiring at least one slash, unlike pathlib's
    # glob semantics. A deny such as ``**/.env`` must also protect a root file.
    return any(
        fnmatch.fnmatchcase(path, pattern)
        or (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]))
        for pattern in patterns
    )


def evaluate_contract(
    work: dict[str, Any], completion: dict[str, Any], observation: dict[str, Any]
) -> Decision:
    """Return advance only when agent claims and runner evidence independently agree."""
    malformed = _validate_documents(work, completion, observation)
    if malformed:
        return Decision("double_check", tuple(malformed))

    run_ids = {work["run_id"], completion["run_id"], observation["run_id"]}
    if len(run_ids) != 1:
        return Decision("halt", ("run identity mismatch",))
    expected_hash = document_sha256(work)
    if completion["work_order_sha256"] != expected_hash or observation["work_order_sha256"] != expected_hash:
        return Decision("halt", ("work-order digest mismatch",))
    if observation["base_commit"] != work["base_commit"]:
        return Decision("halt", ("base commit mismatch",))
    if completion["agent_id"] == observation["runner_id"]:
        return Decision("halt", ("agent and independent runner identities match",))

    observed_changes = {(change["path"], change["kind"]) for change in observation["changes"]}
    claimed_changes = {(change["path"], change["kind"]) for change in completion["changes"]}
    observed_paths = {path for path, _kind in observed_changes}
    bad_paths = sorted(path for path in observed_paths if not _safe_relative(path))
    if bad_paths:
        return Decision("halt", (f"unsafe changed paths: {bad_paths}",))

    scope = work["scope"]
    denied = sorted(path for path in observed_paths if _matches(path, scope["deny"]))
    outside = sorted(path for path in observed_paths if not _matches(path, scope["write"]))
    if observation["sandbox_violations"]:
        return Decision("halt", ("sandbox violation observed", *observation["sandbox_violations"]))
    if denied:
        return Decision("halt", (f"denied paths changed: {denied}",))
    if outside:
        return Decision("halt", (f"paths outside write scope: {outside}",))
    if len(observed_paths) > work["limits"]["max_changed_files"]:
        return Decision("halt", ("changed-file limit exceeded",))
    if observation["tool_calls"] > work["limits"]["max_tool_calls"]:
        return Decision("halt", ("tool-call limit exceeded",))
    if observation["wall_seconds"] > work["limits"]["max_wall_seconds"]:
        return Decision("halt", ("wall-time limit exceeded",))

    observed_checks = {check["name"]: check["status"] for check in observation["checks"]}
    claimed_checks = {check["name"]: check["status"] for check in completion["checks"]}
    failed = sorted(name for name in work["required_checks"] if observed_checks.get(name) == "fail")
    if failed:
        return Decision("halt", (f"required checks failed: {failed}",))
    missing = sorted(
        name
        for name in work["required_checks"]
        if observed_checks.get(name) != "pass" or claimed_checks.get(name) != "pass"
    )

    reasons: list[str] = []
    if len(observed_paths) < work["limits"]["min_changed_files"]:
        reasons.append("minimum changed-file count not met")
    if observed_changes != claimed_changes:
        reasons.append("claimed changes do not match observation")
    if missing:
        reasons.append(f"required checks lack matching pass evidence: {missing}")
    if completion["status"] != "completed":
        reasons.append(f"agent status is {completion['status']}")
    if completion["requested_action"] != "advance":
        reasons.append("agent requested a double-check")
    if completion["uncertainties"]:
        reasons.append("agent reported unresolved uncertainty")
    if not observation["telemetry_complete"]:
        reasons.append("runner telemetry is incomplete")
    if observation["sandbox"]["kind"] != "docker":
        reasons.append("agent was not run in the standardized sandbox")
    if observation["sandbox"]["network"] not in {"internal", "none"}:
        reasons.append("sandbox network was not isolated")
    if not observation["sandbox"]["read_scope_enforced"]:
        reasons.append("declared read scope was not enforced")
    if not observation["checks_isolated"]:
        reasons.append("required checks were not run in the isolated checker")

    risk_levels = {risk.get("level") for risk in completion["risks"] if isinstance(risk, dict)}
    if "critical" in risk_levels:
        return Decision("halt", ("agent reported critical risk",))
    if "high" in risk_levels:
        reasons.append("agent reported high risk")

    evidence = set(observation["evidence"]) | {observation["telemetry_ref"]}
    unsupported = []
    for index, claim in enumerate(completion["claims"]):
        if not isinstance(claim, dict) or not _strings(claim.get("evidence_refs")):
            unsupported.append(index)
        elif not set(claim["evidence_refs"]).issubset(evidence):
            unsupported.append(index)
    if unsupported:
        reasons.append(f"claims lack observed evidence: {unsupported}")

    return Decision("double_check", tuple(reasons)) if reasons else Decision("advance", ())
