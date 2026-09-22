import os
import signal
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "llm-serve"


def environment(tmp_path: Path, preset: Path) -> dict[str, str]:
    return os.environ | {
        "HOME": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "LLM_PRESET": str(preset),
        "LLAMA_SERVER": "/bin/true",
        "LLM_PORT": "49199",
    }


def write_preset(tmp_path: Path, model_path: Path) -> Path:
    preset = tmp_path / "models.ini"
    preset.write_text(
        "\n".join(
            (
                "version = 1",
                "[*]",
                "np = 1",
                "[test-model]",
                f"model = {model_path}",
                "c = 4096",
                "",
            )
        )
    )
    return preset


def test_models_lists_configured_ids(tmp_path: Path):
    model = tmp_path / "model.gguf"
    model.touch()
    preset = write_preset(tmp_path, model)

    result = subprocess.run(
        [SCRIPT, "models"],
        env=environment(tmp_path, preset),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "test-model\n"


def test_doctor_rejects_missing_gguf(tmp_path: Path):
    preset = write_preset(tmp_path, tmp_path / "missing.gguf")

    result = subprocess.run(
        [SCRIPT, "doctor"],
        env=environment(tmp_path, preset),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "GGUF not found" in result.stdout


def test_load_rejects_path_instead_of_model_id(tmp_path: Path):
    model = tmp_path / "model.gguf"
    model.touch()
    preset = write_preset(tmp_path, model)

    result = subprocess.run(
        [SCRIPT, "load", str(model)],
        env=environment(tmp_path, preset),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unknown model id" in result.stderr
    assert "test-model" in result.stderr


def test_doctor_creates_private_state_directory(tmp_path: Path):
    model = tmp_path / "model.gguf"
    model.touch()
    preset = write_preset(tmp_path, model)

    result = subprocess.run(
        [SCRIPT, "doctor"],
        env=environment(tmp_path, preset),
        capture_output=True,
        text=True,
        check=False,
    )

    state = tmp_path / "state" / "llama-router"
    assert result.returncode == 0
    assert state.stat().st_mode & 0o777 == 0o700


def test_start_warm_status_and_stop_round_trip(tmp_path: Path):
    model = tmp_path / "model.gguf"
    model.touch()
    preset = write_preset(tmp_path, model)
    server = tmp_path / "fake_server.py"
    server.write_text(
        'import os, pathlib, time\npathlib.Path(os.environ["FAKE_ROUTER_STARTED"]).touch()\ntime.sleep(300)\n'
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

state_file = pathlib.Path(os.environ["FAKE_ROUTER_STATE"])
if not pathlib.Path(os.environ["FAKE_ROUTER_STARTED"]).exists():
    raise SystemExit(7)
state = state_file.read_text() if state_file.exists() else "unloaded"
url = next(arg for arg in sys.argv[1:] if arg.startswith("http"))
if url.endswith("/models/load"):
    state = "loaded"
    state_file.write_text(state)
    print("{}")
elif url.endswith("/models/unload"):
    state = "unloaded"
    state_file.write_text(state)
    print("{}")
elif url.endswith("/models"):
    print(json.dumps({"data": [{"id": "test-model", "status": {"value": state}}]}))
elif url.endswith("/health"):
    print("{}")
else:
    raise SystemExit(22)
"""
    )
    fake_curl.chmod(0o755)
    env = environment(tmp_path, preset) | {
        "LLAMA_SERVER": f"{sys.executable} {server}",
        "FAKE_ROUTER_STATE": str(tmp_path / "router.state"),
        "FAKE_ROUTER_STARTED": str(tmp_path / "router.started"),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    pid_file = tmp_path / "state" / "llama-router" / "server.pid"

    try:
        started = subprocess.run([SCRIPT, "start"], env=env, capture_output=True, text=True, check=False)
        assert started.returncode == 0, started.stderr

        warmed = subprocess.run(
            [SCRIPT, "warm", "test-model"], env=env, capture_output=True, text=True, check=False
        )
        assert warmed.returncode == 0, warmed.stderr
        assert "loaded: test-model" in warmed.stdout

        status = subprocess.run([SCRIPT, "status"], env=env, capture_output=True, text=True, check=False)
        assert status.returncode == 0, status.stderr
        assert "loaded     test-model" in status.stdout

        stopped = subprocess.run([SCRIPT, "stop"], env=env, capture_output=True, text=True, check=False)
        assert stopped.returncode == 0, stopped.stderr
        assert not pid_file.exists()
    finally:
        if pid_file.exists():
            pid = int(pid_file.read_text())
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
