from harness.controller import document_sha256, evaluate_contract, validate_work_order


def documents():
    work = {
        "schema": "local-harness/work-order/v1",
        "run_id": "run-1",
        "objective": "make one safe change",
        "base_commit": "a" * 40,
        "scope": {
            "read": ["**"],
            "write": ["harness/**"],
            "deny": ["harness/secrets/**"],
        },
        "required_checks": ["ruff", "pytest"],
        "limits": {
            "min_changed_files": 1,
            "max_changed_files": 2,
            "max_tool_calls": 20,
            "max_wall_seconds": 600,
        },
        "approval": "human",
        "next_step": "present for human review",
    }
    work_hash = document_sha256(work)
    completion = {
        "schema": "local-harness/completion/v1",
        "run_id": "run-1",
        "work_order_sha256": work_hash,
        "agent_id": "agent-1",
        "status": "completed",
        "summary": "changed the controller",
        "changes": [{"path": "harness/controller.py", "kind": "modified"}],
        "checks": [
            {"name": "ruff", "status": "pass"},
            {"name": "pytest", "status": "pass"},
        ],
        "claims": [{"statement": "tests pass", "evidence_refs": ["check:pytest"]}],
        "risks": [],
        "uncertainties": [],
        "requested_action": "advance",
    }
    observation = {
        "schema": "local-harness/observation/v1",
        "run_id": "run-1",
        "work_order_sha256": work_hash,
        "runner_id": "runner-1",
        "base_commit": "a" * 40,
        "changes": [{"path": "harness/controller.py", "kind": "modified"}],
        "checks": [
            {
                "name": "ruff",
                "status": "pass",
                "exit_code": 0,
                "output_sha256": "a" * 64,
                "output_ref": "checks/ruff.log",
            },
            {
                "name": "pytest",
                "status": "pass",
                "exit_code": 0,
                "output_sha256": "b" * 64,
                "output_ref": "checks/pytest.log",
            },
        ],
        "checks_isolated": True,
        "sandbox": {
            "kind": "docker",
            "manifest_sha256": "c" * 64,
            "network": "internal",
            "read_scope_enforced": True,
        },
        "sandbox_violations": [],
        "telemetry_ref": "telemetry:run-1",
        "telemetry_complete": True,
        "tool_calls": 8,
        "wall_seconds": 12.5,
        "evidence": ["check:ruff", "check:pytest", "path:harness/controller.py"],
    }
    return work, completion, observation


def test_advances_only_when_documents_agree():
    assert evaluate_contract(*documents()).outcome == "advance"


def test_missing_evidence_requests_double_check():
    work, completion, observation = documents()
    observation["evidence"].remove("check:pytest")
    decision = evaluate_contract(work, completion, observation)
    assert decision.outcome == "double_check"
    assert "claims lack observed evidence" in decision.reasons[-1]


def test_unreported_change_requests_double_check():
    work, completion, observation = documents()
    observation["changes"].append({"path": "harness/new.py", "kind": "added"})
    assert evaluate_contract(work, completion, observation).outcome == "double_check"


def test_out_of_scope_change_halts():
    work, completion, observation = documents()
    observation["changes"] = [{"path": "PLAN.md", "kind": "modified"}]
    assert evaluate_contract(work, completion, observation).outcome == "halt"


def test_denied_change_halts():
    work, completion, observation = documents()
    observation["changes"] = [{"path": "harness/secrets/key", "kind": "modified"}]
    assert evaluate_contract(work, completion, observation).outcome == "halt"


def test_failed_required_check_halts():
    work, completion, observation = documents()
    observation["checks"][1] = {
        "name": "pytest",
        "status": "fail",
        "exit_code": 1,
        "output_sha256": "c" * 64,
        "output_ref": "checks/pytest.log",
    }
    assert evaluate_contract(work, completion, observation).outcome == "halt"


def test_uncertainty_requests_double_check():
    work, completion, observation = documents()
    completion["uncertainties"] = ["could not reproduce an intermittent failure"]
    assert evaluate_contract(work, completion, observation).outcome == "double_check"


def test_identity_mismatch_halts():
    work, completion, observation = documents()
    observation["run_id"] = "another-run"
    assert evaluate_contract(work, completion, observation).outcome == "halt"


