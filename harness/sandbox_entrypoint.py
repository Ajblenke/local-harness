"""Prepare writable pi state and wait for the private model router."""

from __future__ import annotations

import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def prepare_pi_config(
    source: Path = Path("/opt/pi-config-template"), destination: Path | None = None
) -> Path:
    destination = destination or Path(os.environ.get("PI_CODING_AGENT_DIR", "/tmp/pi-config"))
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return destination


def wait_for_router() -> None:
    url = os.environ.get("LOCAL_HARNESS_ROUTER_HEALTH")
    if not url:
        return
    deadline = time.monotonic() + float(os.environ.get("LOCAL_HARNESS_ROUTER_WAIT_SECONDS", "300"))
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise SystemExit(f"local-harness: model router did not become ready: {url}")


def main() -> None:
    prepare_pi_config()
    wait_for_router()
    os.execvp("pi", ["pi", *sys.argv[1:]])


if __name__ == "__main__":
    main()
