from harness.client import AuditedClient, AuditError, read_model_info, validate_tool_schema
from harness.evals import run_case
from harness.validation import validate_tool_arguments

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "mode"],
    "properties": {
        "path": {"type": "string"},
        "mode": {"type": "string", "enum": ["read", "write"]},
    },
}


def test_model_info_selects_exact_router_model(monkeypatch):
    monkeypatch.setattr(
        "harness.client._get",
        lambda _url: {
            "data": [
                {"id": "other", "path": "/models/other.gguf"},
                {"id": "wanted", "path": "/models/wanted.gguf"},
            ]
        },
    )
    assert read_model_info("http://local", "wanted")["path"] == "/models/wanted.gguf"


def test_argument_validator_enforces_required_and_enum():
    assert validate_tool_arguments({"path": "a"}, SCHEMA) == ["$.mode is required"]
    assert "not in" in validate_tool_arguments({"path": "a", "mode": "delete"}, SCHEMA)[0]


def test_argument_validator_rejects_extra_fields():
    assert validate_tool_arguments({"path": "a", "mode": "read", "secret": True}, SCHEMA) == [
        "$.secret is not allowed"
    ]


def test_argument_validator_does_not_accept_boolean_as_integer():
    assert validate_tool_arguments(True, {"type": "integer"}) == ["$ expected integer, got bool"]


def test_argument_validator_enforces_numeric_and_string_bounds():
    assert validate_tool_arguments(float("nan"), {"type": "number"}) == ["$ must be finite"]
    assert validate_tool_arguments(0, {"type": "integer", "minimum": 1}) == ["$ is below minimum 1"]
    assert validate_tool_arguments("", {"type": "string", "minLength": 1}) == [
        "$ is shorter than minLength 1"
    ]


def test_tool_schema_validator_fails_closed_without_crashing():
    malformed = {
        "type": "function",
        "function": {
            "name": "broken",
            "description": "Broken",
            "parameters": {"type": "object", "properties": [], "required": "path"},
        },
    }
    problems = validate_tool_schema(malformed)
    assert any("properties must be an object" in problem for problem in problems)
    assert any("required must be a string array" in problem for problem in problems)


def test_client_rejects_bad_schema_before_network(monkeypatch):
    called = False

    def fake_post(*_args):
        nonlocal called
        called = True
        return response()

    monkeypatch.setattr("harness.client._post", fake_post)
    client = AuditedClient("test", log_path=None)
    try:
        client.complete([{"role": "user", "content": "test"}], tools=[{"not": "a function"}])
    except AuditError as exc:
        assert "invalid tool schema" in str(exc)
    else:
        raise AssertionError("invalid schema was sent")
    assert called is False


def test_client_rejects_symbolic_link_audit_log(tmp_path):
    target = tmp_path / "target"
    target.write_text("do not append")
    log = tmp_path / "turns.jsonl"
    log.symlink_to(target)
    try:
        AuditedClient("test", log_path=log)
    except ValueError as exc:
        assert "symbolic-link audit log" in str(exc)
    else:
        raise AssertionError("symbolic-link audit log was accepted")
    assert target.read_text() == "do not append"


def response(arguments='{"path":"a"}'):
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {"name": "operate", "arguments": arguments},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    }


def tool():
    return {
        "type": "function",
        "function": {"name": "operate", "description": "Operate", "parameters": SCHEMA},
    }


def test_fatal_argument_finding_fails_eval_case(monkeypatch):
    monkeypatch.setattr("harness.client._post", lambda *_args: response())
    monkeypatch.setattr("harness.client._get", lambda *_args: None)
    client = AuditedClient("test", log_path=None)
    scenario = {"system": "test", "tools": [tool()]}
    case = {
        "name": "required",
        "prompt": "operate",
        "expect_calls": [{"tool": "operate", "args": {"path": "a"}}],
    }
    result = run_case(client, scenario, case, {})
    assert result["passed"] is False
    assert "mode is required" in result["reason"]


def test_cache_warning_uses_conversation_turn_not_global_case_index(monkeypatch):
    monkeypatch.setattr("harness.client._post", lambda *_args: response('{"path":"a","mode":"read"}'))
    monkeypatch.setattr("harness.client._get", lambda *_args: None)
    client = AuditedClient("test", log_path=None)
    for _ in range(2):
        turn = client.complete(
            [{"role": "user", "content": "operate"}],
            tools=[tool()],
            conversation_turn=1,
        )
    assert not any(finding.check == "prompt_cache" for finding in turn.findings)