def test_work_order_digest_mismatch_halts():
    work, completion, observation = documents()
    work["objective"] = "tampered"
    assert evaluate_contract(work, completion, observation).outcome == "halt"


def test_same_agent_cannot_verify_itself():
    work, completion, observation = documents()
    observation["runner_id"] = completion["agent_id"]
    assert evaluate_contract(work, completion, observation).outcome == "halt"


def test_incomplete_telemetry_requests_double_check():
    work, completion, observation = documents()
    observation["telemetry_complete"] = False
    assert evaluate_contract(work, completion, observation).outcome == "double_check"


def test_unsandboxed_agent_requests_double_check():
    work, completion, observation = documents()
    observation["sandbox"] = {
        "kind": "host",
        "manifest_sha256": None,
        "network": "host",
        "read_scope_enforced": False,
    }
    assert evaluate_contract(work, completion, observation).outcome == "double_check"


def test_check_status_must_match_exit_code():
    work, completion, observation = documents()
    observation["checks"][0]["exit_code"] = 1
    assert evaluate_contract(work, completion, observation).outcome == "double_check"


def test_malformed_observation_requests_double_check():
    work, completion, observation = documents()
    observation["changes"] = [42]
    assert evaluate_contract(work, completion, observation).outcome == "double_check"


def test_tool_call_limit_halts():
    work, completion, observation = documents()
    observation["tool_calls"] = 21
    assert evaluate_contract(work, completion, observation).outcome == "halt"


def test_wall_time_limit_halts():
    work, completion, observation = documents()
    observation["wall_seconds"] = 601
    assert evaluate_contract(work, completion, observation).outcome == "halt"


def test_boolean_runner_counters_are_rejected():
    work, completion, observation = documents()
    observation["tool_calls"] = True
    assert evaluate_contract(work, completion, observation).outcome == "double_check"
    observation["tool_calls"] = 1
    observation["wall_seconds"] = False
    assert evaluate_contract(work, completion, observation).outcome == "double_check"


def test_duplicate_runner_evidence_is_rejected():
    work, completion, observation = documents()
    observation["evidence"].append("check:pytest")
    assert evaluate_contract(work, completion, observation).outcome == "double_check"


def test_root_denied_file_halts_for_recursive_pattern():
    work, completion, observation = documents()
    work["scope"]["write"] = ["**"]
    work["scope"]["deny"] = ["**/.env"]
    completion["work_order_sha256"] = document_sha256(work)
    observation["work_order_sha256"] = document_sha256(work)
    completion["changes"] = [{"path": ".env", "kind": "added"}]
    observation["changes"] = [{"path": ".env", "kind": "added"}]
    observation["evidence"] = ["check:ruff", "check:pytest", "path:.env"]
    assert evaluate_contract(work, completion, observation).outcome == "halt"


def test_unsafe_run_id_and_abbreviated_commit_are_rejected():
    work, completion, observation = documents()
    work["run_id"] = "../../escape"
    work["base_commit"] = "abc123"
    assert evaluate_contract(work, completion, observation).outcome == "double_check"


def test_wrong_claimed_change_kind_requests_double_check():
    work, completion, observation = documents()
    completion["changes"][0]["kind"] = "deleted"
    decision = evaluate_contract(work, completion, observation)
    assert decision.outcome == "double_check"
    assert "claimed changes" in decision.reasons[0]


def test_no_progress_does_not_advance():
    work, completion, observation = documents()
    completion["changes"] = []
    observation["changes"] = []
    observation["evidence"] = ["check:ruff", "check:pytest"]
    decision = evaluate_contract(work, completion, observation)
    assert decision.outcome == "double_check"
    assert "minimum changed-file count" in decision.reasons[0]


def test_scope_patterns_cannot_escape_worktree():
    work, completion, observation = documents()
    work["scope"]["deny"] = ["../secrets/**"]
    assert evaluate_contract(work, completion, observation).outcome == "double_check"


def test_work_order_v1_does_not_delegate_approval_to_controller():
    work, _completion, _observation = documents()
    work["approval"] = "controller"
    assert validate_work_order(work) == ["work order: v1 requires human approval"]
