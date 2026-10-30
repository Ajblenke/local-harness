import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from harness.sandbox import (
    agent_command,
    compose_project_name,
    doctor,
    run_agent,
    sandbox_source_sha256,
    write_manifest,
)
from harness.sandbox_entrypoint import prepare_pi_config


def test_agent_command_keeps_prompt_out_of_environment(tmp_path: Path):
    work = tmp_path / "work.json"
    work.write_text(json.dumps({"run_id": "run-1"}))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("private prompt")
    command, environment, stdin = agent_command(work, prompt, tmp_path, tmp_path / "artifacts")
    assert stdin == "private prompt"
    assert "private prompt" not in json.dumps(command)
    assert "private prompt" not in json.dumps(environment)
    assert environment["LOCAL_HARNESS_RUN_ID"] == "run-1"
    assert compose_project_name("run-1") in command


def test_manifest_redacts_prompt(tmp_path: Path):
    work = tmp_path / "work.json"
    work.write_text(json.dumps({"run_id": "run-1"}))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("secret prompt")
    command, environment, _stdin = agent_command(work, prompt, tmp_path, tmp_path)
    path = write_manifest(tmp_path, command, environment)
    assert "secret prompt" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    manifest = json.loads(path.read_text())
    assert len(manifest["checker_compose_sha256"]) == 64
    assert manifest["sandbox_source_sha256"] == sandbox_source_sha256()
    assert manifest["security"]["checker_network"] == "none"
    assert manifest["security"]["checker_workspace_read_only"] is True
    assert manifest["security"]["cleanup_completed"] is True


def test_entrypoint_copies_image_config_to_writable_state(tmp_path: Path):
    source = tmp_path / "template"
    destination = tmp_path / "runtime"
    source.mkdir()
    (source / "models.json").write_text("{}")
    result = prepare_pi_config(source, destination)
    assert result == destination
    assert (destination / "models.json").read_text() == "{}"


def test_doctor_requires_real_model_and_checks_both_definitions(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("harness.sandbox.find_engine", lambda: "docker")
    monkeypatch.delenv("MODEL_FILE", raising=False)
    assert doctor()["reason"] == "MODEL_FILE must name an existing GGUF"

    model = tmp_path / "model.gguf"
    model.write_text("stub")
    monkeypatch.setenv("MODEL_FILE", str(model))
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="27.0\n", stderr="")

    monkeypatch.setattr("harness.sandbox.subprocess.run", fake_run)
    result = doctor()
    assert result["ready"] is True
    assert len(result["definitions"]) == 2
    assert len(calls) == 3


def _fake_actuator(monkeypatch, tmp_path: Path, script: str, *args: str) -> tuple[Path, Path, list]:
    """Run a real local process in place of the container; fake only Compose teardown."""
    work = tmp_path / "work.json"
    work.write_text(json.dumps({"run_id": "run-stream", "limits": {"max_wall_seconds": 1}}))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("bounded prompt")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("harness.sandbox.find_engine", lambda: "docker")
    monkeypatch.setattr("harness.sandbox.validate_worktree", lambda *_args: None)
    monkeypatch.setattr(
        "harness.sandbox.agent_command",
        lambda *_args: ([sys.executable, "-c", script, *args], {"LOCAL_HARNESS_RUN_ID": "run-stream"}, ""),
    )
    monkeypatch.setattr("harness.sandbox.subprocess.run", fake_run)
    return work, prompt, calls


def test_timeout_still_tears_down_compose_project_and_records_manifest(tmp_path: Path, monkeypatch):
    script = "import time; print('partial', flush=True); time.sleep(30)"
    work, prompt, calls = _fake_actuator(monkeypatch, tmp_path, script)
    artifacts = tmp_path / "artifacts"
    assert run_agent(work, prompt, tmp_path / "worktree", artifacts) == 124
    assert any("down" in command for command in calls)
    manifest = json.loads((artifacts / "sandbox-manifest.json").read_text())
    assert manifest["cleanup_exit_code"] == 0
    assert "exceeded wall-time" in (artifacts / "agent-stderr.txt").read_text()
    events = artifacts / "runner-events.jsonl"
    assert events.read_text() == "partial\n"
    assert manifest["runner_events_sha256"] == hashlib.sha256(events.read_bytes()).hexdigest()


def test_runner_events_reach_disk_while_actuator_is_running(tmp_path: Path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    events = artifacts / "runner-events.jsonl"
    # The actuator checks the evidence file itself before exiting: the line it just
    # flushed must already be on disk, not buffered in the harness.
    script = (
        "import pathlib, sys\n"
        'print(\'{"type": "agent_start"}\', flush=True)\n'
        "seen = pathlib.Path(sys.argv[1]).read_text()\n"
        "sys.exit(0 if 'agent_start' in seen else 3)\n"
    )
    work, prompt, _calls = _fake_actuator(monkeypatch, tmp_path, script, str(events))
    assert run_agent(work, prompt, tmp_path / "worktree", artifacts) == 0
    assert events.stat().st_mode & 0o777 == 0o600
    assert (artifacts / "agent-stderr.txt").stat().st_mode & 0o777 == 0o600


def test_streamed_evidence_refuses_planted_symbolic_link(tmp_path: Path, monkeypatch):
    work, prompt, calls = _fake_actuator(monkeypatch, tmp_path, "print('x')")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    target = tmp_path / "target"
    target.write_text("do not overwrite")
    (artifacts / "runner-events.jsonl").symlink_to(target)
    try:
        run_agent(work, prompt, tmp_path / "worktree", artifacts)
    except OSError:
        pass
    else:
        raise AssertionError("streamed evidence followed a symbolic link")
    assert target.read_text() == "do not overwrite"
    assert calls == []
