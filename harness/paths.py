"""Where runtime data lives.

Nothing under STATE_DIR is ever committed or handed to a cloud model. It holds
eval runs, telemetry, reports, and handoff scratch directories.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "local-harness"
RUNS_DIR = STATE_DIR / "runs"
REPORTS_DIR = STATE_DIR / "reports"
HANDOFF_DIR = STATE_DIR / "handoffs"

SCENARIOS_DIR = REPO_DIR / "evals" / "scenarios"
PRESETS_DIR = REPO_DIR / "evals" / "presets"
