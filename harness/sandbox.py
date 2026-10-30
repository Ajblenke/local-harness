"""Build and launch the standardized, local-only agent container."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, TextIO

from harness.controller import document_sha256, load_document, validate_work_order
from harness.paths import REPO_DIR
from harness.secureio import write_private_json

COMPOSE_FILE = REPO_DIR / "sandbox" / "compose.yaml"
CHECKER_COMPOSE_FILE = REPO_DIR / "sandbox" / "checker.compose.yaml"
SANDBOX_SOURCE_PATHS = (
    REPO_DIR / ".dockerignore",
    REPO_DIR / "README.md",
    REPO_DIR / "pyproject.toml",
    REPO_DIR / "uv.lock",
    REPO_DIR / "harness",
    REPO_DIR / "pi" / "extensions" / "telemetry.ts",
    REPO_DIR / "sandbox" / "Dockerfile",
    REPO_DIR / "sandbox" / "compose.yaml",
    REPO_DIR / "sandbox" / "checker.compose.yaml",
    REPO_DIR / "sandbox" / "pi",
)


def sandbox_source_sha256() -> str:
    """Digest every repository input copied into or used to launch the sandbox."""
    files: list[Path] = []
    for candidate in SANDBOX_SOURCE_PATHS:
        files.extend(sorted(candidate.rglob("*")) if candidate.is_dir() else [candidate])
    digest = hashlib.sha256()
    build_files = (
        path for path in files if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    for path in sorted(build_files):
        digest.update(path.relative_to(REPO_DIR).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def render_prompt(work_order: Path, memory: Path | None = None) -> str:
    work = load_document(work_order)
    problems = validate_work_order(work)
    if problems:
        raise ValueError("invalid work order: " + "; ".join(problems))
    guidance = memory.read_text() if memory and memory.is_file() else "(no standing feedback)"
    return f"""Use the maintain-local-harness skill for this controlled increment.

The exact work order follows. Its canonical SHA-256 is {document_sha256(work)}.

```json
{json.dumps(work, indent=2)}
```

Standing feedback:

{guidance}

Change only the declared write scope. Run the required checks, but treat their results as agent claims.
Before finishing, write `/artifacts/completion.json` conforming to
`contracts/completion-v1.schema.json`. Use the supplied work-order digest, identify yourself with an
`agent_id` other than `local-harness-runner`, list every changed path, reference only evidence the runner
can independently reproduce, and request `double_check` for any unresolved uncertainty. Do not commit,
push, open a PR, broaden permissions, or access anything outside `/workspace` and `/artifacts`.
"""


def find_engine() -> str | None:
    if shutil.which("docker"):
        return "docker"
    return None


def compose_project_name(run_id: str) -> str:
    return f"local-harness-{hashlib.sha256(run_id.encode()).hexdigest()[:16]}"


def doctor() -> dict[str, Any]:
    engine = find_engine()
    result: dict[str, Any] = {
        "ready": False,
        "engine": engine,
        "compose_file": str(COMPOSE_FILE),
        "security": {
            "internal_network": True,
            "read_only_root": True,
            "capabilities": "all dropped",
            "host_home_mounted": False,
            "docker_socket_mounted": False,
        },
    }
    if not engine:
        result["reason"] = "Docker with Compose is not installed"
        return result
    model_file = os.environ.get("MODEL_FILE")
    if not model_file or not Path(model_file).is_file():
        result["reason"] = "MODEL_FILE must name an existing GGUF"
        return result
    daemon = subprocess.run(
        [engine, "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if daemon.returncode:
        result["reason"] = (daemon.stderr or daemon.stdout).strip() or "Docker daemon is unavailable"
        return result
    environment = os.environ | {
        "WORKTREE": str(REPO_DIR),
        "MODEL_FILE": model_file,
        "LOCAL_HARNESS_RUN_ID": "doctor",
    }
    definitions = [COMPOSE_FILE, CHECKER_COMPOSE_FILE]
    checks = []
    for definition in definitions:
        check = subprocess.run(
            [engine, "compose", "-f", str(definition), "config", "--quiet"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
        checks.append(check)
    result["docker_server"] = daemon.stdout.strip()
    result["definitions"] = {
        str(path): check.returncode == 0 for path, check in zip(definitions, checks, strict=True)
    }
    result["ready"] = all(check.returncode == 0 for check in checks)
    failed = next((check for check in checks if check.returncode), None)
    if failed:
        result["reason"] = (failed.stderr or failed.stdout).strip()
    return result


def agent_command(
    work_order: Path,
    prompt_file: Path,
    worktree: Path,
    artifacts: Path,
    model: str = "llama-cpp/qwen3.5-4b",
) -> tuple[list[str], dict[str, str], str]:
    """Return the auditable command/environment without executing it."""
    work = load_document(work_order)
    project = compose_project_name(work["run_id"])
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "-f",
        str(COMPOSE_FILE),
        "run",
        "--rm",
        "agent",
        "--mode",
        "json",
        "--print",
        "--approve",
        "--no-session",
        "--model",
        model,
        "--tools",
        "read,bash,edit,write",
    ]
    environment = {
        "WORKTREE": str(worktree.resolve()),
        "ARTIFACTS": str(artifacts.resolve()),
        "MODEL_FILE": os.environ.get("MODEL_FILE", ""),
        "LOCAL_HARNESS_RUN_ID": work["run_id"],
        "AGENT_UID": str(os.getuid()),
        "AGENT_GID": str(os.getgid()),
    }
    return command, environment, prompt_file.read_text()


def write_manifest(
    artifacts: Path,
    command: list[str],
    environment: dict[str, str],
    exit_code: int | None = None,
    wall_seconds: float | None = None,
    runner_events_sha256: str | None = None,
    cleanup_exit_code: int = 0,
) -> Path:
    artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = {
        "command": command,
        "environment": {key: value for key, value in environment.items() if key != "MODEL_FILE"},
        "compose_file": str(COMPOSE_FILE),
        "compose_sha256": hashlib.sha256(COMPOSE_FILE.read_bytes()).hexdigest(),
        "checker_compose_sha256": hashlib.sha256(CHECKER_COMPOSE_FILE.read_bytes()).hexdigest(),
        "sandbox_source_sha256": sandbox_source_sha256(),
        "exit_code": exit_code,
        "wall_seconds": wall_seconds,
        "runner_events_sha256": runner_events_sha256,
        "cleanup_exit_code": cleanup_exit_code,
        "security": {
            "network": "internal",
            "read_only_root": True,
            "capabilities": "all dropped",
            "host_home_mounted": False,
            "docker_socket_mounted": False,
            "checker_network": "none",
            "checker_workspace_read_only": True,
            "cleanup_completed": cleanup_exit_code == 0,
        },
    }
    path = artifacts / "sandbox-manifest.json"
    write_private_json(path, manifest)
    return path


def validate_worktree(work_order: Path, worktree: Path, artifacts: Path) -> None:
    work = load_document(work_order)
    problems = validate_work_order(work)
    if problems:
        raise ValueError("invalid work order: " + "; ".join(problems))
    root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    if Path(root).resolve() == REPO_DIR.resolve():
        raise ValueError("refusing to run the agent in the primary checkout; create a git worktree")
    if head != work["base_commit"]:
        raise ValueError("sandbox worktree HEAD does not match the work-order base commit")
    if work["scope"]["read"] != ["**"]:
        raise ValueError("sandbox v1 enforces only whole-worktree read scope")
    denied_present = sorted(
        str(path.relative_to(worktree))
        for pattern in work["scope"]["deny"]
        for path in worktree.glob(pattern)
    )
    if denied_present:
        raise ValueError(f"denied paths exist in the sandbox worktree: {denied_present}")
    if artifacts.resolve().is_relative_to(worktree.resolve()):
        raise ValueError("artifact directory must be outside the agent worktree")
    model_file = os.environ.get("MODEL_FILE")
    if not model_file or not Path(model_file).is_file():
        raise ValueError("MODEL_FILE must name the GGUF mounted into the local router")


def _open_stream(path: Path) -> TextIO:
    """Open a private, append-only evidence file, refusing a planted symbolic link."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "a")


