"""Create and run explicitly approved, copy-only Gemini handoffs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from harness.paths import HANDOFF_DIR, STATE_DIR
from harness.secureio import write_private, write_private_json

SCHEMA = "local-harness/gemini-handoff/v1"
MAX_FILES = 32
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 600
DEFAULT_MODEL = "gemini-2.5-flash"
_MODEL = re.compile(r"^gemini-[A-Za-z0-9._-]+$")
_DENIED_NAMES = {
    ".env",
    ".git",
    ".pi",
    "auth.json",
    "credentials",
    "credentials.json",
    "secrets",
    "sessions",
    "telemetry",
    "vault",
}
_DENIED_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return _sha256(encoded)


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    path.chmod(0o700)


def _repo_root(cwd: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip()).resolve(strict=True)


def _reject_symlink_components(root: Path, path: Path) -> None:
    relative = path.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError(f"symbolic links cannot be handed off: {relative}")


def _validate_source(root: Path, cwd: Path, raw_path: str) -> tuple[Path, Path, bytes]:
    lexical = Path(raw_path)
    if not lexical.is_absolute():
        lexical = cwd / lexical
    lexical = Path(os.path.abspath(lexical))
    try:
        lexical.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"handoff path is outside repository: {raw_path}") from exc
    _reject_symlink_components(root, lexical)
    source = lexical.resolve(strict=True)
    try:
        relative = source.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"handoff path resolves outside repository: {raw_path}") from exc
    if not source.is_file():
        raise ValueError(f"handoff path is not a regular file: {raw_path}")
    lowered = [part.lower() for part in relative.parts]
    if any(part.startswith(".") or part in _DENIED_NAMES for part in lowered):
        raise ValueError(f"dotfiles, state, credentials, sessions, and vaults are denied: {relative}")
    if source.suffix.lower() in _DENIED_SUFFIXES:
        raise ValueError(f"credential-like file type is denied: {relative}")
    state = STATE_DIR.resolve(strict=False)
    if source == state or state in source.parents:
        raise ValueError(f"local harness state is denied: {relative}")
    data = source.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"handoff file exceeds {MAX_FILE_BYTES} bytes: {relative}")
    if b"\0" in data:
        raise ValueError(f"binary files are not supported: {relative}")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"handoff files must be UTF-8 text: {relative}") from exc
    return source, relative, data


def create_handoff(
    objective: str,
    files: list[str],
    *,
    model: str = DEFAULT_MODEL,
    cwd: Path | None = None,
    output: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Copy reviewed inputs into private state and return the handoff manifest."""
    if not objective.strip():
        raise ValueError("handoff objective cannot be empty")
    if not files:
        raise ValueError("at least one explicitly named file is required")
    if len(files) > MAX_FILES:
        raise ValueError(f"handoff is limited to {MAX_FILES} files")
    if not _MODEL.fullmatch(model):
        raise ValueError("Gemini model must be an explicit gemini-* model id")

    working_directory = (cwd or Path.cwd()).resolve(strict=True)
    root = _repo_root(working_directory)
    marker = root / ".escalate-ok"
    if marker.is_symlink() or not marker.is_file():
        raise ValueError(f"repository has not opted in; create and review {marker}")

    selected: list[tuple[Path, Path, bytes]] = []
    seen: set[str] = set()
    total = 0
    for raw_path in files:
        source, relative, data = _validate_source(root, working_directory, raw_path)
        key = relative.as_posix()
        if key in seen:
            raise ValueError(f"duplicate handoff path: {relative}")
        seen.add(key)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError(f"handoff exceeds {MAX_TOTAL_BYTES} total bytes")
        selected.append((source, relative, data))

    handoff_id = f"gemini-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    destination = (output or HANDOFF_DIR / handoff_id).resolve(strict=False)
    if destination == root or root in destination.parents:
        raise ValueError("handoff output must be outside the source repository")
    _private_dir(destination)
    workspace = destination / "workspace"
    workspace.mkdir(mode=0o700)

    entries = []
    for _source, relative, data in selected:
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(data)
        target.chmod(0o600)
        entries.append({"path": relative.as_posix(), "bytes": len(data), "sha256": _sha256(data)})

    core = {
        "schema": SCHEMA,
        "handoff_id": handoff_id,
        "created_at": int(time.time()),
        "objective": objective.strip(),
        "provider": "google",
        "model": model,
        "files": entries,
    }
    confirmation = _canonical_digest(core)
    manifest = core | {"confirmation": confirmation}
    write_private_json(destination / "manifest.json", manifest)
    inventory = "\n".join(
        f"- `{entry['path']}` ({entry['bytes']} bytes, sha256 `{entry['sha256']}`)" for entry in entries
    )
    brief = (
        "# Gemini handoff review\n\n"
        f"Objective: {objective.strip()}\n\n"
        f"Provider/model: `google/{model}`\n\n"
        "Only these copied UTF-8 files will be visible to Gemini:\n\n"
        f"{inventory}\n\n"
        f"Confirmation digest: `{confirmation}`\n\n"
        "The source repository, Git metadata, local sessions, telemetry, credentials, dotfiles, "
        "vault, and home directory are not mounted. The copied workspace may be edited; changes "
        "are never applied to the source repository automatically.\n"
    )
    write_private(destination / "brief.md", brief)
    return destination, manifest


