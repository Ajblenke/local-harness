import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.handoff import build_command, create_handoff, run_handoff


def opted_in_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", repo], check=True)
    (repo / ".escalate-ok").write_text("reviewed\n")
    (repo / "src").mkdir()
    (repo / "src" / "example.py").write_text("print('safe')\n")
    return repo


def test_create_handoff_copies_only_named_text_with_private_permissions(tmp_path: Path):
    repo = opted_in_repo(tmp_path)
    destination, manifest = create_handoff(
        "Review this function",
        ["src/example.py"],
        cwd=repo,
        output=tmp_path / "handoff",
    )

    assert manifest["provider"] == "google"
    assert manifest["model"] == "gemini-2.5-flash"
    assert [entry["path"] for entry in manifest["files"]] == ["src/example.py"]
    assert (destination / "workspace" / "src" / "example.py").read_text() == "print('safe')\n"
    assert destination.stat().st_mode & 0o777 == 0o700
    assert (destination / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert (destination / "brief.md").stat().st_mode & 0o777 == 0o600
    assert str(repo) not in (destination / "brief.md").read_text()


def test_create_handoff_requires_opt_in_and_rejects_secrets_and_symlinks(tmp_path: Path):
    repo = opted_in_repo(tmp_path)
    (repo / ".escalate-ok").unlink()
    with pytest.raises(ValueError, match="not opted in"):
        create_handoff("review", ["src/example.py"], cwd=repo, output=tmp_path / "one")

    (repo / ".escalate-ok").write_text("reviewed\n")
    (repo / ".env").write_text("TOKEN=nope\n")
    with pytest.raises(ValueError, match="denied"):
        create_handoff("review", [".env"], cwd=repo, output=tmp_path / "two")

    (repo / "link.py").symlink_to("src/example.py")
    with pytest.raises(ValueError, match="symbolic"):
        create_handoff("review", ["link.py"], cwd=repo, output=tmp_path / "three")


def test_build_command_disables_context_and_shell_and_mounts_only_copy(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("harness.handoff.shutil.which", lambda name: f"/usr/bin/{name}")

    command = build_command(workspace, "gemini-2.5-flash")

    assert "--no-session" in command
    assert "--no-extensions" in command
    assert "--no-skills" in command
    assert "--no-context-files" in command
    assert command[command.index("--tools") + 1] == "read,grep,find,ls,edit,write"
    assert "bash" not in command[command.index("--tools") + 1]
    assert str(workspace) in command
    assert str(Path.home()) not in command
    assert "GEMINI_API_KEY" not in command


def test_run_handoff_requires_digest_and_invokes_gemini_once(tmp_path: Path, monkeypatch):
    repo = opted_in_repo(tmp_path)
    destination, manifest = create_handoff(
        "Review this function",
        ["src/example.py"],
        cwd=repo,
        output=tmp_path / "handoff",
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.setattr("harness.handoff.shutil.which", lambda name: f"/usr/bin/{name}")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="review complete\n", stderr="")

    monkeypatch.setattr("harness.handoff.subprocess.run", fake_run)
    with pytest.raises(ValueError, match="confirmation"):
        run_handoff(destination, "wrong")

    result = run_handoff(destination, manifest["confirmation"])

    assert result["exit_code"] == 0
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert kwargs["env"]["GEMINI_API_KEY"] == "test-secret"
    assert "test-secret" not in json.dumps(command)
    assert command[command.index("--model") + 1] == manifest["model"]
    assert "Review this function" == kwargs["input"].split("\n\n")[-1]
    assert (destination / "response.txt").read_text() == "review complete\n"
    assert (destination / "result.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="already been run"):
        run_handoff(destination, manifest["confirmation"])


def test_run_rejects_workspace_changed_after_review(tmp_path: Path, monkeypatch):
    repo = opted_in_repo(tmp_path)
    destination, manifest = create_handoff(
        "Review this function",
        ["src/example.py"],
        cwd=repo,
        output=tmp_path / "handoff",
    )
    (destination / "workspace" / "src" / "example.py").write_text("changed\n")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")

    with pytest.raises(ValueError, match="changed after review"):
        run_handoff(destination, manifest["confirmation"])


def test_api_key_is_not_persisted(tmp_path: Path, monkeypatch):
    repo = opted_in_repo(tmp_path)
    destination, manifest = create_handoff(
        "Review this function",
        ["src/example.py"],
        cwd=repo,
        output=tmp_path / "handoff",
    )
    monkeypatch.setenv("GEMINI_API_KEY", "never-write-this-value")
    monkeypatch.setattr("harness.handoff.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "harness.handoff.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )

    run_handoff(destination, manifest["confirmation"])

    persisted = "\n".join(
        path.read_text(errors="replace") for path in destination.rglob("*") if path.is_file()
    )
    assert "never-write-this-value" not in persisted
    assert os.environ["GEMINI_API_KEY"] == "never-write-this-value"
