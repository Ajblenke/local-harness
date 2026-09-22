from harness.client import AuditError
from harness.evals import _file_sha256, _source_digest, run_eval, validate_scenario


def test_source_digest_is_stable_sha256():
    first = _source_digest()
    assert len(first) == 64
    assert first == _source_digest()


def test_eval_rejects_invalid_model_identity_before_network():
    try:
        run_eval("model", "greedy", save=False, model_sha256="not-a-digest")
    except ValueError as exc:
        assert "model_sha256" in str(exc)
    else:
        raise AssertionError("invalid model identity was accepted")


def test_model_file_is_hashed_from_bytes(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"model bytes")
    assert _file_sha256(model) == "9cb7487000bc86ac36ce83c4acfabe8878552be99572a6770f65ab1d048a5c48"


def test_eval_rejects_two_model_identity_sources_before_network(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"model")
    try:
        run_eval("model", "greedy", save=False, model_file=model, model_sha256="a" * 64)
    except ValueError as exc:
        assert "not both" in str(exc)
    else:
        raise AssertionError("ambiguous model identity was accepted")


def test_eval_fails_fast_when_model_is_not_listed(monkeypatch):
    monkeypatch.setattr("harness.evals.read_model_info", lambda *_args: {})

    try:
        run_eval("missing", "greedy", save=False)
    except AuditError as exc:
        assert "llm-serve doctor" in str(exc)
    else:
        raise AssertionError("eval attempted to run an unavailable model")


def test_eval_binds_hashed_file_to_loaded_router_path(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"model")

    class Client:
        trace_id = "trace"

        def __init__(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr("harness.evals.AuditedClient", Client)
    monkeypatch.setattr("harness.evals.load_preset", lambda _name: {})
    monkeypatch.setattr("harness.evals.load_scenarios", lambda _names: [])
    monkeypatch.setattr("harness.evals.read_metrics", lambda *_args: {})
    monkeypatch.setattr(
        "harness.evals.read_model_info",
        lambda *_args: {"path": str(model.resolve()), "status": {"value": "loaded"}},
    )
    summary = run_eval("model", "greedy", save=False, model_file=model)
    manifest = summary["manifest"]
    assert manifest["model_identity_source"] == "hashed_file"
    assert manifest["server_model_path_matches"] is True
    assert manifest["server_model_status"] == "loaded"


def test_scenario_validator_rejects_ignored_expectation_typo():
    scenario = {
        "name": "typo",
        "description": "test",
        "system": "test",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "read",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            }
        ],
        "cases": [
            {
                "name": "case",
                "prompt": "read",
                "expect_calls": [{"tool": "read", "args": {"path": "file"}}],
                "expect_final_contians": ["done"],
            }
        ],
    }
    assert any("unexpected fields" in problem for problem in validate_scenario(scenario))


def test_scenario_validator_checks_expected_argument_types():
    scenario = {
        "name": "types",
        "description": "test",
        "system": "test",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "count",
                    "description": "count",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                    },
                },
            }
        ],
        "cases": [
            {
                "name": "case",
                "prompt": "count",
                "expect_calls": [{"tool": "count", "args": {"value": True}}],
            }
        ],
    }
    assert any("expected integer" in problem for problem in validate_scenario(scenario))
