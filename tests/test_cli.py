import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from harness.cli import cmd_contract_init


def test_contract_init_is_private_deduplicated_and_registered(tmp_path: Path, monkeypatch):
    commit = "a" * 40
    monkeypatch.setattr(
        "harness.cli.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=commit + "\n"),
    )
    monkeypatch.setattr(
        "harness.cli.load_check_registry",
        lambda *_args, **_kwargs: {"ruff": ["ruff"], "pytest": ["pytest"]},
    )
    output = tmp_path / "private" / "work.json"
    args = argparse.Namespace(
        run_id="run-cli",
        objective="one change",
        write=["harness/**", "harness/**"],
        deny=[".env"],
        check=["ruff", "ruff", "pytest"],
        min_changed_files=1,
        max_changed_files=2,
        max_tool_calls=3,
        max_wall_seconds=30,
        next_step="review",
        output=str(output),
    )
    assert cmd_contract_init(args) == 0
    work = json.loads(output.read_text())
    assert work["scope"]["write"] == ["harness/**"]
    assert work["required_checks"] == ["ruff", "pytest"]
    assert work["scope"]["deny"].count(".env") == 1
    assert output.stat().st_mode & 0o777 == 0o600