def _run_streaming(
    command: list[str],
    environment: dict[str, str],
    stdin: str,
    stdout_stream: TextIO,
    stderr_stream: TextIO,
    timeout: float,
) -> int:
    """Run the actuator with its output redirected to files; return its exit code."""
    try:
        process = subprocess.Popen(
            command,
            cwd=REPO_DIR,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=stdout_stream,
            stderr=stderr_stream,
            text=True,
        )
    except OSError as exc:
        stderr_stream.write(f"local-harness: could not start actuator: {exc}\n")
        return 127
    try:
        process.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        stderr_stream.write("\nlocal-harness: actuator exceeded wall-time limit\n")
        return 124
    except BaseException:
        process.kill()
        process.wait()
        raise
    return process.returncode


def run_agent(
    work_order: Path,
    prompt_file: Path,
    worktree: Path,
    artifacts: Path,
    model: str = "llama-cpp/qwen3.5-4b",
) -> int:
    """Run pi in the container; the caller still controls whether to accept its changes."""
    if not find_engine():
        raise RuntimeError("Docker with Compose is required; run `harness sandbox doctor`")
    validate_worktree(work_order, worktree, artifacts)
    work = load_document(work_order)
    command, environment, stdin = agent_command(work_order, prompt_file, worktree, artifacts, model)
    events = artifacts / "runner-events.jsonl"
    stderr_path = artifacts / "agent-stderr.txt"
    parent_existed = artifacts.exists()
    artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent_existed:
        os.chmod(artifacts, 0o700)
    started = time.monotonic()
    cleanup_exit_code = 1
    # Stream the actuator's output straight to evidence files so a crash of this
    # process mid-run still leaves every event emitted so far on disk.
    with _open_stream(events) as stdout_stream, _open_stream(stderr_path) as stderr_stream:
        try:
            exit_code = _run_streaming(
                command,
                os.environ | environment,
                stdin,
                stdout_stream,
                stderr_stream,
                work["limits"]["max_wall_seconds"],
            )
        finally:
            try:
                cleanup = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "--project-name",
                        compose_project_name(work["run_id"]),
                        "-f",
                        str(COMPOSE_FILE),
                        "down",
                        "--remove-orphans",
                    ],
                    cwd=REPO_DIR,
                    env=os.environ | environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                cleanup_exit_code = cleanup.returncode
                cleanup_detail = cleanup.stderr or cleanup.stdout
            except subprocess.TimeoutExpired as exc:
                cleanup_exit_code = 124
                cleanup_detail = str(exc)
            except OSError as exc:
                cleanup_exit_code = 127
                cleanup_detail = str(exc)
            if cleanup_exit_code:
                stderr_stream.write("\nlocal-harness cleanup failed:\n" + cleanup_detail)
            for stream in (stdout_stream, stderr_stream):
                stream.flush()
                os.fsync(stream.fileno())
    wall_seconds = round(time.monotonic() - started, 3)
    write_manifest(
        artifacts,
        command,
        environment,
        exit_code=exit_code,
        wall_seconds=wall_seconds,
        runner_events_sha256=hashlib.sha256(events.read_bytes()).hexdigest(),
        cleanup_exit_code=cleanup_exit_code,
    )
    return exit_code
