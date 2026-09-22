# ruff: noqa: E501
"""Private local telemetry store and loopback-only observer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from harness.paths import REPO_DIR, STATE_DIR

TELEMETRY_LOG = STATE_DIR / "telemetry.jsonl"
TELEMETRY_DB = STATE_DIR / "telemetry.sqlite3"
TELEMETRY_SCHEMA = "local-harness/telemetry/v1"
MAX_EVENT_BYTES = 1024 * 1024


def _private_parent(path: Path) -> None:
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent_existed or path == TELEMETRY_DB:
        os.chmod(path.parent, 0o700)


def connect(path: Path = TELEMETRY_DB) -> sqlite3.Connection:
    if path.is_symlink():
        raise ValueError(f"refusing symbolic-link telemetry database: {path}")
    _private_parent(path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY, ts INTEGER NOT NULL, session TEXT,
          event TEXT NOT NULL, cwd TEXT, payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS events_ts ON events(ts DESC);
        CREATE TABLE IF NOT EXISTS sources (
          path TEXT PRIMARY KEY, inode INTEGER NOT NULL, offset INTEGER NOT NULL,
          prefix_sha256 TEXT NOT NULL DEFAULT ''
        );
        """
    )
    columns = {row["name"] for row in db.execute("PRAGMA table_info(sources)")}
    if "prefix_sha256" not in columns:
        db.execute("ALTER TABLE sources ADD COLUMN prefix_sha256 TEXT NOT NULL DEFAULT ''")
    os.chmod(path, 0o600)
    return db


def _valid_record(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    if "schema" in record and record["schema"] != TELEMETRY_SCHEMA:
        return False
    event_id = record.get("event_id")
    return (
        isinstance(record.get("event"), str)
        and bool(record["event"])
        and isinstance(record.get("ts"), int)
        and not isinstance(record["ts"], bool)
        and record["ts"] >= 0
        and (event_id is None or (isinstance(event_id, str) and bool(event_id)))
    )


def sync(source: Path = TELEMETRY_LOG, database: Path = TELEMETRY_DB) -> dict[str, int]:
    """Incrementally import valid JSONL records; malformed lines are counted, not trusted."""
    if not source.exists():
        return {"imported": 0, "invalid": 0}
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"telemetry source must be a regular, non-symbolic-link file: {source}")
    stat = source.stat()
    db = connect(database)
    row = db.execute(
        "SELECT inode, offset, prefix_sha256 FROM sources WHERE path = ?", (str(source),)
    ).fetchone()
    offset = row["offset"] if row and row["inode"] == stat.st_ino and row["offset"] <= stat.st_size else 0
    if offset and row["prefix_sha256"]:
        with source.open("rb") as prefix_stream:
            prefix = prefix_stream.read(offset)
        if hashlib.sha256(prefix).hexdigest() != row["prefix_sha256"]:
            offset = 0
    imported = invalid = 0
    with source.open("rb") as stream:
        stream.seek(offset)
        while True:
            line_start = stream.tell()
            line = stream.readline(MAX_EVENT_BYTES + 1)
            if not line:
                offset = stream.tell()
                break
            if len(line) > MAX_EVENT_BYTES and not line.endswith(b"\n"):
                while line and not line.endswith(b"\n"):
                    line = stream.readline(MAX_EVENT_BYTES + 1)
                invalid += 1
                offset = stream.tell()
                continue
            if not line.endswith(b"\n"):
                offset = line_start
                break
            try:
                record = json.loads(line)
                if not _valid_record(record):
                    raise ValueError("invalid telemetry envelope")
                event = record["event"]
                ts = record["ts"]
            except (TypeError, ValueError, json.JSONDecodeError):
                invalid += 1
                continue
            fallback = f"{source}:{line_start}:".encode() + line
            event_id = record.get("event_id") or hashlib.sha256(fallback).hexdigest()
            cursor = db.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, ts, record.get("session"), event, record.get("cwd"), json.dumps(record)),
            )
            imported += cursor.rowcount
    with source.open("rb") as prefix_stream:
        prefix_sha256 = hashlib.sha256(prefix_stream.read(offset)).hexdigest()
    db.execute(
        """INSERT INTO sources(path, inode, offset, prefix_sha256) VALUES (?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET inode=excluded.inode, offset=excluded.offset,
        prefix_sha256=excluded.prefix_sha256""",
        (str(source), stat.st_ino, offset, prefix_sha256),
    )
    db.commit()
    db.close()
    return {"imported": imported, "invalid": invalid}


