"""Create runner-owned observations from git state and registered checks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from harness.controller import RUN_ID_PATTERN, document_sha256, load_document, validate_work_order
from harness.paths import REPO_DIR, STATE_DIR
from harness.sandbox import sandbox_source_sha256
from harness.secureio import write_private, write_private_json
from harness.telemetry import TELEMETRY_DB, TELEMETRY_LOG, run_stats, sync

CHECKER_COMPOSE = REPO_DIR / "sandbox" / "checker.compose.yaml"


def _run(command: list[str], cwd: Path, timeout: int = 900) -> tuple[int, str]:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        output = stdout + stderr
        return 124, output
    output = result.stdout + result.stderr
    return result.returncode, output


def load_check_registry(base_commit: str, cwd: Path = REPO_DIR) -> dict[str, list[str]]:
    """Load check commands from the pre-agent commit, never the mutable worktree."""
    result = subprocess.run(
        ["git", "show", f"{base_commit}:contracts/checks.json"],
        cwd=cwd,
        capture_output=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError(
            f"cannot load trusted check registry: {result.stderr[-4000:].decode(errors='replace')}"
        )
    if len(result.stdout) > 64 * 1024:
        raise ValueError("trusted check registry exceeds 64 KiB")
    output = result.stdout.decode()
    registry = json.loads(output)
    if not isinstance(registry, dict):
        raise ValueError("trusted check registry must be an object")
    return registry


def _run_check(command: list[str], cwd: Path, timeout: int, isolated: bool) -> tuple[int, str]:
    if not isolated:
        return _run(command, cwd, timeout)
    if not shutil.which("docker"):
        return 127, "Docker unavailable; isolated checks were not run"
    docker_command = [
        "docker",
        "compose",
        "-f",
        str(CHECKER_COMPOSE),
        "run",
        "--rm",
        "checker",
        *command,
    ]
    environment = os.environ | {
        "WORKTREE": str(cwd.resolve()),
        "AGENT_UID": str(os.getuid()),
        "AGENT_GID": str(os.getgid()),
    }
    try:
        result = subprocess.run(
            docker_command,
            cwd=REPO_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return 124, ((exc.stdout or "") + (exc.stderr or ""))[-4000:]
    return result.returncode, (result.stdout + result.stderr)[-4000:]


def runner_event_stats(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    records = []
    invalid = 0
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or not isinstance(record.get("type"), str):
                raise ValueError("invalid event")
            records.append(record)
        except (ValueError, json.JSONDecodeError):
            invalid += 1
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    starts = [index for index, record in enumerate(records) if record["type"] == "agent_start"]
    ends = [index for index, record in enumerate(records) if record["type"] == "agent_end"]
    return {
        "tool_calls": sum(record["type"] == "tool_execution_start" for record in records),
        "wall_seconds": manifest.get("wall_seconds", 0),
        "complete": (
            invalid == 0
            and len(starts) == 1
            and len(ends) == 1
            and starts[0] < ends[0]
            and manifest.get("runner_events_sha256") == digest
            and manifest.get("exit_code") == 0
        ),
        "ref": f"runner-events:{digest}",
    }


def changed_files(base_commit: str, cwd: Path = REPO_DIR) -> list[dict[str, str]]:
    """Return actual path and change kind using Git's NUL-delimited format."""
    result = subprocess.run(
        ["git", "diff", "--name-status", "-z", base_commit, "--"],
        cwd=cwd,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        detail = result.stderr[-4000:].decode(errors="replace")
        raise RuntimeError(f"cannot enumerate changed paths: {detail}")
    fields = [os.fsdecode(value) for value in result.stdout.split(b"\0") if value]
    changes: dict[str, str] = {}
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise RuntimeError("Git returned a malformed rename record")
            _old_path, path = fields[index : index + 2]
            index += 2
            kind = "renamed" if status.startswith("R") else "added"
        else:
            if index >= len(fields):
                raise RuntimeError("Git returned a malformed change record")
            path = fields[index]
            index += 1
            kind = {"A": "added", "D": "deleted"}.get(status[:1], "modified")
        changes[path] = kind

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=cwd,
        capture_output=True,
        timeout=30,
    )
    if untracked.returncode:
        detail = untracked.stderr[-4000:].decode(errors="replace")
        raise RuntimeError(f"cannot enumerate untracked paths: {detail}")
    for value in untracked.stdout.split(b"\0"):
        if value:
            changes[os.fsdecode(value)] = "added"
    return [{"path": path, "kind": changes[path]} for path in sorted(changes)]


