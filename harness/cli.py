"""Command line entry point: harness <command>."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from harness import evals
from harness.client import DEFAULT_BASE_URL
from harness.paths import RUNS_DIR, STATE_DIR


def cmd_eval(args: argparse.Namespace) -> int:
    summary = evals.run_eval(
        model=args.model,
        preset=args.preset,
        scenario_names=args.scenario or None,
        base_url=args.base_url,
        label=args.label,
        save=not args.no_save,
    )
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        print()
    else:
        print(evals.format_run(summary))
    return 0 if summary["passed"] == summary["total"] else 1


def cmd_compare(args: argparse.Namespace) -> int:
    print(evals.format_compare(evals.load_run(args.a), evals.load_run(args.b)))
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    runs = evals.list_runs()
    if not runs:
        print(f"no runs yet in {RUNS_DIR}")
        return 0
    for run in runs:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(run["ts"]))
        label = f"  {run['label']}" if run.get("label") else ""
        print(f"  {stamp}  {run['score']:<6} {run['model']:<40} {run['preset']}{label}")
        print(f"      {Path(run['path']).name}")
    return 0


def _get_json(url: str, timeout: float = 5.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def cmd_status(args: argparse.Namespace) -> int:
    models = _get_json(f"{args.base_url}/models")
    if models is None:
        print(f"router not reachable at {args.base_url} (start it with: llm-serve start)")
    else:
        print(f"router {args.base_url}")
        for m in models.get("data", []):
            state = m.get("status", {}).get("value", "?")
            if state != "unloaded" or args.all:
                print(f"  {state:<10} {m['id']}")

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        print(f"gpu   {out}")
    except (OSError, subprocess.SubprocessError):
        pass

    active = []
    for status_file in Path("/tmp").glob("pi-subagents-*/async-subagent-runs/*/status.json"):
        try:
            data = json.loads(status_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("state") in {"running", "paused"} or args.all:
            active.append((status_file.parent.name, data))
    print(f"subagent runs: {len(active)} active")
    for run_id, data in active:
        print(f"  {data.get('state', '?'):<8} {run_id}  {data.get('agent', '')}")

    print(f"state {STATE_DIR}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harness", description=__doc__)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="llama-server router URL")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("eval", help="run scenarios against a model with a sampling preset")
    p.add_argument("--model", required=True, help="model id as listed by /models")
    p.add_argument("--preset", default="greedy", help="name of a file in evals/presets")
    p.add_argument("--scenario", action="append", help="limit to one scenario file (repeatable)")
    p.add_argument("--label", default="", help="free text tag stored with the run")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.add_argument("--no-save", action="store_true", help="do not write the run to the state dir")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("compare", help="diff two saved runs case by case")
    p.add_argument("a", help="run path, file name, or unique prefix")
    p.add_argument("b")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("runs", help="list saved runs")
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("status", help="loaded models, GPU memory, active subagent runs")
    p.add_argument("--all", action="store_true", help="include unloaded models and finished runs")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