def run_stats(run_id: str, database: Path = TELEMETRY_DB) -> dict[str, Any]:
    """Derive limits from runner-ingested events rather than agent claims."""
    db = connect(database)
    records = [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM events")]
    db.close()
    records = [record for record in records if record.get("run_id") == run_id]
    starts = [int(record["ts"]) for record in records if record.get("event") == "session_start"]
    ends = [int(record["ts"]) for record in records if record.get("event") == "agent_settled"]
    return {
        "tool_calls": sum(record.get("event") == "tool_start" for record in records),
        "wall_seconds": round((max(ends) - min(starts)) / 1000, 3) if starts and ends else 0.0,
        "complete": bool(starts and ends),
        "events": len(records),
    }


def _git_changes() -> list[str]:
    try:
        output = subprocess.run(
            ["git", "status", "--short"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout
        return output.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


def _decisions() -> list[dict[str, Any]]:
    root = STATE_DIR / "contracts"
    decisions = []
    for path in sorted(root.glob("*/decision.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            decision = json.loads(path.read_text())
            decisions.append(decision | {"run_id": path.parent.name})
        except (OSError, json.JSONDecodeError):
            continue
    return decisions[:20]


def snapshot(database: Path = TELEMETRY_DB, limit: int = 100) -> dict[str, Any]:
    db = connect(database)
    rows = db.execute("SELECT payload FROM events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    counts = db.execute("SELECT event, count(*) count FROM events GROUP BY event").fetchall()
    sessions = db.execute("SELECT count(DISTINCT session) count FROM events WHERE session != ''").fetchone()
    result = {
        "generated_at": int(time.time() * 1000),
        "sessions": sessions["count"],
        "counts": {row["event"]: row["count"] for row in counts},
        "events": [json.loads(row["payload"]) for row in rows],
        "changes": _git_changes(),
        "decisions": _decisions(),
    }
    db.close()
    return result


HTML = """<!doctype html><meta charset=utf-8><title>Local Harness</title>
<style>body{font:14px system-ui;background:#10141b;color:#e7edf5;margin:2rem}h1{font-size:20px}pre{white-space:pre-wrap;background:#18202b;padding:1rem;border-radius:8px}.ok{color:#76d39b}</style>
<h1>Local Harness telemetry <span class=ok>● local</span></h1><div id=summary></div>
<h2>Working tree</h2><pre id=changes></pre><h2>Decisions</h2><pre id=decisions></pre>
<h2>Events</h2><pre id=events>loading…</pre>
<script>async function load(){let d=await(await fetch('/api/snapshot')).json();summary.textContent=`${d.sessions} sessions · ${Object.values(d.counts).reduce((a,b)=>a+b,0)} events`;changes.textContent=d.changes.join("\n")||"clean";decisions.textContent=JSON.stringify(d.decisions,null,2);events.textContent=d.events.map(e=>new Date(e.ts).toLocaleTimeString()+"  "+e.event+"  "+JSON.stringify(e)).join("\n")}load();setInterval(load,2000)</script>"""


def serve(host: str, port: int, source: Path, database: Path) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("observer is loopback-only; use 127.0.0.1, ::1, or localhost")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/snapshot":
                sync(source, database)
                body = json.dumps(snapshot(database)).encode()
                content_type = "application/json"
            elif self.path == "/":
                body = HTML.encode()
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness.telemetry")
    sub = parser.add_subparsers(dest="command", required=True)
    sync_parser = sub.add_parser("sync")
    sync_parser.add_argument("--source", type=Path, default=TELEMETRY_LOG)
    sync_parser.add_argument("--database", type=Path, default=TELEMETRY_DB)
    observe = sub.add_parser("observe")
    observe.add_argument("--host", default="127.0.0.1")
    observe.add_argument("--port", type=int, default=8765)
    observe.add_argument("--source", type=Path, default=TELEMETRY_LOG)
    observe.add_argument("--database", type=Path, default=TELEMETRY_DB)
    args = parser.parse_args(argv)
    if args.command == "sync":
        print(json.dumps(sync(args.source, args.database)))
    else:
        serve(args.host, args.port, args.source, args.database)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
