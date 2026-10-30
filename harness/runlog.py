"""Append-only per-run event log and crash recovery for scheduled runs.

Every scheduled run keeps `run-log.jsonl` beside its work order. The runner appends one
fsynced record before and after each step that has an effect outside the process
(issuing the order, creating the worktree, starting and stopping the actuator, observing,
deciding). The last record is the run's state, so a later invocation can tell a finished
run from one that was killed and repair the latter without guessing.

Recovery never mutates a candidate worktree and never retries a mutation. It tears down
a run's Compose project when the actuator may still be running, rolls a run forward when
its decision was already persisted, and otherwise records `abandoned`, which the control
loop treats as `double_check`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from harness.controller import RUN_ID_PATTERN
from harness.sandbox import COMPOSE_FILE, compose_project_name
from harness.secureio import fsync_directory, write_private_json

RUN_LOG_SCHEMA = "local-harness/run-event/v1"
RUN_LOG_NAME = "run-log.jsonl"
MAX_RECORD_BYTES = 64 * 1024

# Events a scheduled run appends, in order. Only the last three end a run.
EVENTS = (
    "issued",
    "worktree_created",
    "actuator_started",
    "actuator_exited",
    "observed",
    "decided",
    "completed",
    "failed",
    "abandoned",
)
TERMINAL_EVENTS = frozenset({"completed", "failed", "abandoned"})


def log_path(run_root: Path) -> Path:
    return run_root / RUN_LOG_NAME


def read_events(run_root: Path) -> tuple[list[dict[str, Any]], int]:
    """Return valid records and the count of invalid lines.

    A final line without a newline is a write torn by a crash; it is counted as
    invalid rather than trusted.
    """
    path = log_path(run_root)
    if not path.exists():
        return [], 0
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"run log must be a regular, non-symbolic-link file: {path}")
    records: list[dict[str, Any]] = []
    invalid = 0
    for line in path.read_bytes().splitlines(keepends=True):
        try:
            if not line.endswith(b"\n") or len(line) > MAX_RECORD_BYTES:
                raise ValueError("torn or oversized record")
            record = json.loads(line)
            if (
                not isinstance(record, dict)
                or record.get("schema") != RUN_LOG_SCHEMA
                or record.get("event") not in EVENTS
                or type(record.get("seq")) is not int
            ):
                raise ValueError("invalid run event")
        except (ValueError, json.JSONDecodeError):
            invalid += 1
            continue
        records.append(record)
    return records, invalid


def append_event(run_root: Path, run_id: str, event: str, **data: Any) -> dict[str, Any]:
    """Durably append one event; returns the record written."""
    if event not in EVENTS:
        raise ValueError(f"unknown run event {event!r}")
    path = log_path(run_root)
    records, _invalid = read_events(run_root)
    if records and records[-1]["event"] in TERMINAL_EVENTS:
        raise ValueError(f"run {run_id} already ended with {records[-1]['event']}")
    record = {
        "schema": RUN_LOG_SCHEMA,
        "seq": records[-1]["seq"] + 1 if records else 0,
        "ts": int(time.time() * 1000),
        "run_id": run_id,
        "event": event,
        **({"data": data} if data else {}),
    }
    encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_RECORD_BYTES:
        raise ValueError("run event exceeds the record size limit")
    created = not path.exists()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        size = os.fstat(descriptor).st_size
        if size and _last_byte(path) != b"\n":
            # Terminate a torn record so this one starts on its own line.
            encoded = b"\n" + encoded
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if created:
        fsync_directory(path.parent)
    return record


def _last_byte(path: Path) -> bytes:
    with path.open("rb") as stream:
        stream.seek(-1, os.SEEK_END)
        return stream.read(1)


def last_event(run_root: Path) -> dict[str, Any] | None:
    records, _invalid = read_events(run_root)
    return records[-1] if records else None


def _teardown(run_id: str) -> dict[str, Any]:
    if not shutil.which("docker"):
        return {"exit_code": 127, "detail": "Docker unavailable; Compose project was not torn down"}
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                compose_project_name(run_id),
                "-f",
                str(COMPOSE_FILE),
                "down",
                "--remove-orphans",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        return {"exit_code": 124, "detail": str(exc)[-2000:]}
    except OSError as exc:
        return {"exit_code": 127, "detail": str(exc)[-2000:]}
    return {"exit_code": result.returncode, "detail": (result.stderr or result.stdout)[-2000:]}


def recover_run(run_root: Path) -> dict[str, Any] | None:
    """Repair one unfinished run; return a summary, or None when it had already ended.

    The caller must hold the schedule lock so no live invocation owns this run.
    """
    run_id = run_root.name
    if not RUN_ID_PATTERN.fullmatch(run_id):
        return None
    records, invalid = read_events(run_root)
    if records and records[-1]["event"] in TERMINAL_EVENTS:
        return None
    result_path = run_root / "schedule-result.json"
    if not records and result_path.exists():
        # Finished before run logs existed.
        return None
    last = records[-1]["event"] if records else None
    seen = {record["event"] for record in records}
    summary: dict[str, Any] = {"run_id": run_id, "last_event": last, "invalid_records": invalid}

    if "actuator_started" in seen and "actuator_exited" not in seen:
        summary["teardown"] = _teardown(run_id)
    events = run_root / "evidence" / "agent" / "runner-events.jsonl"
    if events.is_file() and not events.is_symlink():
        summary["runner_events_bytes"] = events.stat().st_size

    decision_path = run_root / "evidence" / "decision.json"
    if last == "decided" and decision_path.is_file() and not decision_path.is_symlink():
        # The decision was persisted; only the schedule summary is missing. Roll forward.
        decision = json.loads(decision_path.read_text())
        write_private_json(
            result_path,
            decision
            | {
                "run_id": run_id,
                "worktree": str(run_root / "worktree"),
                "evidence": str(run_root / "evidence"),
                "recovered": True,
            },
        )
        append_event(run_root, run_id, "completed", recovered=True, outcome=decision.get("outcome"))
        summary["action"] = "completed"
        return summary

    reason = f"run interrupted after {last or 'no recorded event'}; recovered by a later invocation"
    if not result_path.exists():
        write_private_json(
            result_path,
            {
                "outcome": "double_check",
                "reasons": [reason],
                "run_id": run_id,
                "worktree": str(run_root / "worktree"),
                "evidence": str(run_root / "evidence"),
                "recovered": True,
            },
        )
    append_event(run_root, run_id, "abandoned", after=last, reason=reason, **_teardown_data(summary))
    summary["action"] = "abandoned"
    return summary


def _teardown_data(summary: dict[str, Any]) -> dict[str, Any]:
    teardown = summary.get("teardown")
    return {"teardown_exit_code": teardown["exit_code"]} if teardown else {}


def recover_schedule(schedule_root: Path) -> list[dict[str, Any]]:
    """Recover every unfinished run under one schedule directory, oldest first."""
    if not schedule_root.is_dir():
        return []
    recovered = []
    for run_root in sorted(path for path in schedule_root.iterdir() if path.is_dir()):
        if run_root.is_symlink():
            continue
        summary = recover_run(run_root)
        if summary:
            recovered.append(summary)
    return recovered
