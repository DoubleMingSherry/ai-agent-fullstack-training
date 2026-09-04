"""出口校验层：Structured Output 双层校验与修复边界。

双层校验：
- 第一层：请求时把业务 JSON Schema 带给上游（provider.py 注入 system）。
- 第二层：返回后用 Pydantic 本地校验——供应商的 Schema 保证不等于应用层安全。

修复边界：JSON 提取与修复合计至多 1 轮（直接解析失败 → 提取一次；
仍失败即 schema_validation_failed），不静默吞掉、不无限重试。

红线：未通过 Schema 校验的输出不得作为成功响应返回给调用方（不变量 1）。

Schema 时机：build_validator 在进入执行层前对请求方的业务 Schema 编译，
Schema 本身非法属于请求问题（invalid_request, 4xx）；而模型输出不满足
Schema 属于上游/网关失败（schema_validation_failed, 5xx）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from .errors import GatewayError

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# 本地校验支持的 JSON Schema 子集（其余类型直接视为非法 Schema，不放行）
_SUPPORTED_TYPES = {"string": str, "integer": int, "number": float, "boolean": bool}


def extract_json(text: str) -> Any:
    """从模型输出中提取 JSON：直接解析 → 代码围栏 → 平衡扫描，共 1 轮修复。"""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    for fence in _FENCE_RE.findall(text):
        try:
            return json.loads(fence.strip())
        except (json.JSONDecodeError, ValueError):
            continue
    start = text.find("{")
    while start != -1:
        candidate = _balanced_slice(text, start)
        if candidate is not None:
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                pass
        start = text.find("{", start + 1)
    raise GatewayError("schema_validation_failed", "输出中未找到合法 JSON")


def _balanced_slice(text: str, start: int) -> str | None:
    """从 text[start] 的 '{' 开始截取第一个括号平衡的片段（跳过字符串字面量）。"""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def build_validator(schema: dict[str, Any], name: str = "BusinessModel") -> type[BaseModel]:
    """把业务 JSON Schema 编译成 Pydantic 模型（本地第二层校验）。

    支持 object/properties/required、string(minLength/maxLength)、
    integer/number(minimum/maximum)、boolean、array(items)、enum、嵌套 object。
    对象固定 extra="forbid"：Schema 之外的字段同样不放行。
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise GatewayError(
            "invalid_request", "response_schema 必须是 object 类型的 JSON Schema"
        )
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise GatewayError("invalid_request", "response_schema.properties 不能为空")
    required = set(schema.get("required", []))
    fields: dict[str, tuple[Any, Any]] = {}
    for prop_name, prop in properties.items():
        annotation = _annotation(prop, prop_name)
        constraints = _constraints(prop)
        if prop_name in required:
            default: Any = ... if constraints is None else Field(..., **constraints)
            fields[prop_name] = (annotation, default)
        else:
            default = None if constraints is None else Field(None, **constraints)
            fields[prop_name] = (annotation | None, default)
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)


def _annotation(prop: dict[str, Any], name: str) -> Any:
    """属性类型标注（不含约束，约束由 _constraints 在属性层处理）。"""
    if "enum" in prop:
        return Literal[tuple(prop["enum"])]  # type: ignore[valid-type]
    json_type = prop.get("type")
    if json_type == "object":
        return build_validator(prop, name)
    if json_type == "array":
        items = prop.get("items", {})
        if not isinstance(items, dict) or (
            "type" not in items and "enum" not in items
        ):
            raise GatewayError("invalid_request", f"数组字段 {name} 缺少 items.type")
        return list[_annotation(items, name)]  # type: ignore[valid-type]
    py_type = _SUPPORTED_TYPES.get(json_type)
    if py_type is None:
        raise GatewayError(
            "invalid_request", f"字段 {name} 使用了不支持的类型: {json_type}"
        )
    return py_type


def _constraints(prop: dict[str, Any]) -> dict[str, Any] | None:
    constraints: dict[str, Any] = {}
    if prop.get("type") == "string":
        if "minLength" in prop:
            constraints["min_length"] = prop["minLength"]
        if "maxLength" in prop:
            constraints["max_length"] = prop["maxLength"]
    elif prop.get("type") in ("integer", "number"):
        if "minimum" in prop:
            constraints["ge"] = prop["minimum"]
        if "maximum" in prop:
            constraints["le"] = prop["maximum"]
    return constraints or None


def validate_structured(
    text: str,
    schema: dict[str, Any],
    validator: type[BaseModel] | None = None,
) -> Any:
    """第二层校验入口：提取（≤1 轮修复）→ Pydantic 校验，失败即明确报错。

    返回校验通过的 Python 数据；不通过时抛 schema_validation_failed，
    未通过 Schema 校验的输出绝不作为成功响应返回（不变量 1）。
    """
    data = extract_json(text)  # 内部含至多 1 轮提取修复
    model = validator or build_validator(schema)
    try:
        # strict=True：拒绝 pydantic 宽松强转（如 "5"→5），
        # 与 JSON Schema 的类型语义保持一致
        validated = model.model_validate(data, strict=True)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ()))
        raise GatewayError(
            "schema_validation_failed",
            f"输出不符合业务 Schema: {loc}: {first.get('msg')}",
        ) from exc
    if isinstance(validated, BaseModel):
        return validated.model_dump()  # 协议层只传纯数据，便于 JSON 序列化
    return validated
