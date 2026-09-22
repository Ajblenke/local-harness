import hashlib
import json
import subprocess
import sys
from pathlib import Path

from harness.controller import document_sha256
from harness.observer import changed_files, default_output, observe, runner_event_stats
from harness.sandbox import write_manifest


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def test_observer_uses_check_registry_from_base_commit(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "contracts").mkdir()
    checks = repo / "contracts" / "checks.json"
    checks.write_text(json.dumps({"unit": [sys.executable, "-c", "raise SystemExit(0)"]}))
    (repo / "data.txt").write_text("before\n")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    base = git(repo, "rev-parse", "HEAD")

    work = {
        "schema": "local-harness/work-order/v1",
        "run_id": "observer-test",
        "objective": "test trusted checks",
        "base_commit": base,
        "scope": {"read": ["**"], "write": ["**"], "deny": []},
        "required_checks": ["unit"],
        "limits": {
            "min_changed_files": 1,
            "max_changed_files": 4,
            "max_tool_calls": 4,
            "max_wall_seconds": 30,
        },
        "approval": "human",
        "next_step": "review",
    }
    work_path = tmp_path / "work.json"
    work_path.write_text(json.dumps(work))
    (repo / "data.txt").write_text("after\n")
    checks.write_text(json.dumps({"unit": [sys.executable, "-c", "raise SystemExit(9)"]}))

    telemetry = tmp_path / "telemetry.jsonl"
    telemetry.write_text(
        "".join(
            json.dumps(record) + "\n"
            for record in (
                {"ts": 1000, "event": "session_start", "run_id": "observer-test"},
                {"ts": 1200, "event": "tool_start", "run_id": "observer-test"},
                {"ts": 2000, "event": "agent_settled", "run_id": "observer-test"},
            )
        )
    )
    output = tmp_path / "observation.json"
    result = observe(
        work_path,
        output,
        cwd=repo,
        telemetry_source=telemetry,
        telemetry_database=tmp_path / "telemetry.sqlite3",
    )
    assert result["checks"][0]["status"] == "pass"
    check_log = output.parent / result["checks"][0]["output_ref"]
    assert check_log.is_file()
    assert hashlib.sha256(check_log.read_bytes()).hexdigest() == result["checks"][0]["output_sha256"]
    assert check_log.stat().st_mode & 0o777 == 0o600
    assert result["work_order_sha256"] == document_sha256(work)
    assert result["telemetry_complete"] is True
    assert result["tool_calls"] == 1
    assert result["checks_isolated"] is False
    assert {change["path"] for change in result["changes"]} >= {"contracts/checks.json", "data.txt"}
    assert output.stat().st_mode & 0o777 == 0o600


def test_runner_events_are_bound_to_manifest_digest(tmp_path: Path):
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"agent_start"}\n{"type":"tool_execution_start"}\n{"type":"agent_end"}\n')
    digest = hashlib.sha256(events.read_bytes()).hexdigest()
    manifest = {"runner_events_sha256": digest, "wall_seconds": 2.5, "exit_code": 0}
    stats = runner_event_stats(events, manifest)
    assert stats["complete"] is True
    assert stats["tool_calls"] == 1
    assert stats["wall_seconds"] == 2.5
    assert runner_event_stats(events, manifest | {"runner_events_sha256": "tampered"})["complete"] is False


def test_runner_events_require_order_single_run_and_success(tmp_path: Path):
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"agent_end"}\n{"type":"agent_start"}\n')
    digest = hashlib.sha256(events.read_bytes()).hexdigest()
    manifest = {"runner_events_sha256": digest, "wall_seconds": 1, "exit_code": 0}
    assert runner_event_stats(events, manifest)["complete"] is False
    events.write_text('{"type":"agent_start"}\n{"type":"agent_end"}\n')
    digest = hashlib.sha256(events.read_bytes()).hexdigest()
    manifest = {"runner_events_sha256": digest, "wall_seconds": 1, "exit_code": 1}
    assert runner_event_stats(events, manifest)["complete"] is False


def test_changed_paths_preserve_newlines(tmp_path: Path):
    repo = tmp_path / "repo-newline"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "contracts").mkdir()
    (repo / "contracts" / "checks.json").write_text("{}")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    unusual = repo / "line\nbreak.txt"
    unusual.write_text("content")
    assert changed_files(base, repo) == [{"path": "line\nbreak.txt", "kind": "added"}]


