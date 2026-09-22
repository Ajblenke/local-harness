import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-local-config"


def test_installer_is_dry_run_by_default(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ | {"HOME": str(home), "XDG_STATE_HOME": str(tmp_path / "state")}

    result = subprocess.run([SCRIPT], env=env, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "config/pi/models.json" in result.stdout
    assert not (home / ".pi").exists()


def test_installer_applies_private_config_and_keeps_backups(tmp_path: Path):
    home = tmp_path / "home"
    models = home / ".pi" / "agent" / "models.json"
    settings = home / ".pi" / "agent" / "settings.json"
    models.parent.mkdir(parents=True)
    models.write_text("old models")
    settings.write_text("old settings")
    env = os.environ | {"HOME": str(home), "XDG_STATE_HOME": str(tmp_path / "state")}

    result = subprocess.run([SCRIPT, "--apply"], env=env, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert '"llama-cpp"' in models.read_text()
    assert settings.read_text() == "old settings"
    assert models.stat().st_mode & 0o777 == 0o600
    assert settings.stat().st_mode & 0o777 == 0o600
    backups = list((tmp_path / "state" / "local-harness" / "config-backups").glob("*/*"))
    assert any(path.read_text() == "old models" for path in backups)
    assert any(path.read_text() == "old settings" for path in backups)
