"""Small, fail-closed validators used without third-party dependencies."""

from __future__ import annotations

import math
import re
from typing import Any


def validate_tool_arguments(arguments: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the JSON Schema subset used by harness tool definitions."""
    problems: list[str] = []
    expected = schema.get("type")
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    numeric_bool = expected in {"number", "integer"} and isinstance(arguments, bool)
    if expected in types and (numeric_bool or not isinstance(arguments, types[expected])):
        return [f"{path} expected {expected}, got {type(arguments).__name__}"]
    if "enum" in schema and arguments not in schema["enum"]:
        problems.append(f"{path} value {arguments!r} is not in {schema['enum']!r}")
    if expected in {"number", "integer"} and isinstance(arguments, (int, float)):
        if not math.isfinite(arguments):
            problems.append(f"{path} must be finite")
        if "minimum" in schema and arguments < schema["minimum"]:
            problems.append(f"{path} is below minimum {schema['minimum']}")
        if "maximum" in schema and arguments > schema["maximum"]:
            problems.append(f"{path} is above maximum {schema['maximum']}")
    if expected == "string" and isinstance(arguments, str):
        if "minLength" in schema and len(arguments) < schema["minLength"]:
            problems.append(f"{path} is shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(arguments) > schema["maxLength"]:
            problems.append(f"{path} is longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], arguments) is None:
            problems.append(f"{path} does not match pattern {schema['pattern']!r}")
    if expected == "object" and isinstance(arguments, dict):
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in arguments:
                problems.append(f"{path}.{name} is required")
        if schema.get("additionalProperties") is False:
            for name in arguments.keys() - properties.keys():
                problems.append(f"{path}.{name} is not allowed")
        for name, value in arguments.items():
            if name in properties:
                problems.extend(validate_tool_arguments(value, properties[name], f"{path}.{name}"))
    if expected == "array" and isinstance(arguments, list) and isinstance(schema.get("items"), dict):
        if "minItems" in schema and len(arguments) < schema["minItems"]:
            problems.append(f"{path} has fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(arguments) > schema["maxItems"]:
            problems.append(f"{path} has more than maxItems {schema['maxItems']}")
        for index, value in enumerate(arguments):
            problems.extend(validate_tool_arguments(value, schema["items"], f"{path}[{index}]"))
    return problems
