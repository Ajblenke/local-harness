import json
from pathlib import Path
from types import SimpleNamespace

from harness.runlog import (
    RUN_LOG_NAME,
    append_event,
    last_event,
    read_events,
    recover_run,
    recover_schedule,
)

RUN_ID = "nightly-20260101T000000-deadbeef"


def _run(tmp_path: Path) -> Path:
    run_root = tmp_path / RUN_ID
    run_root.mkdir()
    return run_root


def _fake_docker(monkeypatch) -> list:
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("harness.runlog.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr("harness.runlog.subprocess.run", fake_run)
    return calls


def test_events_are_private_ordered_and_schema_tagged(tmp_path: Path):
    run_root = _run(tmp_path)
    append_event(run_root, RUN_ID, "issued", base_commit="a" * 40)
    append_event(run_root, RUN_ID, "worktree_created")
    records, invalid = read_events(run_root)
    assert invalid == 0
    assert [(record["seq"], record["event"]) for record in records] == [
        (0, "issued"),
        (1, "worktree_created"),
    ]
    assert records[0]["data"] == {"base_commit": "a" * 40}
    assert all(record["schema"] == "local-harness/run-event/v1" for record in records)
    assert (run_root / RUN_LOG_NAME).stat().st_mode & 0o777 == 0o600


def test_torn_final_record_is_ignored_and_next_append_starts_a_new_line(tmp_path: Path):
    run_root = _run(tmp_path)
    append_event(run_root, RUN_ID, "issued")
    with (run_root / RUN_LOG_NAME).open("ab") as stream:
        stream.write(b'{"schema": "local-harness/run-ev')
    records, invalid = read_events(run_root)
    assert [record["event"] for record in records] == ["issued"] and invalid == 1
    append_event(run_root, RUN_ID, "worktree_created")
    records, invalid = read_events(run_root)
    assert [record["event"] for record in records] == ["issued", "worktree_created"]
    assert records[-1]["seq"] == 1 and invalid == 1


def test_ended_run_refuses_further_events(tmp_path: Path):
    run_root = _run(tmp_path)
    append_event(run_root, RUN_ID, "issued")
    append_event(run_root, RUN_ID, "failed", reason="boom")
    try:
        append_event(run_root, RUN_ID, "actuator_started")
    except ValueError as exc:
        assert "already ended" in str(exc)
    else:
        raise AssertionError("event appended after a terminal event")


def test_unknown_event_and_symbolic_link_log_are_rejected(tmp_path: Path):
    run_root = _run(tmp_path)
    try:
        append_event(run_root, RUN_ID, "retry")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown event accepted")
    target = tmp_path / "target"
    target.write_text("")
    (run_root / RUN_LOG_NAME).symlink_to(target)
    try:
        read_events(run_root)
    except ValueError:
        pass
    else:
        raise AssertionError("symbolic-link run log was trusted")


def test_recovery_tears_down_live_actuator_and_marks_double_check(tmp_path: Path, monkeypatch):
    calls = _fake_docker(monkeypatch)
    run_root = _run(tmp_path)
    for event in ("issued", "worktree_created", "actuator_started"):
        append_event(run_root, RUN_ID, event)
    (run_root / "worktree").mkdir()
    agent = run_root / "evidence" / "agent"
    agent.mkdir(parents=True)
    (agent / "runner-events.jsonl").write_text('{"type": "agent_start"}\n')

    summary = recover_run(run_root)

    assert summary["action"] == "abandoned"
    assert summary["runner_events_bytes"] > 0
    (down,) = calls
    assert down[-2:] == ["down", "--remove-orphans"]
    assert down[down.index("--project-name") + 1].startswith("local-harness-")
    record = last_event(run_root)
    assert record["event"] == "abandoned"
    assert record["data"]["after"] == "actuator_started"
    assert record["data"]["teardown_exit_code"] == 0
    result = json.loads((run_root / "schedule-result.json").read_text())
    assert result["outcome"] == "double_check" and "actuator_started" in result["reasons"][0]
    assert (run_root / "worktree").is_dir()


def test_recovery_skips_teardown_after_actuator_exited(tmp_path: Path, monkeypatch):
    calls = _fake_docker(monkeypatch)
    run_root = _run(tmp_path)
    for event in ("issued", "worktree_created", "actuator_started", "actuator_exited"):
        append_event(run_root, RUN_ID, event)
    assert recover_run(run_root)["action"] == "abandoned"
    assert calls == []


def test_recovery_rolls_forward_a_persisted_decision(tmp_path: Path, monkeypatch):
    calls = _fake_docker(monkeypatch)
    run_root = _run(tmp_path)
    for event in ("issued", "worktree_created", "actuator_started", "actuator_exited", "observed", "decided"):
        append_event(run_root, RUN_ID, event)
    (run_root / "evidence").mkdir()
    (run_root / "evidence" / "decision.json").write_text(json.dumps({"outcome": "advance", "reasons": []}))

    assert recover_run(run_root)["action"] == "completed"
    assert calls == []
    assert last_event(run_root)["event"] == "completed"
    result = json.loads((run_root / "schedule-result.json").read_text())
    assert result["outcome"] == "advance" and result["recovered"] is True


def test_recovery_leaves_finished_and_legacy_finished_runs_alone(tmp_path: Path):
    finished = _run(tmp_path)
    append_event(finished, RUN_ID, "issued")
    append_event(finished, RUN_ID, "completed", outcome="halt")
    legacy = tmp_path / "nightly-20250101T000000-cafef00d"
    legacy.mkdir()
    (legacy / "work-order.json").write_text("{}")
    (legacy / "schedule-result.json").write_text('{"outcome": "advance"}')
    assert recover_schedule(tmp_path) == []
    assert not (legacy / RUN_LOG_NAME).exists()


def test_recovery_marks_legacy_run_without_result(tmp_path: Path):
    legacy = tmp_path / "nightly-20250101T000000-cafef00d"
    legacy.mkdir()
    (legacy / "work-order.json").write_text("{}")
    (tmp_path / "active.lock").write_text("")
    (summary,) = recover_schedule(tmp_path)
    assert summary["last_event"] is None and summary["action"] == "abandoned"
    assert json.loads((legacy / "schedule-result.json").read_text())["outcome"] == "double_check"
    assert last_event(legacy)["event"] == "abandoned"
