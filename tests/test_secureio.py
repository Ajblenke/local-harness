from pathlib import Path

from harness.secureio import write_private


def test_private_write_replaces_untrusted_symlink(tmp_path: Path):
    target = tmp_path / "target"
    target.write_text("do not overwrite")
    output = tmp_path / "output"
    output.symlink_to(target)
    write_private(output, "runner evidence")
    assert target.read_text() == "do not overwrite"
    assert output.read_text() == "runner evidence"
    assert not output.is_symlink()
    assert output.stat().st_mode & 0o777 == 0o600


def test_private_write_preserves_existing_parent_permissions(tmp_path: Path):
    tmp_path.chmod(0o755)
    write_private(tmp_path / "private.json", "evidence")
    assert tmp_path.stat().st_mode & 0o777 == 0o755
