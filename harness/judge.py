"""Transparent score vector for eval runs and continuation decisions."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
GIT_OID_PATTERN = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")


def _valid_manifest(manifest: Any) -> bool:
    if not isinstance(manifest, dict):
        return False
    required_text = {"python", "platform", "base_url"}
    required_digests = {
        "harness_source_sha256",
        "model_sha256",
        "preset_sha256",
        "scenarios_sha256",
    }
    return (
        isinstance(manifest.get("harness_commit"), str)
        and bool(GIT_OID_PATTERN.fullmatch(manifest["harness_commit"]))
        and all(isinstance(manifest.get(key), str) and manifest[key] for key in required_text)
        and all(
            isinstance(manifest.get(key), str) and bool(SHA256_PATTERN.fullmatch(manifest[key]))
            for key in required_digests
        )
    )


def _trusted_model_identity(manifest: Any) -> bool:
    return (
        isinstance(manifest, dict)
        and manifest.get("model_identity_source") == "hashed_file"
        and isinstance(manifest.get("server_model_path"), str)
        and manifest["server_model_path"].startswith("/")
        and manifest.get("server_model_path_matches") is True
        and manifest.get("server_model_status") == "loaded"
    )


def _scenario_scores(run: dict[str, Any]) -> dict[str, float]:
    scores = {}
    for scenario in run.get("scenarios", []):
        results = scenario.get("results", [])
        if isinstance(scenario.get("name"), str) and isinstance(results, list) and results:
            scores[scenario["name"]] = round(
                sum(bool(result.get("passed")) for result in results) / len(results), 3
            )
    return scores


def _valid_run_shape(run: Any) -> bool:
    if not isinstance(run, dict) or not isinstance(run.get("scenarios"), list):
        return False
    for scenario in run["scenarios"]:
        if (
            not isinstance(scenario, dict)
            or not isinstance(scenario.get("name"), str)
            or not scenario["name"]
            or not isinstance(scenario.get("results"), list)
        ):
            return False
        for result in scenario["results"]:
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("name"), str)
                or not result["name"]
                or not isinstance(result.get("passed"), bool)
                or not isinstance(result.get("findings", []), list)
                or not all(isinstance(value, str) for value in result.get("findings", []))
                or any(
                    key in result
                    and (not isinstance(result[key], int) or isinstance(result[key], bool) or result[key] < 0)
                    for key in ("turns", "completion_tokens")
                )
            ):
                return False
    return True


def _repetition_integrity(run: dict[str, Any]) -> tuple[bool, int]:
    declared = run.get("repeat", 1)
    if not isinstance(declared, int) or isinstance(declared, bool) or declared < 1:
        return False, 0
    counts: Counter[tuple[str, str]] = Counter()
    repetitions: dict[tuple[str, str], set[int]] = {}
    for scenario in run.get("scenarios", []):
        if not isinstance(scenario, dict) or not isinstance(scenario.get("results"), list):
            return False, 0
        scenario_name = scenario.get("name")
        for result in scenario.get("results", []):
            if not isinstance(result, dict):
                return False, 0
            key = (scenario_name, result.get("name"))
            repetition = result.get("repetition", 1)
            if not all(isinstance(value, str) and value for value in key):
                return False, 0
            if not isinstance(repetition, int) or isinstance(repetition, bool):
                return False, 0
            counts[key] += 1
            repetitions.setdefault(key, set()).add(repetition)
    expected = set(range(1, declared + 1))
    valid = bool(counts) and all(count == declared for count in counts.values())
    valid = valid and all(values == expected for values in repetitions.values())
    return valid, declared if valid else 0


def score_eval(
    run: dict[str, Any], minimum_repetitions: int = 1, baseline: dict[str, Any] | None = None
) -> dict[str, Any]:
    if minimum_repetitions < 1:
        raise ValueError("minimum_repetitions must be at least 1")
    valid_run_shape = _valid_run_shape(run)
    results = (
        [item for scenario in run["scenarios"] for item in scenario["results"]] if valid_run_shape else []
    )
    fatal = sum(finding.startswith("[FAIL]") for result in results for finding in result.get("findings", []))
    total = len(results)
    passed = sum(bool(result.get("passed")) for result in results)
    tokens = sum(int(result.get("completion_tokens", 0)) for result in results)
    turns = sum(int(result.get("turns", 0)) for result in results)
    repetition_integrity, observed_repetitions = _repetition_integrity(run)
    scenario_scores = _scenario_scores(run) if valid_run_shape else {}
    baseline_shape_valid = baseline is not None and _valid_run_shape(baseline)
    baseline_repetitions_valid = baseline is not None and _repetition_integrity(baseline)[0]
    baseline_manifest_valid = baseline is not None and _valid_manifest(baseline.get("manifest"))
    baseline_identity_trusted = baseline is not None and _trusted_model_identity(baseline.get("manifest"))
    baseline_scores = _scenario_scores(baseline) if baseline_shape_valid else None
    same_scenarios = baseline is None or (
        baseline_scores is not None and set(scenario_scores) == set(baseline_scores)
    )
    no_group_regression = baseline is None or (
        baseline_scores is not None
        and same_scenarios
        and all(scenario_scores[name] >= baseline_scores[name] for name in scenario_scores)
    )
    hard_gates = {
        "has_cases": total > 0,
        "valid_run_shape": valid_run_shape,
        "valid_baseline_shape": baseline is None or baseline_shape_valid,
        "valid_baseline_manifest": baseline is None or baseline_manifest_valid,
        "baseline_model_artifact_hashed_by_evaluator": baseline is None or baseline_identity_trusted,
        "baseline_repetition_integrity": baseline is None or baseline_repetitions_valid,
        "all_cases_pass": total > 0 and passed == total,
        "no_fatal_audit_findings": fatal == 0,
        "has_reproducibility_manifest": _valid_manifest(run.get("manifest")),
        "model_artifact_hashed_by_evaluator": _trusted_model_identity(run.get("manifest")),
        "repetition_integrity": repetition_integrity,
        "enough_repetitions": observed_repetitions >= minimum_repetitions,
        "same_scenario_groups_as_baseline": same_scenarios,
        "no_scenario_group_regression": no_group_regression,
    }
    return {
        "verdict": "pass" if all(hard_gates.values()) else "double_check",
        "hard_gates": hard_gates,
        "scores": {
            "correctness": round(passed / total, 3) if total else 0.0,
            "fatal_findings": fatal,
            "completion_tokens": tokens,
            "turns": turns,
            "duration_seconds": run.get("duration_s"),
            "repetitions": observed_repetitions,
            "scenario_correctness": scenario_scores,
            "baseline_scenario_correctness": baseline_scores,
        },
        "policy": "hard gates plus an unweighted score vector; no opaque aggregate",
    }
