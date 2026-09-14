"""Audited client for a local llama.cpp server.

Wraps /v1/chat/completions so that every turn is recorded as one JSON line and
checked against the invariants that actually break in practice. No third party
dependencies, so it runs under any interpreter that can see this file.

The checks exist because each of these failures returns HTTP 200:
  - reasoning consumes the whole token budget, leaving content empty
  - the server accepts a malformed tool schema without complaint
  - the chat parser silently falls back to a generic tool format
  - a perturbed prompt prefix destroys prompt cache reuse
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.paths import STATE_DIR

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_LOG = STATE_DIR / "turns.jsonl"

# Tool call parsers that mean the server understood the model's native format.
# Anything else means it is improvising, and tool accuracy will be worse.
DEGRADED_CHAT_FORMATS = {"Content-only", "Generic"}


class AuditError(RuntimeError):
    """A turn violated an invariant that makes its result untrustworthy."""


@dataclass
class Finding:
    check: str
    detail: str
    fatal: bool = False

    def __str__(self) -> str:
        mark = "FAIL" if self.fatal else "warn"
        return f"[{mark}] {self.check}: {self.detail}"


@dataclass
class Turn:
    """One request/response pair plus everything worth asserting on."""

    trace_id: str
    turn_index: int
    request_id: str | None
    model: str | None
    system_fingerprint: str | None
    finish_reason: str | None
    content: str | None
    reasoning_chars: int
    tool_calls: list[dict[str, Any]]
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    prompt_ms: float
    predicted_ms: float
    predicted_per_second: float
    wall_ms: float
    chat_format: str | None
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.fatal for f in self.findings)

    @property
    def message(self) -> dict[str, Any]:
        """The assistant message as it should be appended to a conversation."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg

    def as_record(self) -> dict[str, Any]:
        return {
            "ts": time.time(),
            "trace_id": self.trace_id,
            "turn": self.turn_index,
            "request_id": self.request_id,
            "model": self.model,
            "build": self.system_fingerprint,
            "finish_reason": self.finish_reason,
            "chat_format": self.chat_format,
            "tool_calls": [
                {"name": c["function"]["name"], "arguments": c["function"]["arguments"]}
                for c in self.tool_calls
            ],
            "content_chars": len(self.content or ""),
            "reasoning_chars": self.reasoning_chars,
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "cached": self.cached_tokens,
            },
            "timing_ms": {
                "prompt": round(self.prompt_ms, 1),
                "predicted": round(self.predicted_ms, 1),
                "wall": round(self.wall_ms, 1),
                "tok_per_s": round(self.predicted_per_second, 1),
            },
            "findings": [{"check": f.check, "detail": f.detail, "fatal": f.fatal} for f in self.findings],
            "ok": self.ok,
        }


def _post(url: str, payload: dict[str, Any], timeout: float, api_key: str | None) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise AuditError(f"HTTP {exc.code} from {url}: {exc.read()[:400].decode(errors='replace')}") from exc
    except urllib.error.URLError as exc:
        # The classic symptom of a suspended server: the socket accepts, nothing answers.
        raise AuditError(
            f"no response from {url} ({exc.reason}). If the port is LISTEN but nothing "
            f"replies, check the process state: grep ^State /proc/$(pgrep -x llama)/status"
        ) from exc


def _get(url: str, timeout: float = 5.0) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def validate_tool_schema(tool: dict[str, Any]) -> list[str]:
    """The server accepts malformed schemas silently, so check them here.

    Verified: {"parameters": {"type": "nonsense_type"}} returns HTTP 200 with no
    warning, and merely makes the tool harder for the model to call correctly.
    """
    problems: list[str] = []
    fn = tool.get("function")
    if tool.get("type") != "function" or not isinstance(fn, dict):
        return [f"tool is not a well formed function entry: {tool!r:.120}"]
    if not fn.get("name"):
        problems.append("function has no name")
    if not fn.get("description"):
        problems.append(f"{fn.get('name', '?')}: no description, the model has to guess intent")

    valid = {"object", "array", "string", "number", "integer", "boolean", "null"}
    params = fn.get("parameters")
    if params is None:
        return problems
    if not isinstance(params, dict):
        return problems + [f"{fn.get('name')}: parameters is not an object"]

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        t = node.get("type")
        if isinstance(t, str) and t not in valid:
            problems.append(f"{fn.get('name')}{path}: invalid JSON Schema type {t!r}")
        for key, child in (node.get("properties") or {}).items():
            walk(child, f"{path}.{key}")
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{path}[]")

    walk(params, "")
    for name in params.get("required", []) or []:
        if name not in (params.get("properties") or {}):
            problems.append(f"{fn.get('name')}: required field {name!r} is not in properties")
    return problems


