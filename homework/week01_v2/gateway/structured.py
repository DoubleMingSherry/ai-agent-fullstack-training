"""Egress validation — layer 4 structured output.

Double validation:
  1. request time: the business JSON Schema is handed to the adapter (each
     protocol implements this differently inside its adapter);
  2. response time: the returned text is parsed and locally validated here.

Repair is BOUNDED: JSON extraction + fix combined run at most ONE round.  If
the output still fails, ``schema_validation_failed`` is raised — never silently
swallowed, never endlessly retried.  Output that fails this layer must not be
returned to the caller as a success (invariant 1).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal as TypingLiteral, Optional, Union

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, create_model

from .errors import GatewayError

JsonValue = Union[Dict[str, Any], List[Any], str, int, float, bool, None]

_SCALARS = (str, int, float, bool)
_MISSING = object()


def _literal_ok(values: Any) -> bool:
    return isinstance(values, (list, tuple)) and bool(values) and all(
        isinstance(v, (str, int, float, bool)) or v is None for v in values
    )


def _type_for(schema: Dict[str, Any]) -> Any:
    """Map a JSON-Schema subset onto a Python type usable by pydantic."""
    if not isinstance(schema, dict):
        return Any
    if "const" in schema and _literal_ok([schema["const"]]):
        return TypingLiteral[schema["const"]]  # type: ignore[index]
    if "enum" in schema and _literal_ok(schema.get("enum")):
        return TypingLiteral[tuple(schema["enum"])]  # type: ignore[index]
    union_parts = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(union_parts, list) and union_parts:
        mapped = [_type_for(p) for p in union_parts]
        if len(mapped) == 1:
            return mapped[0]
        return Union[tuple(mapped)]  # type: ignore[arg-type]
    schema_type = schema.get("type")
    if schema_type == "string":
        return str
    if schema_type == "number":
        return float
    if schema_type == "integer":
        return int
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        items = schema.get("items")
        item_type = _type_for(items) if isinstance(items, dict) else Any
        return List[item_type]  # type: ignore[valid-type]
    props = schema.get("properties")
    if isinstance(props, dict):
        return _model_for_object(schema)
    if schema_type in ("object",) or props is not None:
        return _model_for_object({**schema, "properties": props or {}})
    return Any


def _model_for_object(schema: Dict[str, Any]) -> Any:
    props: Dict[str, Any] = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    fields: Dict[str, Any] = {}
    for name, prop_schema in props.items():
        if not isinstance(prop_schema, dict):
            continue
        typ = _type_for(prop_schema)
        if name in required:
            fields[name] = (typ, ...)
        else:
            default = prop_schema.get("default", None)
            fields[name] = (Optional[typ], default)  # type: ignore[valid-type]
    extra = "forbid" if schema.get("additionalProperties") is False else "ignore"
    model = create_model(
        "GatewayOutput",
        __config__=ConfigDict(extra=extra),  # type: ignore[arg-type]
        **fields,
    )
    return model


def _is_pydantic_model(typ: Any) -> bool:
    return isinstance(typ, type) and issubclass(typ, BaseModel)


def _validate_value(value: JsonValue, schema: Dict[str, Any]) -> JsonValue:
    """Validate a parsed JSON value against the business schema via pydantic."""
    if not isinstance(schema, dict):
        raise GatewayError("schema_validation_failed", "business schema must be a JSON object")

    if "enum" in schema and value not in schema["enum"]:
        raise GatewayError(
            "schema_validation_failed", f"value {value!r} not in enum {schema['enum']!r}"
        )
    if "const" in schema and value != schema["const"]:
        raise GatewayError("schema_validation_failed", f"value {value!r} != const {schema['const']!r}")

    typ = _type_for(schema)
    try:
        if typ is Any:
            return value
        if _is_pydantic_model(typ):
            instance = typ.model_validate(value)
            return instance.model_dump()
        adapted = TypeAdapter(typ).validate_python(value)
        if _is_pydantic_model(type(adapted)):
            return adapted.model_dump()
        return adapted
    except ValidationError as exc:  # pydantic v2 error
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", [])) or "(root)"
        raise GatewayError(
            "schema_validation_failed",
            f"output does not conform to schema at {where}: {first.get('msg', 'invalid')}",
        ) from exc
    except GatewayError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise GatewayError("schema_validation_failed", f"output failed local validation: {exc}") from exc


# --------------------------------------------------------------------------
# parsing + bounded single-round repair
# --------------------------------------------------------------------------
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def _try_loads(text: str) -> Optional[JsonValue]:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _extract_json(text: str) -> Optional[JsonValue]:
    """Single best-effort extraction pass: fences -> substring scan -> comma fix."""
    stripped = _FENCE_RE.sub("", text).strip()
    candidate = _try_loads(stripped)
    if candidate is not None:
        return candidate
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = stripped.find(open_ch)
        if start < 0:
            continue
        depth = 0
        for idx in range(start, len(stripped)):
            ch = stripped[idx]
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    sub = stripped[start : idx + 1]
                    candidate = _try_loads(sub)
                    if candidate is not None:
                        return candidate
                    break
    fixed = re.sub(r",\s*([}\]])", r"\1", stripped)
    if fixed != stripped:
        candidate = _try_loads(fixed)
        if candidate is not None:
            return candidate
    return None


def validate_output_text(text: str, schema: Dict[str, Any]) -> JsonValue:
    """Parse + validate; perform at most one repair round; raise on final failure."""
    parsed = _try_loads(text)
    if parsed is None:
        parsed = _extract_json(text)  # repair round #1 (the only one)
        if parsed is None:
            raise GatewayError(
                "schema_validation_failed",
                "model output is not valid JSON and could not be repaired",
            )
    return _validate_value(parsed, schema)
