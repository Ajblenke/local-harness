import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace

from harness.controller import Decision, document_sha256
from harness.workflow import _write_json, run_config, run_iteration, run_scheduled


def test_decision_artifact_is_private(tmp_path: Path):
    path = tmp_path / "decision.json"
    _write_json(path, Decision("double_check", ("missing evidence",)).as_dict())
    assert path.stat().st_mode & 0o777 == 0o600
    assert "missing evidence" in path.read_text()


def test_iteration_mutates_once_then_uses_independent_observation(tmp_path: Path, monkeypatch):
    work = {
        "schema": "local-harness/work-order/v1",
        "run_id": "run-1",
        "objective": "one change",
        "base_commit": "a" * 40,
        "scope": {"read": ["**"], "write": ["file.txt"], "deny": []},
        "required_checks": ["unit"],
        "limits": {
            "min_changed_files": 1,
            "max_changed_files": 1,
            "max_tool_calls": 4,
            "max_wall_seconds": 30,
        },
        "approval": "human",
        "next_step": "review",
    }
    work_path = tmp_path / "work.json"
    work_path.write_text(json.dumps(work))
    artifacts = tmp_path / "artifacts"
    calls = []

    def fake_agent(_work, _prompt, _worktree, agent_artifacts, _model):
        calls.append("agent")
        completion = {
            "schema": "local-harness/completion/v1",
            "run_id": "run-1",
            "work_order_sha256": document_sha256(work),
            "agent_id": "agent",
            "status": "completed",
            "summary": "changed file",
            "changes": [{"path": "file.txt", "kind": "modified"}],
            "checks": [{"name": "unit", "status": "pass"}],
            "claims": [{"statement": "unit passes", "evidence_refs": ["check:unit"]}],
            "risks": [],
            "uncertainties": [],
            "requested_action": "advance",
        }
        agent_artifacts.mkdir(parents=True, exist_ok=True)
        (agent_artifacts / "completion.json").write_text(json.dumps(completion))
        return 0

    def fake_observer(_work, _output, **_kwargs):
        calls.append("observer")
        return {
            "schema": "local-harness/observation/v1",
            "run_id": "run-1",
            "work_order_sha256": document_sha256(work),
            "runner_id": "local-harness-runner",
            "base_commit": "a" * 40,
            "changes": [{"path": "file.txt", "kind": "modified"}],
            "checks": [
                {
                    "name": "unit",
                    "status": "pass",
                    "exit_code": 0,
                    "output_sha256": "a" * 64,
                    "output_ref": "checks/unit.log",
                }
            ],
            "checks_isolated": True,
            "sandbox": {
                "kind": "docker",
                "manifest_sha256": "b" * 64,
                "network": "internal",
                "read_scope_enforced": True,
            },
            "sandbox_violations": [],
            "telemetry_ref": "runner-events:abc",
            "telemetry_complete": True,
            "evidence": ["check:unit", "path:file.txt"],
            "tool_calls": 1,
            "wall_seconds": 1,
        }

    monkeypatch.setattr("harness.workflow.run_agent", fake_agent)
    monkeypatch.setattr("harness.workflow.observe", fake_observer)
    monkeypatch.setattr("harness.workflow.STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("harness.workflow.TELEMETRY_DB", tmp_path / "state" / "telemetry.sqlite3")
    decision = run_iteration(work_path, tmp_path / "worktree", artifacts)
    assert decision.outcome == "advance"
    assert calls == ["agent", "observer"]
    assert json.loads((artifacts / "decision.json").read_text())["outcome"] == "advance"
    indexed = json.loads((tmp_path / "state" / "contracts" / "run-1" / "decision.json").read_text())
    assert indexed["changes"] == [{"path": "file.txt", "kind": "modified"}]


def test_iteration_rejects_symlinked_completion(tmp_path: Path, monkeypatch):
    work = {
        "schema": "local-harness/work-order/v1",
        "run_id": "run-symlink",
        "objective": "one change",
        "base_commit": "a" * 40,
        "scope": {"read": ["**"], "write": ["file.txt"], "deny": []},
        "required_checks": ["unit"],
        "limits": {
            "min_changed_files": 1,
            "max_changed_files": 1,
            "max_tool_calls": 4,
            "max_wall_seconds": 30,
        },
        "approval": "human",
        "next_step": "review",
    }
    work_path = tmp_path / "work.json"
    work_path.write_text(json.dumps(work))

    def fake_agent(_work, _prompt, _worktree, agent_artifacts, _model):
        agent_artifacts.mkdir(parents=True, exist_ok=True)
        (agent_artifacts / "real.json").write_text("{}")
        (agent_artifacts / "completion.json").symlink_to("real.json")
        return 0

    monkeypatch.setattr("harness.workflow.run_agent", fake_agent)
    monkeypatch.setattr("harness.workflow.observe", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("harness.workflow.STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("harness.workflow.TELEMETRY_DB", tmp_path / "state" / "telemetry.sqlite3")
    decision = run_iteration(work_path, tmp_path / "worktree", tmp_path / "artifacts")
    assert decision.outcome == "halt"
    assert "symbolic link" in decision.reasons[-1]


def test_loop_config_rejects_unknown_fields(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"work_order": "w", "worktree": "t", "artifacts": "a", "typo": 1}))
    try:
        run_config(path)
    except ValueError as exc:
        assert "unexpected fields" in str(exc)
    else:
        raise AssertionError("unknown config field was accepted")


def test_iteration_rejects_artifacts_inside_worktree_before_writing(tmp_path: Path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    work = {
        "schema": "local-harness/work-order/v1",
        "run_id": "run-path",
        "objective": "one change",
        "base_commit": "a" * 40,
        "scope": {"read": ["**"], "write": ["file.txt"], "deny": []},
        "required_checks": ["unit"],
        "limits": {
            "min_changed_files": 1,
            "max_changed_files": 1,
            "max_tool_calls": 2,
            "max_wall_seconds": 30,
        },
        "approval": "human",
        "next_step": "review",
    }
    work_path = tmp_path / "work.json"
    work_path.write_text(json.dumps(work))
    artifacts = worktree / "artifacts"
    try:
        run_iteration(work_path, worktree, artifacts)
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("unsafe artifact path was accepted")
    assert not artifacts.exists()


def test_scheduled_loop_issues_unique_work_order_and_worktree(tmp_path: Path, monkeypatch):
    config = tmp_path / "maintenance.json"
    config.write_text(
        json.dumps(
            {
                "objective": "one safe scheduled improvement",
                "write": ["harness/**", "tests/**"],
                "checks": ["ruff", "pytest"],
            }
        )
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = []

    def fake_subprocess(command, **_kwargs):
        calls.append(command)
        if command[1:3] == ["status", "--porcelain"]:
            return SimpleNamespace(stdout="")
        if command[1:3] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(stdout="a" * 40 + "\n")
        if command[1:3] == ["worktree", "add"]:
            Path(command[-2]).mkdir(parents=True)
            return SimpleNamespace(stdout="")
        raise AssertionError(command)

    captured = {}

    def fake_iteration(work_order, worktree, evidence, model, memory):
        captured.update(
            work=json.loads(work_order.read_text()),
            worktree=worktree,
            evidence=evidence,
            model=model,
            memory=memory,
        )
        return Decision("advance", ())

    monkeypatch.setattr("harness.workflow.STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("harness.workflow.REPO_DIR", repo)
    monkeypatch.setattr("harness.workflow.subprocess.run", fake_subprocess)
    monkeypatch.setattr("harness.workflow.load_check_registry", lambda *_args: {"ruff": [], "pytest": []})
    monkeypatch.setattr("harness.workflow.run_iteration", fake_iteration)
    decision, run_root = run_scheduled(config)

    assert decision.outcome == "advance"
    assert captured["work"]["run_id"].startswith("maintenance-")
    assert captured["work"]["approval"] == "human"
    assert captured["worktree"].parent == run_root
    assert captured["evidence"].parent == run_root
    assert json.loads((run_root / "schedule-result.json").read_text())["outcome"] == "advance"
    assert any(command[1:3] == ["worktree", "add"] for command in calls)


def test_scheduled_loop_refuses_dirty_primary_checkout(tmp_path: Path, monkeypatch):
    config = tmp_path / "maintenance.json"
    config.write_text(json.dumps({"objective": "scheduled task", "write": ["harness/**"]}))
    monkeypatch.setattr("harness.workflow.STATE_DIR", tmp_path / "state")
    monkeypatch.setattr("harness.workflow.REPO_DIR", tmp_path)
    monkeypatch.setattr(
        "harness.workflow.subprocess.run", lambda *_args, **_kwargs: SimpleNamespace(stdout=" M file.py\n")
    )
    try:
        run_scheduled(config)
    except RuntimeError as exc:
        assert "clean primary checkout" in str(exc)
    else:
        raise AssertionError("dirty scheduled checkout was accepted")


def test_scheduled_loop_refuses_overlapping_invocation(tmp_path: Path, monkeypatch):
    config = tmp_path / "maintenance.json"
    config.write_text(json.dumps({"objective": "scheduled task", "write": ["harness/**"]}))
    state = tmp_path / "state"
    lock = state / "scheduled" / "maintenance" / "active.lock"
    lock.parent.mkdir(parents=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr("harness.workflow.STATE_DIR", state)
    try:
        try:
            run_scheduled(config)
        except RuntimeError as exc:
            assert "already active" in str(exc)
        else:
            raise AssertionError("overlapping scheduled invocation was accepted")
    finally:
        os.close(descriptor)