class AuditedClient:
    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        log_path: Path | None = DEFAULT_LOG,
        api_key: str | None = None,
        timeout: float = 180.0,
        trace_id: str | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.trace_id = trace_id or uuid.uuid4().hex[:12]
        self.turn_index = 0
        self.log_path = log_path
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)

    def _chat_format(self) -> str | None:
        """Read the parser the server actually chose, from /slots.

        In router mode /slots belongs to one model instance, so the model must
        be named in the query.
        """
        slots = _get(f"{self.base_url}/slots?model={urllib.parse.quote(self.model, safe='')}")
        if not isinstance(slots, list):
            return None
        for slot in slots:
            fmt = (slot.get("params") or {}).get("chat_format")
            if fmt:
                return fmt
        return None

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        expect_tool_call: bool = False,
        **params: Any,
    ) -> Turn:
        self.turn_index += 1

        schema_problems: list[str] = []
        for tool in tools or []:
            schema_problems.extend(validate_tool_schema(tool))

        payload: dict[str, Any] = {"model": self.model, "messages": messages, **params}
        if tools:
            payload["tools"] = tools

        started = time.perf_counter()
        raw = _post(f"{self.base_url}/v1/chat/completions", payload, self.timeout, self.api_key)
        wall_ms = (time.perf_counter() - started) * 1000

        choice = (raw.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = raw.get("usage") or {}
        timings = raw.get("timings") or {}
        offered = {t["function"]["name"] for t in (tools or []) if t.get("function", {}).get("name")}

        turn = Turn(
            trace_id=self.trace_id,
            turn_index=self.turn_index,
            request_id=raw.get("id"),
            model=raw.get("model"),
            system_fingerprint=raw.get("system_fingerprint"),
            finish_reason=choice.get("finish_reason"),
            content=message.get("content"),
            reasoning_chars=len(message.get("reasoning_content") or ""),
            tool_calls=message.get("tool_calls") or [],
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cached_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
            prompt_ms=timings.get("prompt_ms", 0.0),
            predicted_ms=timings.get("predicted_ms", 0.0),
            predicted_per_second=timings.get("predicted_per_second", 0.0),
            wall_ms=wall_ms,
            chat_format=self._chat_format(),
        )

        for problem in schema_problems:
            turn.findings.append(Finding("tool_schema", problem, fatal=True))
        self._check(turn, offered, expect_tool_call)

        if self.log_path is not None:
            with self.log_path.open("a") as fh:
                fh.write(json.dumps(turn.as_record()) + "\n")
        return turn

    def _check(self, turn: Turn, offered: set[str], expect_tool_call: bool) -> None:
        add = turn.findings.append

        # Budget starvation. Verified: a strict json_schema request returned
        # content="" with finish_reason="length" after spending 1562 chars of
        # reasoning. The grammar does not constrain the reasoning block.
        if turn.finish_reason == "length":
            detail = "generation hit max_tokens"
            if turn.reasoning_chars and not (turn.content or "").strip():
                detail = (
                    f"reasoning consumed the budget ({turn.reasoning_chars} chars) and content is "
                    f"empty. Cap it at the server with --reasoning-budget N, or raise max_tokens"
                )
            add(Finding("finish_reason", detail, fatal=True))

        if not (turn.content or "").strip() and not turn.tool_calls:
            add(Finding("empty_turn", "no content and no tool calls", fatal=True))

        # Tool calls the server will not validate for you.
        for call in turn.tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name")
            if offered and name not in offered:
                add(Finding("tool_name", f"model called {name!r}, which was not offered", fatal=True))
            try:
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    add(Finding("tool_arguments", f"{name}: arguments are not an object", fatal=True))
            except json.JSONDecodeError as exc:
                add(Finding("tool_arguments", f"{name}: arguments are not valid JSON ({exc})", fatal=True))

        if expect_tool_call and not turn.tool_calls:
            add(Finding("expected_tool_call", "turn was expected to call a tool and did not", fatal=True))

        # Silent parser fallback, which degrades tool accuracy without erroring.
        if turn.chat_format in DEGRADED_CHAT_FORMATS and offered:
            add(
                Finding(
                    "chat_format",
                    f"server parsed with {turn.chat_format!r} while tools were offered; the model's "
                    f"native format was not recognised. Check --jinja and --chat-template-file",
                    fatal=False,
                )
            )

        # Prompt cache. The largest performance lever in an agent loop.
        if turn.turn_index > 1 and turn.cached_tokens == 0 and turn.prompt_tokens > 64:
            add(
                Finding(
                    "prompt_cache",
                    f"no cache reuse on turn {turn.turn_index} ({turn.prompt_tokens} prompt tokens). "
                    f"Something is perturbing the prefix, often a timestamp or per turn id near the top",
                    fatal=False,
                )
            )


def read_metrics(base_url: str = DEFAULT_BASE_URL, model: str | None = None) -> dict[str, float]:
    """Scrape /metrics into a dict. Returns {} if --metrics was not passed."""
    url = f"{base_url}/metrics"
    if model:
        url += f"?model={urllib.parse.quote(model, safe='')}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            text = resp.read().decode()
    except Exception:
        return {}
    out: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("#") or " " not in line:
            continue
        name, _, value = line.rpartition(" ")
        try:
            out[name.strip()] = float(value)
        except ValueError:
            continue
    return out
