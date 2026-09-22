from harness.judge import score_eval


def manifest(commit: str = "a"):
    return {
        "harness_commit": commit * 40,
        "harness_source_sha256": "b" * 64,
        "model_sha256": "e" * 64,
        "model_identity_source": "hashed_file",
        "server_model_path": "/models/test.gguf",
        "server_model_status": "loaded",
        "server_model_path_matches": True,
        "python": "3.13.15",
        "platform": "test",
        "preset_sha256": "c" * 64,
        "scenarios_sha256": "d" * 64,
        "base_url": "http://127.0.0.1:8080",
    }


def test_judge_requires_manifest_and_all_cases():
    run = {
        "duration_s": 1,
        "scenarios": [
            {"name": "tools", "results": [{"name": "call", "passed": True, "findings": [], "turns": 1}]}
        ],
    }
    assert score_eval(run)["verdict"] == "double_check"
    run["manifest"] = manifest()
    assert score_eval(run)["verdict"] == "pass"


def test_judge_does_not_average_away_fatal_findings():
    run = {
        "manifest": manifest(),
        "scenarios": [
            {
                "name": "tools",
                "results": [
                    {
                        "name": "call",
                        "passed": True,
                        "findings": ["[FAIL] empty_turn: bad"],
                        "turns": 1,
                    }
                ],
            }
        ],
    }
    score = score_eval(run)
    assert score["verdict"] == "double_check"
    assert score["scores"]["fatal_findings"] == 1


def test_judge_can_require_repeated_runs():
    run = {
        "manifest": manifest(),
        "repeat": 2,
        "scenarios": [
            {
                "name": "tools",
                "results": [
                    {"name": "call", "repetition": 1, "passed": True, "findings": [], "turns": 1},
                    {"name": "call", "repetition": 2, "passed": True, "findings": [], "turns": 1},
                ],
            }
        ],
    }
    assert score_eval(run, minimum_repetitions=3)["verdict"] == "double_check"


def test_judge_rejects_declared_repetitions_without_results():
    run = {
        "manifest": manifest(),
        "repeat": 3,
        "scenarios": [
            {
                "name": "tools",
                "results": [{"name": "call", "repetition": 1, "passed": True, "findings": [], "turns": 1}],
            }
        ],
    }
    score = score_eval(run, minimum_repetitions=3)
    assert score["verdict"] == "double_check"
    assert score["hard_gates"]["repetition_integrity"] is False


def test_judge_rejects_scenario_group_regression():
    baseline = {
        "manifest": manifest(),
        "scenarios": [{"name": "tools", "results": [{"name": "call", "passed": True, "findings": []}]}],
    }
    candidate = {
        "manifest": manifest("e"),
        "scenarios": [{"name": "tools", "results": [{"name": "call", "passed": False, "findings": []}]}],
    }
    score = score_eval(candidate, baseline=baseline)
    assert score["verdict"] == "double_check"
    assert score["hard_gates"]["no_scenario_group_regression"] is False


def test_judge_requires_complete_manifest_and_positive_repetition_policy():
    run = {
        "manifest": {"harness_commit": "a" * 40},
        "scenarios": [{"name": "tools", "results": [{"name": "call", "passed": True, "findings": []}]}],
    }
    assert score_eval(run)["hard_gates"]["has_reproducibility_manifest"] is False
    try:
        score_eval(run, minimum_repetitions=0)
    except ValueError as exc:
        assert "at least 1" in str(exc)
    else:
        raise AssertionError("invalid judge repetition policy was accepted")


def test_judge_rejects_operator_attested_model_digest():
    claimed = manifest()
    claimed["model_identity_source"] = "operator_attested"
    run = {
        "manifest": claimed,
        "scenarios": [{"name": "tools", "results": [{"name": "call", "passed": True}]}],
    }
    gates = score_eval(run)["hard_gates"]
    assert gates["has_reproducibility_manifest"] is True
    assert gates["model_artifact_hashed_by_evaluator"] is False


def test_judge_rejects_model_file_not_bound_to_loaded_server_record():
    unbound = manifest()
    unbound["server_model_path_matches"] = False
    run = {
        "manifest": unbound,
        "scenarios": [{"name": "tools", "results": [{"name": "call", "passed": True}]}],
    }
    assert score_eval(run)["hard_gates"]["model_artifact_hashed_by_evaluator"] is False


def test_judge_rejects_untrusted_baseline():
    candidate = {
        "manifest": manifest(),
        "scenarios": [{"name": "tools", "results": [{"name": "call", "passed": True}]}],
    }
    baseline = {
        "manifest": manifest(),
        "scenarios": [{"name": "tools", "results": [{"name": "call", "passed": False}]}],
    }
    baseline["manifest"]["model_identity_source"] = "operator_attested"
    gates = score_eval(candidate, baseline=baseline)["hard_gates"]
    assert gates["valid_baseline_shape"] is True
    assert gates["baseline_model_artifact_hashed_by_evaluator"] is False


def test_judge_fails_closed_on_malformed_results():
    score = score_eval({"manifest": manifest(), "scenarios": [{"name": "tools", "results": [42]}]})
    assert score["verdict"] == "double_check"
    assert score["hard_gates"]["valid_run_shape"] is False
