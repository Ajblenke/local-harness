"""Command line entry point: harness <command>."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from harness import evals
from harness.client import DEFAULT_BASE_URL
from harness.controller import document_sha256, evaluate_contract, load_document, validate_work_order
from harness.handoff import DEFAULT_MODEL as DEFAULT_GEMINI_MODEL
from harness.handoff import create_handoff, run_handoff
from harness.judge import score_eval
from harness.observer import default_output, load_check_registry
from harness.observer import observe as build_observation
from harness.paths import RUNS_DIR, STATE_DIR
from harness.sandbox import doctor as sandbox_doctor
from harness.sandbox import render_prompt, run_agent
from harness.secureio import write_private, write_private_json
from harness.telemetry import TELEMETRY_DB, TELEMETRY_LOG, snapshot, sync
from harness.telemetry import serve as serve_telemetry
from harness.workflow import run_config, run_iteration, run_scheduled


def cmd_eval(args: argparse.Namespace) -> int:
    summary = evals.run_eval(
        model=args.model,
        preset=args.preset,
        scenario_names=args.scenario or None,
        base_url=args.base_url,
        label=args.label,
        save=not args.no_save,
        repeat=args.repeat,
        model_sha256=args.model_sha256,
        model_file=Path(args.model_file) if args.model_file else None,
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
    return 0 if models is not None else 1


def cmd_contract_check(args: argparse.Namespace) -> int:
    try:
        decision = evaluate_contract(
            load_document(Path(args.work_order)),
            load_document(Path(args.completion)),
            load_document(Path(args.observation)),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"outcome": "double_check", "reasons": [str(exc)]}
        print(json.dumps(result, indent=2) if args.json else f"double_check: {exc}")
        return 2
    if args.json:
        print(json.dumps(decision.as_dict(), indent=2))
    else:
        print(decision.outcome)
        for reason in decision.reasons:
            print(f"  - {reason}")
    return {"advance": 0, "double_check": 2, "halt": 3}[decision.outcome]


def cmd_contract_hash(args: argparse.Namespace) -> int:
    print(document_sha256(load_document(Path(args.work_order))))
    return 0


def cmd_contract_init(args: argparse.Namespace) -> int:
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    checks = list(dict.fromkeys(args.check or ["ruff", "pytest"]))
    deny = list(dict.fromkeys([".env", "**/.env", "secrets/**", "**/secrets/**", *(args.deny or [])]))
    write_scope = list(dict.fromkeys(args.write))
    work = {
        "schema": "local-harness/work-order/v1",
        "run_id": args.run_id or f"run-{uuid.uuid4().hex[:12]}",
        "objective": args.objective,
        "base_commit": base_commit,
        "scope": {"read": ["**"], "write": write_scope, "deny": deny},
        "required_checks": checks,
        "limits": {
            "min_changed_files": args.min_changed_files,
            "max_changed_files": args.max_changed_files,
            "max_tool_calls": args.max_tool_calls,
            "max_wall_seconds": args.max_wall_seconds,
        },
        "approval": "human",
        "next_step": args.next_step,
    }
    problems = validate_work_order(work)
    if problems:
        raise ValueError("invalid work order: " + "; ".join(problems))
    registry = load_check_registry(base_commit, Path.cwd())
    missing_checks = sorted(set(checks) - set(registry))
    if missing_checks:
        raise ValueError(f"checks are not registered at {base_commit}: {missing_checks}")
    destination = Path(args.output)
    write_private_json(destination, work)
    print(f"{destination}\nsha256 {document_sha256(work)}")
    return 0


def cmd_contract_observe(args: argparse.Namespace) -> int:
    work = load_document(Path(args.work_order))
    problems = validate_work_order(work)
    if problems:
        raise ValueError("invalid work order: " + "; ".join(problems))
    output = Path(args.output) if args.output else default_output(work["run_id"])
    observation = build_observation(
        Path(args.work_order),
        output,
        cwd=Path(args.cwd),
        telemetry_source=Path(args.telemetry_source),
        telemetry_database=Path(args.telemetry_database),
        sandbox_manifest=Path(args.sandbox_manifest) if args.sandbox_manifest else None,
        runner_events=Path(args.runner_events) if args.runner_events else None,
    )
    print(json.dumps(observation, indent=2))
    return 0


def cmd_contract_decide(args: argparse.Namespace) -> int:
    work = load_document(Path(args.work_order))
    problems = validate_work_order(work)
    if problems:
        raise ValueError("invalid work order: " + "; ".join(problems))
    completion = load_document(Path(args.completion))
    observation_path = Path(args.observation) if args.observation else default_output(work["run_id"])
    observation = load_document(observation_path)
    decision = evaluate_contract(work, completion, observation)
    decision_dir = STATE_DIR / "contracts" / work["run_id"]
    decision_path = decision_dir / "decision.json"
    write_private_json(
        decision_path,
        decision.as_dict()
        | {
            "recorded_at": int(time.time() * 1000),
            "objective": work["objective"],
            "changes": observation.get("changes", []),
            "checks": observation.get("checks", []),
            "artifact_dir": str(observation_path.parent.resolve()),
        },
    )
    print(json.dumps(decision.as_dict() | {"path": str(decision_path)}, indent=2))
    return {"advance": 0, "double_check": 2, "halt": 3}[decision.outcome]


def cmd_judge(args: argparse.Namespace) -> int:
    baseline = evals.load_run(args.baseline) if args.baseline else None
    result = score_eval(
        evals.load_run(args.run),
        minimum_repetitions=args.minimum_repetitions,
        baseline=baseline,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "pass" else 2


def cmd_telemetry_sync(args: argparse.Namespace) -> int:
    print(json.dumps(sync(Path(args.source), Path(args.database)), indent=2))
    return 0


def cmd_telemetry_snapshot(args: argparse.Namespace) -> int:
    sync(Path(args.source), Path(args.database))
    print(json.dumps(snapshot(Path(args.database), args.limit), indent=2))
    return 0


def cmd_observe(args: argparse.Namespace) -> int:
    print(f"telemetry dashboard: http://{args.host}:{args.port}")
    serve_telemetry(args.host, args.port, Path(args.source), Path(args.database))
    return 0


def cmd_sandbox_doctor(_args: argparse.Namespace) -> int:
    result = sandbox_doctor()
    print(json.dumps(result, indent=2))
    return 0 if result["ready"] else 2


def cmd_sandbox_run(args: argparse.Namespace) -> int:
    return run_agent(
        Path(args.work_order),
        Path(args.prompt),
        Path(args.worktree),
        Path(args.artifacts),
        args.model,
    )


def cmd_sandbox_prompt(args: argparse.Namespace) -> int:
    prompt = render_prompt(Path(args.work_order), Path(args.memory) if args.memory else None)
    write_private(Path(args.output), prompt)
    print(args.output)
    return 0


def cmd_loop_run(args: argparse.Namespace) -> int:
    decision = run_iteration(
        Path(args.work_order),
        Path(args.worktree),
        Path(args.artifacts),
        args.model,
        Path(args.memory) if args.memory else None,
    )
    print(json.dumps(decision.as_dict(), indent=2))
    return {"advance": 0, "double_check": 2, "halt": 3}[decision.outcome]


def cmd_loop_config(args: argparse.Namespace) -> int:
    decision = run_config(Path(args.config))
    print(json.dumps(decision.as_dict(), indent=2))
    return {"advance": 0, "double_check": 2, "halt": 3}[decision.outcome]


def cmd_loop_scheduled(args: argparse.Namespace) -> int:
    decision, run_root = run_scheduled(Path(args.config))
    print(json.dumps(decision.as_dict() | {"run_root": str(run_root)}, indent=2))
    return {"advance": 0, "double_check": 2, "halt": 3}[decision.outcome]


def cmd_handoff_create(args: argparse.Namespace) -> int:
    destination, manifest = create_handoff(
        args.objective,
        args.file,
        model=args.model,
        output=Path(args.output) if args.output else None,
    )
    print(f"created {destination}")
    print(f"review {destination / 'brief.md'}")
    print("run exactly once after review:")
    print(f"uv run harness handoff run {destination} --confirm {manifest['confirmation']}")
    return 0


def cmd_handoff_run(args: argparse.Namespace) -> int:
    result = run_handoff(Path(args.handoff), args.confirm, timeout=args.timeout)
    print(json.dumps(result, indent=2))
    return 0 if result["exit_code"] == 0 else 2


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
    p.add_argument("--repeat", type=int, default=1, help="repeat each case for reliability")
    identity = p.add_mutually_exclusive_group()
    identity.add_argument(
        "--model-file", help="hash this model artifact locally (required for a passing judge verdict)"
    )
    identity.add_argument(
        "--model-sha256", help="operator-attested digest (recorded, but insufficient for judge trust)"
    )
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

    p = sub.add_parser("contract", help="evaluate a controller continuation contract")
    contract_sub = p.add_subparsers(dest="contract_command", required=True)
    check = contract_sub.add_parser("check", help="compare work order, agent claim, and runner evidence")
    check.add_argument("work_order")
    check.add_argument("completion")
    check.add_argument("observation")
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_contract_check)

    digest = contract_sub.add_parser("hash", help="print the canonical work-order digest")
    digest.add_argument("work_order")
    digest.set_defaults(func=cmd_contract_hash)

    initialize = contract_sub.add_parser("init", help="issue a work order anchored to HEAD")
    initialize.add_argument("--objective", required=True)
    initialize.add_argument("--write", action="append", required=True)
    initialize.add_argument("--deny", action="append")
    initialize.add_argument("--check", action="append")
    initialize.add_argument("--next-step", default="present the verified change for human review")
    initialize.add_argument("--run-id")
    initialize.add_argument("--min-changed-files", type=int, default=1)
    initialize.add_argument("--max-changed-files", type=int, default=8)
    initialize.add_argument("--max-tool-calls", type=int, default=40)
    initialize.add_argument("--max-wall-seconds", type=int, default=900)
    initialize.add_argument("--output", required=True)
    initialize.set_defaults(func=cmd_contract_init)

    observation = contract_sub.add_parser("observe", help="run trusted checks and record git evidence")
    observation.add_argument("work_order")
    observation.add_argument("--output")
    observation.add_argument("--cwd", default=".")
    observation.add_argument("--telemetry-source", default=TELEMETRY_LOG)
    observation.add_argument("--telemetry-database", default=TELEMETRY_DB)
    observation.add_argument("--sandbox-manifest")
    observation.add_argument("--runner-events")
    observation.set_defaults(func=cmd_contract_observe)

    decide = contract_sub.add_parser("decide", help="persist a continuation decision")
    decide.add_argument("work_order")
    decide.add_argument("completion")
    decide.add_argument("--observation")
    decide.set_defaults(func=cmd_contract_decide)

    p = sub.add_parser("judge", help="apply transparent hard gates to a saved eval")
    p.add_argument("run")
    p.add_argument("--baseline", help="require no correctness regression in any scenario group")
    p.add_argument("--minimum-repetitions", type=int, default=1)
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("telemetry", help="manage the private telemetry store")
    telemetry_sub = p.add_subparsers(dest="telemetry_command", required=True)
    for name, function in (("sync", cmd_telemetry_sync), ("snapshot", cmd_telemetry_snapshot)):
        command = telemetry_sub.add_parser(name)
        command.add_argument("--source", default=TELEMETRY_LOG)
        command.add_argument("--database", default=TELEMETRY_DB)
        if name == "snapshot":
            command.add_argument("--limit", type=int, default=100)
        command.set_defaults(func=function)

    p = sub.add_parser("observe", help="serve the loopback-only telemetry dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--source", default=TELEMETRY_LOG)
    p.add_argument("--database", default=TELEMETRY_DB)
    p.set_defaults(func=cmd_observe)

    p = sub.add_parser("sandbox", help="inspect or run the Docker agent sandbox")
    sandbox_sub = p.add_subparsers(dest="sandbox_command", required=True)
    doctor = sandbox_sub.add_parser("doctor")
    doctor.set_defaults(func=cmd_sandbox_doctor)
    run = sandbox_sub.add_parser("run")
    run.add_argument("work_order")
    run.add_argument("prompt")
    run.add_argument("--worktree", required=True)
    run.add_argument("--artifacts", required=True)
    run.add_argument("--model", default="llama-cpp/qwen3.5-4b")
    run.set_defaults(func=cmd_sandbox_run)

    prompt = sandbox_sub.add_parser("prompt", help="render the bounded actuator prompt")
    prompt.add_argument("work_order")
    prompt.add_argument("--memory")
    prompt.add_argument("--output", required=True)
    prompt.set_defaults(func=cmd_sandbox_prompt)

    p = sub.add_parser("loop", help="run one sandboxed control-loop iteration")
    loop_sub = p.add_subparsers(dest="loop_command", required=True)
    run = loop_sub.add_parser("run")
    run.add_argument("work_order")
    run.add_argument("--worktree", required=True)
    run.add_argument("--artifacts", required=True)
    run.add_argument("--model", default="llama-cpp/qwen3.5-4b")
    run.add_argument("--memory", default=".agents/memory/local-harness-control-loop.md")
    run.set_defaults(func=cmd_loop_run)
    config = loop_sub.add_parser("config", help="run once from a concrete worktree/artifact config")
    config.add_argument("config")
    config.set_defaults(func=cmd_loop_config)
    scheduled = loop_sub.add_parser(
        "scheduled", help="issue a unique work order and worktree from a schedule template"
    )
    scheduled.add_argument("config")
    scheduled.set_defaults(func=cmd_loop_scheduled)

    p = sub.add_parser("handoff", help="create or run an explicitly approved Gemini handoff")
    handoff_sub = p.add_subparsers(dest="handoff_command", required=True)
    create = handoff_sub.add_parser("create", help="copy named files into a reviewable handoff")
    create.add_argument("--objective", required=True)
    create.add_argument("--file", action="append", required=True, help="named UTF-8 file (repeatable)")
    create.add_argument("--model", default=DEFAULT_GEMINI_MODEL)
    create.add_argument("--output", help="private output directory (defaults to local state)")
    create.set_defaults(func=cmd_handoff_create)
    run = handoff_sub.add_parser("run", help="run one fresh, isolated Gemini process")
    run.add_argument("handoff")
    run.add_argument("--confirm", required=True, help="digest printed by handoff create")
    run.add_argument("--timeout", type=int, default=600)
    run.set_defaults(func=cmd_handoff_run)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
