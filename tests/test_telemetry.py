import json
from pathlib import Path

from harness.telemetry import MAX_EVENT_BYTES, run_stats, serve, snapshot, sync


def test_sync_is_incremental_and_private(tmp_path: Path):
    source = tmp_path / "events.jsonl"
    database = tmp_path / "state" / "events.sqlite3"
    record = {"ts": 1, "event": "turn_start", "session": "s1", "event_id": "e1"}
    source.write_text(json.dumps(record) + "\nnot-json\n")
    assert sync(source, database) == {"imported": 1, "invalid": 1}
    assert sync(source, database) == {"imported": 0, "invalid": 0}
    assert database.stat().st_mode & 0o777 == 0o600
    view = snapshot(database)
    assert view["sessions"] == 1
    assert view["counts"] == {"turn_start": 1}


def test_sync_handles_rotation(tmp_path: Path):
    source = tmp_path / "events.jsonl"
    database = tmp_path / "events.sqlite3"
    source.write_text('{"ts":1,"event":"one","event_id":"one"}\n')
    sync(source, database)
    source.write_text('{"ts":2,"event":"two","event_id":"two"}\n')
    sync(source, database)
    assert snapshot(database)["counts"] == {"one": 1, "two": 1}


def test_sync_waits_for_complete_jsonl_line(tmp_path: Path):
    source = tmp_path / "events.jsonl"
    database = tmp_path / "events.sqlite3"
    source.write_text('{"ts":1,"event":"one"')
    assert sync(source, database) == {"imported": 0, "invalid": 0}
    with source.open("a") as stream:
        stream.write("}\n")
    assert sync(source, database) == {"imported": 1, "invalid": 0}


def test_run_stats_requires_start_and_settled(tmp_path: Path):
    source = tmp_path / "events.jsonl"
    database = tmp_path / "events.sqlite3"
    records = [
        {"ts": 1000, "event": "session_start", "run_id": "run-1", "event_id": "a"},
        {"ts": 1500, "event": "tool_start", "run_id": "run-1", "event_id": "b"},
        {"ts": 3000, "event": "agent_settled", "run_id": "run-1", "event_id": "c"},
    ]
    source.write_text("".join(json.dumps(record) + "\n" for record in records))
    sync(source, database)
    assert run_stats("run-1", database) == {
        "tool_calls": 1,
        "wall_seconds": 2.0,
        "complete": True,
        "events": 3,
    }


def test_sync_rejects_wrong_schema_boolean_time_and_bad_event_id(tmp_path: Path):
    source = tmp_path / "events.jsonl"
    database = tmp_path / "events.sqlite3"
    records = [
        {"schema": "unknown", "ts": 1, "event": "turn_start"},
        {"ts": True, "event": "turn_start"},
        {"ts": 1, "event": "turn_start", "event_id": {"not": "text"}},
    ]
    source.write_text("".join(json.dumps(record) + "\n" for record in records))
    assert sync(source, database) == {"imported": 0, "invalid": 3}


def test_sync_rejects_symlinks_and_bounds_event_size(tmp_path: Path):
    target = tmp_path / "target.jsonl"
    target.write_text('{"ts":1,"event":"hidden"}\n')
    source = tmp_path / "events.jsonl"
    source.symlink_to(target)
    try:
        sync(source, tmp_path / "events.sqlite3")
    except ValueError as exc:
        assert "non-symbolic-link" in str(exc)
    else:
        raise AssertionError("symbolic-link telemetry source was accepted")

    source.unlink()
    source.write_bytes(b"x" * (MAX_EVENT_BYTES + 1) + b'\n{"ts":2,"event":"valid"}\n')
    database = tmp_path / "bounded.sqlite3"
    assert sync(source, database) == {"imported": 1, "invalid": 1}
    assert snapshot(database)["counts"] == {"valid": 1}


def test_connect_rejects_symbolic_link_database(tmp_path: Path):
    source = tmp_path / "events.jsonl"
    source.write_text('{"ts":1,"event":"one"}\n')
    target = tmp_path / "target.sqlite3"
    target.write_text("do not overwrite")
    database = tmp_path / "events.sqlite3"
    database.symlink_to(target)
    try:
        sync(source, database)
    except ValueError as exc:
        assert "symbolic-link telemetry database" in str(exc)
    else:
        raise AssertionError("symbolic-link telemetry database was accepted")
    assert target.read_text() == "do not overwrite"


def test_custom_database_preserves_existing_parent_permissions(tmp_path: Path):
    tmp_path.chmod(0o755)
    source = tmp_path / "events.jsonl"
    source.write_text('{"ts":1,"event":"one"}\n')
    sync(source, tmp_path / "events.sqlite3")
    assert tmp_path.stat().st_mode & 0o777 == 0o755


def test_dashboard_is_loopback_only_and_starts_server(tmp_path: Path, monkeypatch):
    try:
        serve("0.0.0.0", 8765, tmp_path / "source", tmp_path / "db")
    except ValueError as exc:
        assert "loopback-only" in str(exc)
    else:
        raise AssertionError("non-loopback dashboard bind was accepted")

    seen = {}

    class FakeServer:
        def __init__(self, address, handler):
            seen["address"] = address
            seen["handler"] = handler

        def serve_forever(self):
            seen["served"] = True

    monkeypatch.setattr("harness.telemetry.ThreadingHTTPServer", FakeServer)
    serve("127.0.0.1", 8765, tmp_path / "source", tmp_path / "db")
    assert seen["address"] == ("127.0.0.1", 8765)
    assert seen["served"] is True
