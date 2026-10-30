"""One bounded sense-control-actuate-observe-decision iteration."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from harness.controller import (
    Decision,
    document_sha256,
    evaluate_contract,
    load_document,
    validate_work_order,
)
from harness.observer import load_check_registry, observe
from harness.paths import REPO_DIR, STATE_DIR
from harness.runlog import append_event, recover_schedule
from harness.sandbox import compose_project_name, render_prompt, run_agent
from harness.secureio import write_private, write_private_json
from harness.telemetry import TELEMETRY_DB, sync

SCHEDULE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _private_dir(path: Path) -> None:
    resolved = path.resolve()
    unsafe = {Path("/"), Path("/tmp"), Path.home().resolve()}
    if resolved in unsafe or (resolved / ".git").exists():
        raise ValueError(f"refusing to use broad artifact directory {resolved}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    write_private_json(path, value)


def run_iteration(
    work_order: Path,
    worktree: Path,
    artifacts: Path,
    model: str = "llama-cpp/qwen3.5-4b",
    memory: Path | None = None,
    run_log: Path | None = None,
) -> Decision:
    """Run exactly one mutation attempt; never retry automatically.

    With ``run_log`` (a scheduled run's root), each step is recorded in its append-only
    run log so a later invocation can recover the run if this process dies.
    """
    work = load_document(work_order)
    problems = validate_work_order(work)
    if problems:
        raise ValueError("invalid work order: " + "; ".join(problems))
    if artifacts.resolve().is_relative_to(worktree.resolve()):
        raise ValueError("artifact directory must be outside the agent worktree")
    _private_dir(artifacts)
    agent_artifacts = artifacts / "agent"
    _private_dir(agent_artifacts)
    work_copy = artifacts / "work-order.json"
    shutil.copyfile(work_order, work_copy)
    work_copy.chmod(0o600)
    prompt = artifacts / "prompt.md"
    write_private(prompt, render_prompt(work_order, memory))

    def record(event: str, **data: Any) -> None:
        if run_log is not None:
            append_event(run_log, work["run_id"], event, **data)

    record("actuator_started", compose_project=compose_project_name(work["run_id"]))
    exit_code = run_agent(work_copy, prompt, worktree, agent_artifacts, model)
    record("actuator_exited", exit_code=exit_code)
    _write_json(work_copy, work)
    observation_path = artifacts / "observation.json"
    observation = observe(
        work_copy,
        observation_path,
        cwd=worktree,
        telemetry_source=agent_artifacts / "state" / "local-harness" / "telemetry.jsonl",
        telemetry_database=artifacts / "telemetry.sqlite3",
        sandbox_manifest=agent_artifacts / "sandbox-manifest.json",
        runner_events=agent_artifacts / "runner-events.jsonl",
    )
    record("observed", observation_sha256=document_sha256(observation))
    completion_path = agent_artifacts / "completion.json"
    if completion_path.is_symlink():
        decision = Decision(
            "halt",
            (f"actuator exited {exit_code}", "agent completion artifact is a symbolic link"),
        )
    elif not completion_path.is_file():
        decision = Decision(
            "double_check",
            (f"actuator exited {exit_code}", "agent did not produce completion.json"),
        )
    else:
        decision = evaluate_contract(work, load_document(completion_path), observation)
        if exit_code and decision.outcome == "advance":
            decision = Decision("double_check", (f"actuator exited {exit_code}",))
    _write_json(artifacts / "decision.json", decision.as_dict())
    telemetry_source = agent_artifacts / "state" / "local-harness" / "telemetry.jsonl"
    sync(telemetry_source, TELEMETRY_DB)
    _write_json(
        STATE_DIR / "contracts" / work["run_id"] / "decision.json",
        decision.as_dict()
        | {
            "recorded_at": int(time.time() * 1000),
            "objective": work["objective"],
            "changes": observation.get("changes", []),
            "checks": observation.get("checks", []),
            "artifact_dir": str(artifacts.resolve()),
        },
    )
    record("decided", outcome=decision.outcome)
    return decision


def run_config(path: Path) -> Decision:
    config = load_document(path)
    required = {"work_order", "worktree", "artifacts"}
    missing = sorted(required - config.keys())
    if missing:
        raise ValueError(f"loop config is missing {missing}")
    unexpected = sorted(set(config) - (required | {"model", "memory"}))
    if unexpected:
        raise ValueError(f"loop config has unexpected fields {unexpected}")
    if not all(isinstance(config[key], str) and config[key] for key in required):
        raise ValueError("loop config paths must be non-empty strings")
    if "model" in config and (not isinstance(config["model"], str) or not config["model"]):
        raise ValueError("loop config model must be a non-empty string")
    if "memory" in config and (not isinstance(config["memory"], str) or not config["memory"]):
        raise ValueError("loop config memory must be a non-empty string")
    return run_iteration(
        Path(config["work_order"]),
        Path(config["worktree"]),
        Path(config["artifacts"]),
        config.get("model", "llama-cpp/qwen3.5-4b"),
        Path(config["memory"]) if config.get("memory") else None,
    )


def run_scheduled(path: Path) -> tuple[Decision, Path]:
    """Issue and run one unique scheduled work order without overwriting prior evidence."""
    config = load_document(path)
    required = {"objective", "write"}
    allowed = required | {"deny", "checks", "limits", "model", "memory", "next_step", "schedule_id"}
    missing = sorted(required - config.keys())
    unexpected = sorted(set(config) - allowed)
    if missing:
        raise ValueError(f"scheduled config is missing {missing}")
    if unexpected:
        raise ValueError(f"scheduled config has unexpected fields {unexpected}")
    if not isinstance(config["objective"], str) or not config["objective"]:
        raise ValueError("scheduled objective must be a non-empty string")
    for key in ("write", "deny", "checks"):
        value = config.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise ValueError(f"scheduled {key} must be a string array")
    if not config["write"]:
        raise ValueError("scheduled write scope must not be empty")
    for key in ("model", "memory", "next_step"):
        if key in config and (not isinstance(config[key], str) or not config[key]):
            raise ValueError(f"scheduled {key} must be a non-empty string")
    schedule_id = config.get("schedule_id", path.stem)
    if not isinstance(schedule_id, str) or not SCHEDULE_ID_PATTERN.fullmatch(schedule_id):
        raise ValueError("scheduled schedule_id is unsafe")
    limits = {
        "min_changed_files": 1,
        "max_changed_files": 8,
        "max_tool_calls": 40,
        "max_wall_seconds": 900,
    }
    configured_limits = config.get("limits", {})
    if not isinstance(configured_limits, dict) or not set(configured_limits).issubset(limits):
        raise ValueError("scheduled limits contain unknown fields")
    limits.update(configured_limits)

    schedule_root = STATE_DIR / "scheduled" / schedule_id
    _private_dir(schedule_root)
    with _schedule_lock(schedule_root, schedule_id):
        # The lock proves no other invocation owns a run here, so any run whose log
        # has not ended was interrupted. Repair it before issuing new work.
        recover_schedule(schedule_root)
        return _run_scheduled_locked(config, schedule_id, schedule_root, limits)


def _run_scheduled_locked(
    config: dict[str, Any], schedule_id: str, schedule_root: Path, limits: dict[str, int]
) -> tuple[Decision, Path]:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout
    if status:
        raise RuntimeError("scheduled loop requires a clean primary checkout")
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    checks = list(dict.fromkeys(config.get("checks", ["ruff", "pytest"])))
    registry = load_check_registry(base_commit, REPO_DIR)
    missing_checks = sorted(set(checks) - set(registry))
    if missing_checks:
        raise ValueError(f"scheduled checks are not registered at {base_commit}: {missing_checks}")
    run_id = f"{schedule_id}-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    work = {
        "schema": "local-harness/work-order/v1",
        "run_id": run_id,
        "objective": config["objective"],
        "base_commit": base_commit,
        "scope": {
            "read": ["**"],
            "write": list(dict.fromkeys(config["write"])),
            "deny": list(
                dict.fromkeys([".env", "**/.env", "secrets/**", "**/secrets/**", *config.get("deny", [])])
            ),
        },
        "required_checks": checks,
        "limits": limits,
        "approval": "human",
        "next_step": config.get("next_step", "present the verified change for human review"),
    }
    problems = validate_work_order(work)
    if problems:
        raise ValueError("invalid scheduled work order: " + "; ".join(problems))

    run_root = schedule_root / run_id
    _private_dir(run_root)
    append_event(run_root, run_id, "issued", base_commit=base_commit, work_order_sha256=document_sha256(work))
    work_order = run_root / "work-order.json"
    worktree = run_root / "worktree"
    evidence = run_root / "evidence"
    try:
        write_private_json(work_order, work)
        write_private(run_root / "work-order.sha256", document_sha256(work) + "\n")
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), base_commit],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        append_event(run_root, run_id, "worktree_created")
        decision = run_iteration(
            work_order,
            worktree,
            evidence,
            config.get("model", "llama-cpp/qwen3.5-4b"),
            Path(config["memory"]) if config.get("memory") else None,
            run_log=run_root,
        )
    except Exception as exc:
        write_private_json(
            run_root / "schedule-result.json",
            {
                "outcome": "double_check",
                "reasons": [f"scheduled iteration failed: {exc}"],
                "run_id": run_id,
                "worktree": str(worktree),
                "evidence": str(evidence),
            },
        )
        append_event(run_root, run_id, "failed", reason=str(exc)[-2000:])
        raise
    write_private_json(
        run_root / "schedule-result.json",
        decision.as_dict() | {"run_id": run_id, "worktree": str(worktree), "evidence": str(evidence)},
    )
    append_event(run_root, run_id, "completed", outcome=decision.outcome)
    return decision, run_root


@contextmanager
def _schedule_lock(schedule_root: Path, schedule_id: str) -> Iterator[None]:
    lock_path = schedule_root / "active.lock"
    lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"scheduled loop {schedule_id!r} is already active") from exc
        yield
    finally:
        os.close(lock_descriptor)


def recover_scheduled(schedule_id: str | None = None) -> dict[str, Any]:
    """Recover interrupted runs in one schedule, or in every schedule that is not active."""
    root = STATE_DIR / "scheduled"
    if schedule_id is not None:
        if not SCHEDULE_ID_PATTERN.fullmatch(schedule_id):
            raise ValueError("scheduled schedule_id is unsafe")
        candidates = [root / schedule_id]
    else:
        candidates = sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []
    recovered: list[dict[str, Any]] = []
    skipped: list[str] = []
    for schedule_root in candidates:
        if schedule_root.is_symlink() or not schedule_root.is_dir():
            continue
        try:
            with _schedule_lock(schedule_root, schedule_root.name):
                recovered.extend(recover_schedule(schedule_root))
        except RuntimeError:
            skipped.append(schedule_root.name)
    return {"recovered": recovered, "skipped_active": skipped}