def _load_manifest(handoff: Path) -> dict[str, Any]:
    if handoff.is_symlink() or not handoff.is_dir():
        raise ValueError("handoff must be a real directory")
    manifest_path = handoff / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("handoff manifest is missing or symbolic")
    manifest = json.loads(manifest_path.read_text())
    required = {
        "schema",
        "handoff_id",
        "created_at",
        "objective",
        "provider",
        "model",
        "files",
        "confirmation",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValueError("invalid handoff manifest fields")
    if manifest["schema"] != SCHEMA or manifest["provider"] != "google":
        raise ValueError("unsupported handoff manifest")
    if not isinstance(manifest["objective"], str) or not manifest["objective"].strip():
        raise ValueError("invalid handoff objective")
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        raise ValueError("invalid handoff file list")
    if not isinstance(manifest["model"], str) or not _MODEL.fullmatch(manifest["model"]):
        raise ValueError("invalid Gemini model in handoff manifest")
    core = {key: manifest[key] for key in required - {"confirmation"}}
    if manifest["confirmation"] != _canonical_digest(core):
        raise ValueError("handoff manifest digest does not match its contents")
    return manifest


def _verify_workspace(handoff: Path, manifest: dict[str, Any]) -> Path:
    workspace = handoff / "workspace"
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError("handoff workspace is missing or symbolic")
    expected: set[str] = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256"}:
            raise ValueError("invalid file entry in handoff manifest")
        if (
            not isinstance(entry["path"], str)
            or not isinstance(entry["bytes"], int)
            or not isinstance(entry["sha256"], str)
        ):
            raise ValueError("invalid file values in handoff manifest")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("invalid workspace path in handoff manifest")
        target = workspace / relative
        _reject_symlink_components(workspace, target)
        if not target.is_file():
            raise ValueError(f"handoff file is missing: {relative}")
        data = target.read_bytes()
        if len(data) != entry["bytes"] or _sha256(data) != entry["sha256"]:
            raise ValueError(f"handoff file changed after review: {relative}")
        expected.add(relative.as_posix())
    actual = {
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != expected:
        raise ValueError(f"unreviewed workspace files found: {sorted(actual - expected)}")
    return workspace


def build_command(workspace: Path, model: str) -> list[str]:
    if not _MODEL.fullmatch(model):
        raise ValueError("Gemini model must be an explicit gemini-* model id")
    if shutil.which("bwrap") is None:
        raise RuntimeError("bubblewrap (bwrap) is required for Gemini filesystem isolation")
    pi_executable = shutil.which("pi")
    if pi_executable is None:
        raise RuntimeError("pi is not installed")
    pi_path = Path(pi_executable).resolve(strict=True)
    try:
        pi_path.relative_to("/usr")
    except ValueError as exc:
        raise RuntimeError("pi must be installed below /usr for the isolated handoff") from exc
    command = [
        "bwrap",
        "--unshare-all",
        "--share-net",
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib",
        "/lib64",
        "--ro-bind",
        "/etc/ssl",
        "/etc/ssl",
    ]
    for path in ("/etc/hosts", "/etc/nsswitch.conf", "/etc/resolv.conf"):
        if Path(path).exists():
            command.extend(["--ro-bind", path, path])
    command.extend(
        [
            "--dev",
            "/dev",
            "--dir",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/home",
            "--dir",
            "/home/agent",
            "--bind",
            str(workspace),
            "/workspace",
            "--chdir",
            "/workspace",
            str(pi_path),
            "--provider",
            "google",
            "--model",
            model,
            "--print",
            "--no-session",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--tools",
            "read,grep,find,ls,edit,write",
        ]
    )
    return command


def _workspace_snapshot(workspace: Path) -> dict[str, str]:
    return {
        path.relative_to(workspace).as_posix(): _sha256(path.read_bytes())
        for path in workspace.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def run_handoff(
    handoff: Path,
    confirmation: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run one fresh Gemini process over reviewed copies, without retries."""
    if handoff.is_symlink():
        raise ValueError("handoff directory cannot be a symbolic link")
    handoff = handoff.resolve(strict=True)
    manifest = _load_manifest(handoff)
    if confirmation != manifest["confirmation"]:
        raise ValueError("confirmation does not match the reviewed handoff digest")
    if (handoff / "result.json").exists():
        raise ValueError("handoff has already been run; create a new handoff to run again")
    workspace = _verify_workspace(handoff, manifest)
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")
    if timeout < 1 or timeout > 3600:
        raise ValueError("timeout must be between 1 and 3600 seconds")
    model = manifest["model"]

    before = _workspace_snapshot(workspace)
    prompt = (
        "Work only on the copied files in /workspace. Do not request credentials, hidden files, "
        "local history, telemetry, sessions, or broader access. Do not claim changes outside this "
        "workspace. Complete this objective and finish with a concise summary of findings and edits:\n\n"
        + manifest["objective"]
    )
    environment = {
        "GEMINI_API_KEY": api_key,
        "HOME": "/home/agent",
        "LANG": "C.UTF-8",
        "NO_COLOR": "1",
        "PATH": "/usr/bin:/bin",
        "PI_CODING_AGENT_DIR": "/tmp/pi-config",
        "PI_TELEMETRY": "0",
    }
    started = time.time()
    try:
        completed = subprocess.run(
            build_command(workspace, model),
            input=prompt,
            capture_output=True,
            text=True,
            env=environment,
            timeout=timeout,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        timed_out = True
    after = _workspace_snapshot(workspace)
    paths = sorted(before.keys() | after.keys())
    changes = [
        {
            "path": path,
            "kind": "added" if path not in before else "deleted" if path not in after else "modified",
        }
        for path in paths
        if before.get(path) != after.get(path)
    ]
    write_private(handoff / "response.txt", stdout)
    write_private(handoff / "stderr.txt", stderr)
    result = {
        "schema": "local-harness/gemini-result/v1",
        "handoff_id": manifest["handoff_id"],
        "provider": "google",
        "model": model,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_seconds": round(time.time() - started, 3),
        "changes": changes,
        "response": "response.txt",
        "stderr": "stderr.txt",
    }
    write_private_json(handoff / "result.json", result)
    return result
