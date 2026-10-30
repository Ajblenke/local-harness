"""Atomic private writes for paths adjacent to untrusted agent output."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_private(path: Path, content: str) -> None:
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent_existed:
        os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
        fsync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def fsync_directory(path: Path) -> None:
    """Persist a rename or a new entry in ``path`` across power loss."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    write_private(path, json.dumps(value, indent=2) + "\n")
