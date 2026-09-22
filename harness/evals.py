"""Run scenario files against a model and score the results.

The point is a number, not a transcript. Run the same scenarios before and
after any change (model, quantization, context size, KV cache type, sampling
preset, system prompt) and compare the two runs.

A scenario file holds a system prompt, a tool list, and cases. A case is one
prompt plus what should happen:

  expect_calls          ordered tool calls that must appear, with the argument
                        values that matter ([] means: answer without tools)
  allow_extra_calls     tool names that may appear in addition, in any position
  tool_results          canned result per tool name; when present the case runs
                        for up to max_turns, feeding results back
  max_repeat_calls      how many times the same call (tool and args) may repeat
  expect_final_contains substrings the final answer must contain
  expect_final_contains_any at least one acceptable phrase must appear
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

from harness.client import (
    DEFAULT_BASE_URL,
    AuditedClient,
    AuditError,
    read_metrics,
    read_model_info,
    validate_tool_schema,
)
from harness.paths import PRESETS_DIR, RUNS_DIR, SCENARIOS_DIR
from harness.secureio import write_private_json
from harness.validation import validate_tool_arguments


def load_preset(name: str) -> dict[str, Any]:
    path = PRESETS_DIR / f"{name}.json"
    if not path.exists():
        available = ", ".join(p.stem for p in sorted(PRESETS_DIR.glob("*.json")))
        raise FileNotFoundError(f"no preset {name!r}; available: {available}")
    return json.loads(path.read_text())


def load_scenarios(names: list[str] | None = None) -> list[dict[str, Any]]:
    files = sorted(SCENARIOS_DIR.glob("*.json"))
    scenarios = []
    for path in files:
        scenario = json.loads(path.read_text())
        problems = validate_scenario(scenario)
        if problems:
            raise ValueError(f"{path}: invalid scenario: {'; '.join(problems)}")
        scenarios.append(scenario)
    if names:
        known = {s["name"] for s in scenarios}
        missing = set(names) - known
        if missing:
            raise FileNotFoundError(
                f"no scenario named {', '.join(sorted(missing))}; known: {', '.join(sorted(known))}"
            )
        scenarios = [s for s in scenarios if s["name"] in names]
    return scenarios


def validate_scenario(scenario: Any) -> list[str]:
    """Validate scenario semantics so misspelled expectations cannot be ignored."""
    if not isinstance(scenario, dict):
        return ["scenario must be an object"]
    problems: list[str] = []
    top_fields = {"name", "description", "system", "tools", "cases"}
    unexpected = sorted(set(scenario) - top_fields)
    if unexpected:
        problems.append(f"unexpected scenario fields {unexpected}")
    for key in ("name", "description", "system"):
        if not isinstance(scenario.get(key), str) or not scenario[key]:
            problems.append(f"{key} must be a non-empty string")
    tools = scenario.get("tools")
    if not isinstance(tools, list) or not tools:
        problems.append("tools must be a non-empty array")
        tools = []
    tool_schemas: dict[str, dict[str, Any]] = {}
    for index, tool in enumerate(tools):
        tool_problems = validate_tool_schema(tool)
        problems.extend(f"tool {index}: {problem}" for problem in tool_problems)
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict):
            function = tool["function"]
            name = function.get("name")
            if isinstance(name, str):
                if name in tool_schemas:
                    problems.append(f"duplicate tool {name!r}")
                elif isinstance(function.get("parameters"), dict):
                    tool_schemas[name] = function["parameters"]

    cases = scenario.get("cases")
    if not isinstance(cases, list) or not cases:
        problems.append("cases must be a non-empty array")
        return problems
    allowed_case_fields = {
        "name",
        "prompt",
        "tool_results",
        "expect_calls",
        "allow_extra_calls",
        "max_turns",
        "max_repeat_calls",
        "expect_final_contains",
        "expect_final_contains_any",
    }
    names: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"case {index}"
        if not isinstance(case, dict):
            problems.append(f"{prefix} must be an object")
            continue
        unexpected = sorted(set(case) - allowed_case_fields)
        if unexpected:
            problems.append(f"{prefix}: unexpected fields {unexpected}")
        name = case.get("name")
        if not isinstance(name, str) or not name:
            problems.append(f"{prefix}: name must be a non-empty string")
        elif name in names:
            problems.append(f"{prefix}: duplicate name {name!r}")
        else:
            names.add(name)
        if not isinstance(case.get("prompt"), str) or not case["prompt"]:
            problems.append(f"{prefix}: prompt must be a non-empty string")
        calls = case.get("expect_calls")
        if not isinstance(calls, list):
            problems.append(f"{prefix}: expect_calls must be an array")
            calls = []
        for call_index, call in enumerate(calls):
            call_prefix = f"{prefix} call {call_index}"
            if not isinstance(call, dict) or set(call) != {"tool", "args"}:
                problems.append(f"{call_prefix}: expected exactly tool and args")
                continue
            tool_name, arguments = call.get("tool"), call.get("args")
            if tool_name not in tool_schemas:
                problems.append(f"{call_prefix}: unknown tool {tool_name!r}")
                continue
            if not isinstance(arguments, dict):
                problems.append(f"{call_prefix}: args must be an object")
                continue
            properties = tool_schemas[tool_name].get("properties", {})
            for key, value in arguments.items():
                if key not in properties:
                    problems.append(f"{call_prefix}: unknown argument {key!r}")
                else:
                    problems.extend(
                        f"{call_prefix}: {problem}"
                        for problem in validate_tool_arguments(value, properties[key], f"$.{key}")
                    )
        for key in ("allow_extra_calls", "expect_final_contains", "expect_final_contains_any"):
            values = case.get(key, [])
            if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
                problems.append(f"{prefix}: {key} must be a string array")
        for tool_name in case.get("allow_extra_calls", []):
            if tool_name not in tool_schemas:
                problems.append(f"{prefix}: unknown allowed tool {tool_name!r}")
        results = case.get("tool_results", {})
        if not isinstance(results, dict) or not all(
            name in tool_schemas and isinstance(value, str) for name, value in results.items()
        ):
            problems.append(f"{prefix}: tool_results must map known tools to strings")
        for key in ("max_turns", "max_repeat_calls"):
            value = case.get(key, 1)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                problems.append(f"{prefix}: {key} must be a positive integer")
    return problems


def _arg_mismatches(want: dict[str, Any], got: dict[str, Any] | None) -> list[str]:
    if got is None:
        return ["arguments are not valid JSON"]
    out = []
    for key, expected in want.items():
        actual = got.get(key)
        if isinstance(expected, str) and isinstance(actual, str):
            if actual.strip().lower() != expected.strip().lower():
                out.append(f"{key}={actual!r} (wanted {expected!r})")
        elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            if abs(float(actual) - float(expected)) > 1e-9:
                out.append(f"{key}={actual!r} (wanted {expected!r})")
        elif actual != expected:
            out.append(f"{key}={actual!r} (wanted {expected!r})")
    return out


def _score(case: dict[str, Any], calls: list[dict[str, Any]], final: str | None) -> tuple[bool, str]:
    expected = case.get("expect_calls", [])
    allowed_extra = set(case.get("allow_extra_calls", []))
    expected_names = [e["tool"] for e in expected]

    if not expected and calls:
        return False, f"called {[c['tool'] for c in calls]} when it should have answered directly"

    # Every call must be either the next expected one or an allowed extra.
    cursor = 0
    for call in calls:
        if cursor < len(expected) and call["tool"] == expected_names[cursor]:
            bad = _arg_mismatches(expected[cursor].get("args", {}), call["args"])
            if bad:
                return False, f"{call['tool']}: " + "; ".join(bad)
            cursor += 1
        elif call["tool"] in allowed_extra:
            continue
        else:
            return False, f"expected {expected_names}, got {[c['tool'] for c in calls]}"
    if cursor < len(expected):
        return False, f"expected {expected_names}, got {[c['tool'] for c in calls]}"

    max_repeat = case.get("max_repeat_calls")
    if max_repeat is not None:
        seen: dict[str, int] = {}
        for call in calls:
            key = json.dumps([call["tool"], call["args"]], sort_keys=True)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > max_repeat:
                return False, f"retried the same {call['tool']} call {seen[key]} times"

    needles = case.get("expect_final_contains", [])
    if needles:
        if not (final or "").strip():
            return False, "no final answer after the tool calls"
        text = final.lower()
        missing = [n for n in needles if n.lower() not in text]
        if missing:
            return False, f"final answer lacks {missing}"
    alternatives = case.get("expect_final_contains_any", [])
    if alternatives and not any(value.lower() in (final or "").lower() for value in alternatives):
        return False, f"final answer lacks any of {alternatives}"
    return True, ""


def run_case(
    client: AuditedClient, scenario: dict[str, Any], case: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {"name": case["name"], "expect_calls": case.get("expect_calls", [])}
    messages = [
        {"role": "system", "content": scenario["system"]},
        {"role": "user", "content": case["prompt"]},
    ]
    tool_results = case.get("tool_results", {})
    max_turns = case.get("max_turns", 1)
    expects_tool = bool(case.get("expect_calls"))

    calls: list[dict[str, Any]] = []
    turns = []
    final: str | None = None
    for turn_no in range(max_turns):
        try:
            turn = client.complete(
                messages,
                tools=scenario["tools"],
                expect_tool_call=(turn_no == 0 and expects_tool),
                conversation_turn=turn_no + 1,
                **params,
            )
        except AuditError as exc:
            return result | {"passed": False, "reason": str(exc), "turns": len(turns)}
        turns.append(turn)
        fatal_findings = [str(finding) for finding in turn.findings if finding.fatal]
        if fatal_findings:
            return result | {
                "passed": False,
                "reason": "; ".join(fatal_findings),
                "turns": len(turns),
                "findings": [str(finding) for item in turns for finding in item.findings],
            }
        messages.append(turn.message)
        if not turn.tool_calls:
            final = turn.content
            break
        for call in turn.tool_calls:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = None
            except json.JSONDecodeError:
                args = None
            calls.append({"tool": fn.get("name"), "args": args})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_{len(calls)}",
                    "name": fn.get("name"),
                    "content": tool_results.get(fn.get("name"), f"error: unknown tool {fn.get('name')}"),
                }
            )
        if not tool_results:
            break

    passed, reason = _score(case, calls, final)
    speeds = [t.predicted_per_second for t in turns if t.predicted_per_second]
    return result | {
        "passed": passed,
        "reason": reason,
        "calls": calls,
        "final": (final or "")[:300],
        "turns": len(turns),
        "finish_reason": turns[-1].finish_reason if turns else None,
        "chat_format": next((t.chat_format for t in turns if t.chat_format), None),
        "reasoning_chars": sum(t.reasoning_chars for t in turns),
        "completion_tokens": sum(t.completion_tokens for t in turns),
        "tok_per_s": round(statistics.mean(speeds), 1) if speeds else 0.0,
        "findings": [str(f) for t in turns for f in t.findings],
    }


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", text).strip("-")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _source_digest() -> str:
    root = Path(__file__).resolve().parent.parent
    files = [*sorted((root / "harness").glob("*.py")), *sorted((root / "evals").rglob("*.json"))]
    files.extend(path for path in (root / "pyproject.toml", root / "uv.lock") if path.is_file())
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"model artifact is not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_eval(
    model: str,
    preset: str,
    scenario_names: list[str] | None = None,
    base_url: str = DEFAULT_BASE_URL,
    label: str = "",
    save: bool = True,
    repeat: int = 1,
    model_sha256: str | None = None,
    model_file: Path | None = None,
) -> dict[str, Any]:
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    if model_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", model_sha256):
        raise ValueError("model_sha256 must be 64 lowercase hexadecimal characters")
    if model_file is not None and model_sha256 is not None:
        raise ValueError("provide model_file or model_sha256, not both")

    initial_model_info = read_model_info(base_url, model)
    if not initial_model_info:
        raise AuditError(
            f"model {model!r} is not listed by {base_url}/models; "
            "run `llm-serve doctor`, `llm-serve models`, and `llm-serve start` first"
        )
    initial_status = initial_model_info.get("status")
    initial_status = initial_status.get("value") if isinstance(initial_status, dict) else initial_status
    if initial_status in {"error", "failed"}:
        raise AuditError(f"model {model!r} is already in router state {initial_status!r}")

    if model_file is not None:
        model_sha256 = _file_sha256(model_file)
        model_identity_source = "hashed_file"
    else:
        model_identity_source = "operator_attested" if model_sha256 else "missing"

    # Capture identity before the first request. A concurrent edit during a long
    # evaluation must not be presented as the source that produced earlier turns.
    harness_commit = _git_commit()
    harness_source_sha256 = _source_digest()
    params = load_preset(preset)
    scenarios = load_scenarios(scenario_names)
    client = AuditedClient(model=model, base_url=base_url)
    before = read_metrics(base_url, model)
    started = time.time()

    per_scenario = []
    for scenario in scenarios:
        results = []
        for repetition in range(1, repeat + 1):
            for case in scenario["cases"]:
                result = run_case(client, scenario, case, params)
                result["repetition"] = repetition
                results.append(result)
        per_scenario.append({"name": scenario["name"], "results": results})

    after = read_metrics(base_url, model)
    model_info = read_model_info(base_url, model)
    server_model_path = model_info.get("path")
    server_status = model_info.get("status")
    server_status = server_status.get("value") if isinstance(server_status, dict) else None
    model_path_matches = bool(
        model_file is not None
        and isinstance(server_model_path, str)
        and Path(server_model_path).is_absolute()
        and Path(server_model_path).resolve() == model_file.resolve()
    )
    all_results = [r for s in per_scenario for r in s["results"]]
    passed = sum(r["passed"] for r in all_results)
    summary: dict[str, Any] = {
        "ts": started,
        "model": model,
        "preset": preset,
        "params": params,
        "label": label,
        "trace_id": client.trace_id,
        "repeat": repeat,
        "manifest": {
            "harness_commit": harness_commit,
            "harness_source_sha256": harness_source_sha256,
            "model_sha256": model_sha256,
            "model_identity_source": model_identity_source,
            "server_model_path": server_model_path,
            "server_model_status": server_status,
            "server_model_path_matches": model_path_matches,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "preset_sha256": _digest(params),
            "scenarios_sha256": _digest(scenarios),
            "base_url": base_url,
        },
        "passed": passed,
        "total": len(all_results),
        "score": round(passed / len(all_results), 3) if all_results else 0.0,
        "chat_format": next((r.get("chat_format") for r in all_results if r.get("chat_format")), None),
        "duration_s": round(time.time() - started, 1),
        "scenarios": per_scenario,
    }
    if before and after:
        key = "llamacpp:tokens_predicted_total"
        summary["tokens_predicted"] = after.get(key, 0) - before.get(key, 0)

    if save:
        stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(started))
        path = RUNS_DIR / (
            f"{stamp}_{client.trace_id}_{_slug(model)}_{preset}{'_' + _slug(label) if label else ''}.json"
        )
        write_private_json(path, summary)
        summary["path"] = str(path)
    return summary


def load_run(ref: str) -> dict[str, Any]:
    """Load a run by path, by file name inside the runs dir, or by unique prefix."""
    path = Path(ref)
    if not path.exists():
        path = RUNS_DIR / ref
    if not path.exists():
        matches = sorted(RUNS_DIR.glob(f"{ref}*.json"))
        if len(matches) != 1:
            raise FileNotFoundError(f"{ref!r} matches {len(matches)} runs in {RUNS_DIR}")
        path = matches[0]
    return json.loads(path.read_text())


def list_runs() -> list[dict[str, Any]]:
    out = []
    for path in sorted(RUNS_DIR.glob("*.json")):
        run = json.loads(path.read_text())
        run["path"] = str(path)
        out.append(run)
    return out


def format_run(summary: dict[str, Any]) -> str:
    lines = []
    for scenario in summary["scenarios"]:
        lines.append(f"  {scenario['name']}")
        width = max(len(r["name"]) for r in scenario["results"])
        for r in scenario["results"]:
            mark = " ok " if r["passed"] else "FAIL"
            repetition = f" r{r['repetition']}" if summary.get("repeat", 1) > 1 else ""
            line = f"    [{mark}] {r['name']:<{width}}{repetition}  {r.get('tok_per_s', 0):>6} tok/s"
            if r.get("reasoning_chars"):
                line += f"  think {r['reasoning_chars']}ch"
            if not r["passed"]:
                line += f"\n           {r['reason']}"
            lines.append(line)
            for finding in r.get("findings", []):
                lines.append(f"           {finding}")
    lines.append("")
    lines.append(
        f"  {summary['passed']}/{summary['total']} passed   score {summary['score']}   "
        f"chat_format {summary['chat_format']}   {summary['duration_s']}s"
    )
    lines.append(f"  model {summary['model']}   preset {summary['preset']}   trace {summary['trace_id']}")
    if "tokens_predicted" not in summary:
        lines.append("  /metrics unavailable: start the server with --metrics for throughput totals")
    if summary.get("path"):
        lines.append(f"  saved {summary['path']}")
    return "\n".join(lines)


def format_compare(a: dict[str, Any], b: dict[str, Any]) -> str:
    def flat(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            f"{s['name']}/{r['name']}#r{r.get('repetition', 1)}": r
            for s in run["scenarios"]
            for r in s["results"]
        }

    fa, fb = flat(a), flat(b)
    names = sorted(set(fa) | set(fb))
    width = max(len(n) for n in names)
    lines = [
        f"  A: {a['model']} / {a['preset']} {a.get('label', '')}",
        f"  B: {b['model']} / {b['preset']} {b.get('label', '')}",
        "",
    ]
    lines.append(f"  {'case':<{width}}   A      B      A tok/s  B tok/s")
    for name in names:
        ra, rb = fa.get(name), fb.get(name)
        pa = "ok  " if ra and ra["passed"] else ("FAIL" if ra else " -  ")
        pb = "ok  " if rb and rb["passed"] else ("FAIL" if rb else " -  ")
        change = ""
        if ra and rb and ra["passed"] != rb["passed"]:
            change = "  <- improved" if rb["passed"] else "  <- regressed"
        lines.append(
            f"  {name:<{width}}   {pa}   {pb}   {ra.get('tok_per_s', 0) if ra else '-':>6}   "
            f"{rb.get('tok_per_s', 0) if rb else '-':>6}{change}"
        )
    lines.append("")
    lines.append(f"  score A {a['score']}   B {b['score']}")
    return "\n".join(lines)