def observe(
    work_order_path: Path,
    output_path: Path,
    cwd: Path = REPO_DIR,
    telemetry_ref: str | None = None,
    telemetry_source: Path = TELEMETRY_LOG,
    telemetry_database: Path = TELEMETRY_DB,
    runner_id: str = "local-harness-runner",
    sandbox_manifest: Path | None = None,
    runner_events: Path | None = None,
) -> dict[str, Any]:
    work = load_document(work_order_path)
    problems = validate_work_order(work)
    if problems:
        raise ValueError("invalid work order: " + "; ".join(problems))
    base_commit = work["base_commit"]
    registry = load_check_registry(base_commit, cwd)
    results = []
    evidence = []
    started = time.monotonic()
    isolated_checks = bool(sandbox_manifest and sandbox_manifest.is_file())
    for index, name in enumerate(work["required_checks"]):
        log_name = f"{index:02d}-{hashlib.sha256(name.encode()).hexdigest()[:12]}.log"
        command = registry.get(name)
        if not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command):
            detail = "unregistered check"
            write_private(output_path.parent / "checks" / log_name, detail)
            results.append(
                {
                    "name": name,
                    "status": "fail",
                    "exit_code": 127,
                    "output_sha256": hashlib.sha256(detail.encode()).hexdigest(),
                    "output_ref": f"checks/{log_name}",
                }
            )
            continue
        code, detail = _run_check(
            command,
            cwd,
            work["limits"]["max_wall_seconds"],
            isolated_checks,
        )
        write_private(output_path.parent / "checks" / log_name, detail)
        results.append(
            {
                "name": name,
                "status": "pass" if code == 0 else "fail",
                "exit_code": code,
                "output_sha256": hashlib.sha256(detail.encode()).hexdigest(),
                "output_ref": f"checks/{log_name}",
            }
        )
        if code == 0:
            evidence.append(f"check:{name}")
    changes = changed_files(base_commit, cwd)
    paths = [change["path"] for change in changes]
    evidence.extend(f"path:{path}" for path in paths)
    telemetry_problem = None
    try:
        sync(telemetry_source, telemetry_database)
        telemetry = run_stats(work["run_id"], telemetry_database)
    except (OSError, ValueError) as exc:
        telemetry_problem = str(exc)
        telemetry = {"tool_calls": 0, "wall_seconds": 0.0, "complete": False, "events": 0}
    sandbox = {
        "kind": "host",
        "manifest_sha256": None,
        "network": "host",
        "read_scope_enforced": False,
    }
    sandbox_violations = []
    if sandbox_manifest and sandbox_manifest.is_file():
        manifest = load_document(sandbox_manifest)
        security = manifest.get("security", {})
        sandbox = {
            "kind": "docker",
            "manifest_sha256": hashlib.sha256(sandbox_manifest.read_bytes()).hexdigest(),
            "network": security.get("network", "unknown"),
            "read_scope_enforced": work["scope"]["read"] == ["**"],
        }
        expected_compose = hashlib.sha256((REPO_DIR / "sandbox" / "compose.yaml").read_bytes()).hexdigest()
        if manifest.get("compose_sha256") != expected_compose:
            sandbox_violations.append("sandbox Compose digest mismatch")
        expected_checker = hashlib.sha256(CHECKER_COMPOSE.read_bytes()).hexdigest()
        if manifest.get("checker_compose_sha256") != expected_checker:
            sandbox_violations.append("checker Compose digest mismatch")
        if manifest.get("sandbox_source_sha256") != sandbox_source_sha256():
            sandbox_violations.append("sandbox source digest mismatch")
        if (manifest.get("environment") or {}).get("LOCAL_HARNESS_RUN_ID") != work["run_id"]:
            sandbox_violations.append("sandbox run identity mismatch")
        if security.get("network") != "internal":
            sandbox_violations.append("sandbox network is not internal")
        if security.get("read_only_root") is not True:
            sandbox_violations.append("sandbox root is not read-only")
        if security.get("capabilities") != "all dropped":
            sandbox_violations.append("sandbox capabilities were not dropped")
        if security.get("host_home_mounted") is not False:
            sandbox_violations.append("host home was mounted")
        if security.get("docker_socket_mounted") is not False:
            sandbox_violations.append("Docker socket was mounted")
        if security.get("checker_network") != "none":
            sandbox_violations.append("checker network is not disabled")
        if security.get("checker_workspace_read_only") is not True:
            sandbox_violations.append("checker workspace is writable")
        if security.get("cleanup_completed") is not True or manifest.get("cleanup_exit_code") != 0:
            sandbox_violations.append("sandbox teardown did not complete")
        if telemetry_problem:
            sandbox_violations.append(f"telemetry could not be read safely: {telemetry_problem}")
        if runner_events and runner_events.is_file():
            telemetry = runner_event_stats(runner_events, manifest)
        else:
            telemetry["complete"] = False
    observation = {
        "schema": "local-harness/observation/v1",
        "run_id": work["run_id"],
        "work_order_sha256": document_sha256(work),
        "runner_id": runner_id,
        "base_commit": base_commit,
        "changes": changes,
        "checks": results,
        "checks_isolated": isolated_checks and shutil.which("docker") is not None,
        "sandbox": sandbox,
        "sandbox_violations": sandbox_violations,
        "telemetry_ref": telemetry_ref or telemetry.get("ref", f"telemetry:{work['run_id']}"),
        "telemetry_complete": telemetry["complete"],
        "evidence": evidence or [f"git:{base_commit}"],
        "tool_calls": telemetry["tool_calls"],
        "wall_seconds": telemetry["wall_seconds"] or round(time.monotonic() - started, 3),
    }
    write_private_json(output_path, observation)
    return observation


def default_output(run_id: str) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("invalid run_id for observation path")
    return STATE_DIR / "contracts" / run_id / "observation.json"