def test_observer_detects_checker_manifest_tampering(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo-sandbox"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "contracts").mkdir()
    (repo / "contracts" / "checks.json").write_text(json.dumps({"unit": ["true"]}))
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    work = {
        "schema": "local-harness/work-order/v1",
        "run_id": "sandbox-test",
        "objective": "test manifest binding",
        "base_commit": base,
        "scope": {"read": ["**"], "write": ["**"], "deny": []},
        "required_checks": ["unit"],
        "limits": {
            "min_changed_files": 0,
            "max_changed_files": 2,
            "max_tool_calls": 2,
            "max_wall_seconds": 30,
        },
        "approval": "human",
        "next_step": "review",
    }
    work_path = tmp_path / "work.json"
    work_path.write_text(json.dumps(work))
    artifacts = tmp_path / "artifacts"
    events = artifacts / "runner-events.jsonl"
    events.parent.mkdir()
    events.write_text('{"type":"agent_start"}\n{"type":"agent_end"}\n')
    manifest_path = write_manifest(
        artifacts,
        ["docker", "compose"],
        {"LOCAL_HARNESS_RUN_ID": "sandbox-test"},
        exit_code=0,
        wall_seconds=1,
        runner_events_sha256=hashlib.sha256(events.read_bytes()).hexdigest(),
    )
    manifest = json.loads(manifest_path.read_text())
    manifest["checker_compose_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr("harness.observer._run_check", lambda *_args, **_kwargs: (0, "pass"))
    monkeypatch.setattr("harness.observer.shutil.which", lambda _name: "/usr/bin/docker")
    result = observe(
        work_path,
        tmp_path / "observation.json",
        cwd=repo,
        telemetry_source=tmp_path / "missing.jsonl",
        telemetry_database=tmp_path / "telemetry.sqlite3",
        sandbox_manifest=manifest_path,
        runner_events=events,
    )
    assert "checker Compose digest mismatch" in result["sandbox_violations"]


def test_observer_detects_sandbox_source_manifest_tampering(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo-sandbox-source"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "contracts").mkdir()
    (repo / "contracts" / "checks.json").write_text(json.dumps({"unit": ["true"]}))
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    work = {
        "schema": "local-harness/work-order/v1",
        "run_id": "source-test",
        "objective": "test source binding",
        "base_commit": base,
        "scope": {"read": ["**"], "write": ["**"], "deny": []},
        "required_checks": ["unit"],
        "limits": {
            "min_changed_files": 0,
            "max_changed_files": 2,
            "max_tool_calls": 2,
            "max_wall_seconds": 30,
        },
        "approval": "human",
        "next_step": "review",
    }
    work_path = tmp_path / "work.json"
    work_path.write_text(json.dumps(work))
    artifacts = tmp_path / "artifacts"
    events = artifacts / "runner-events.jsonl"
    events.parent.mkdir()
    events.write_text('{"type":"agent_start"}\n{"type":"agent_end"}\n')
    manifest_path = write_manifest(
        artifacts,
        ["docker", "compose"],
        {"LOCAL_HARNESS_RUN_ID": "source-test"},
        exit_code=0,
        wall_seconds=1,
        runner_events_sha256=hashlib.sha256(events.read_bytes()).hexdigest(),
    )
    manifest = json.loads(manifest_path.read_text())
    manifest["sandbox_source_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr("harness.observer._run_check", lambda *_args, **_kwargs: (0, "pass"))
    monkeypatch.setattr("harness.observer.shutil.which", lambda _name: "/usr/bin/docker")
    result = observe(
        work_path,
        tmp_path / "observation.json",
        cwd=repo,
        telemetry_source=tmp_path / "missing.jsonl",
        telemetry_database=tmp_path / "telemetry.sqlite3",
        sandbox_manifest=manifest_path,
        runner_events=events,
    )
    assert "sandbox source digest mismatch" in result["sandbox_violations"]


def test_default_observation_path_rejects_traversal():
    try:
        default_output("../../escape")
    except ValueError as exc:
        assert "invalid run_id" in str(exc)
    else:
        raise AssertionError("unsafe run id was accepted")


def test_observer_records_untrusted_telemetry_symlink_as_violation(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo-telemetry-link"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "contracts").mkdir()
    (repo / "contracts" / "checks.json").write_text(json.dumps({"unit": ["true"]}))
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    work = {
        "schema": "local-harness/work-order/v1",
        "run_id": "telemetry-link",
        "objective": "reject linked telemetry",
        "base_commit": base,
        "scope": {"read": ["**"], "write": ["**"], "deny": []},
        "required_checks": ["unit"],
        "limits": {
            "min_changed_files": 0,
            "max_changed_files": 2,
            "max_tool_calls": 2,
            "max_wall_seconds": 30,
        },
        "approval": "human",
        "next_step": "review",
    }
    work_path = tmp_path / "work.json"
    work_path.write_text(json.dumps(work))
    artifacts = tmp_path / "artifacts"
    events = artifacts / "runner-events.jsonl"
    events.parent.mkdir()
    events.write_text('{"type":"agent_start"}\n{"type":"agent_end"}\n')
    manifest_path = write_manifest(
        artifacts,
        ["docker", "compose"],
        {"LOCAL_HARNESS_RUN_ID": "telemetry-link"},
        exit_code=0,
        wall_seconds=1,
        runner_events_sha256=hashlib.sha256(events.read_bytes()).hexdigest(),
    )
    target = tmp_path / "target.jsonl"
    target.write_text('{"ts":1,"event":"session_start"}\n')
    source = tmp_path / "telemetry.jsonl"
    source.symlink_to(target)
    monkeypatch.setattr("harness.observer._run_check", lambda *_args, **_kwargs: (0, "pass"))
    monkeypatch.setattr("harness.observer.shutil.which", lambda _name: "/usr/bin/docker")
    result = observe(
        work_path,
        tmp_path / "observation.json",
        cwd=repo,
        telemetry_source=source,
        telemetry_database=tmp_path / "telemetry.sqlite3",
        sandbox_manifest=manifest_path,
        runner_events=events,
    )
    assert any("telemetry could not be read safely" in item for item in result["sandbox_violations"])
