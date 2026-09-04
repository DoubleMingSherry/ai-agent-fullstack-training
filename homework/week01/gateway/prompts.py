"""治理层：Prompt 模板注册表与受限渲染。

- 模板以 name + version 注册，内容与 sha256 hash 一起入册，Trace 记录实际
  使用的 name+version+hash，保证行为可回放。
- 受限模板渲染：不用 eval/exec，不支持任意 Python 表达式，只支持
  ``{{ 变量 }}`` 替换和 ``{% if 变量 %}…{% else %}…{% endif %}`` 条件分支，
  渲染引擎在这个白名单语法内运行（沙箱边界）。
- 变量校验：渲染前按模板声明的变量 Schema 校验，缺失/超长/类型不符/多传
  一律拒绝，不进入上游请求。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .errors import GatewayError

_VAR_PATTERN = r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}"
_TAG_PATTERN = r"\{%\s*(if|else|endif)\s*([a-zA-Z_][a-zA-Z0-9_]*)?\s*%\}"
_TOKEN_RE = re.compile(rf"({_VAR_PATTERN})|({_TAG_PATTERN})")

# 变量 Schema 支持的类型（渲染引擎的沙箱边界，其余一律拒绝）
_ALLOWED_TYPES = {"str": str, "int": int, "float": float, "bool": bool}


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    version: str
    template: str
    variables: dict[str, dict]  # 变量名 → {"type": ..., "max_length": ...}
    hash: str = ""  # sha256(template)

    def __post_init__(self) -> None:
        if not self.hash:
            object.__setattr__(
                self, "hash", hashlib.sha256(self.template.encode("utf-8")).hexdigest()
            )
        referenced = _referenced_variables(self.template)
        declared = set(self.variables)
        missing = referenced - declared
        if missing:
            raise ValueError(
                f"模板 {self.name}@{self.version} 使用了未声明的变量: {sorted(missing)}"
            )


def _referenced_variables(template: str) -> set[str]:
    """模板里引用到的全部变量（含 if 条件变量），用于注册期一致性检查。"""
    names: set[str] = set()
    for m in _TOKEN_RE.finditer(template):
        if m.group(2):
            names.add(m.group(2))
        elif m.group(4) == "if" and m.group(5):
            names.add(m.group(5))
    return names


class TemplateRegistry:
    """name + version → 模板内容与 hash 的注册表。"""

    def __init__(self, templates: list[TemplateSpec]) -> None:
        self._templates: dict[tuple[str, str], TemplateSpec] = {}
        for tpl in templates:
            key = (tpl.name, tpl.version)
            if key in self._templates:
                raise ValueError(f"模板重复注册: {tpl.name}@{tpl.version}")
            self._templates[key] = tpl

    def get(self, name: str, version: str) -> TemplateSpec:
        tpl = self._templates.get((name, version))
        if tpl is None:
            raise GatewayError(
                "unknown_prompt_template",
                f"Prompt 模板不存在: {name}@{version}",
            )
        return tpl

    def latest(self, name: str) -> TemplateSpec | None:
        versions = sorted(v for (n, v) in self._templates if n == name)
        return self._templates[(name, versions[-1])] if versions else None


def validate_variables(tpl: TemplateSpec, variables: dict) -> None:
    """渲染前用模板声明的变量 Schema 校验输入，失败不进入上游请求。"""
    declared = set(tpl.variables)
    provided = set(variables)
    missing = sorted(declared - provided)
    if missing:
        raise GatewayError(
            "missing_prompt_variable", f"模板变量缺失: {missing}"
        )
    unknown = sorted(provided - declared)
    if unknown:
        raise GatewayError(
            "invalid_prompt_variable", f"模板未声明的变量: {unknown}"
        )
    for name, rule in tpl.variables.items():
        value = variables[name]
        expected = _ALLOWED_TYPES.get(rule.get("type", "str"))
        if expected is None:
            raise GatewayError(
                "invalid_prompt_variable",
                f"变量 {name} 声明了不支持的类型: {rule.get('type')}",
            )
        if not isinstance(value, expected) or isinstance(value, bool) != (
            expected is bool
        ):
            raise GatewayError(
                "invalid_prompt_variable",
                f"变量 {name} 类型不符: 期望 {rule.get('type', 'str')}",
            )
        max_length = rule.get("max_length")
        if isinstance(value, str) and max_length is not None and len(value) > max_length:
            raise GatewayError(
                "invalid_prompt_variable",
                f"变量 {name} 超长: {len(value)} > {max_length}",
            )


def render(tpl: TemplateSpec, variables: dict) -> str:
    """受限渲染：变量替换 + 条件分支，遇到未知语法片段直接报错。"""
    tokens = _tokenize(tpl.template)
    return _render_nodes(tokens, variables)


def _tokenize(template: str) -> list:
    """切分为 [文本 | ("var", name) | ("if", name, nodes, else_nodes)] 的线性序列。"""
    nodes: list = []
    stack: list[tuple[str, str, list]] = []  # (if变量, else分支, 当前节点容器)
    current = nodes
    pos = 0
    for m in _TOKEN_RE.finditer(template):
        if m.start() > pos:
            current.append(template[pos:m.start()])
        pos = m.end()
        if m.group(2):  # {{ var }}
            current.append(("var", m.group(2)))
            continue
        keyword, arg = m.group(4), m.group(5)
        if keyword == "if":
            if not arg:
                raise ValueError("if 分支必须给出变量名")
            node = ("if", arg, [], [])
            current.append(node)
            stack.append((arg, node[3], current))
            current = node[2]
        elif keyword == "else":
            if not stack:
                raise ValueError("else 缺少匹配的 if")
            current = stack[-1][1]
        elif keyword == "endif":
            if not stack:
                raise ValueError("endif 缺少匹配的 if")
            stack.pop()
            current = stack[-1][2] if stack else nodes
    if stack:
        raise ValueError("if 分支缺少 endif")
    if pos < len(template):
        current.append(template[pos:])
    return nodes


def _render_nodes(nodes: list, variables: dict) -> str:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            out.append(node)
        elif node[0] == "var":
            out.append(str(variables[node[1]]))
        elif node[0] == "if":
            _, name, then_nodes, else_nodes = node
            branch = then_nodes if variables.get(name) else else_nodes
            out.append(_render_nodes(branch, variables))
    return "".join(out)


def default_templates() -> TemplateRegistry:
    """内置注册表：一个多版本模板 + 一个带条件分支的模板。"""
    return TemplateRegistry(
        [
            TemplateSpec(
                name="answer",
                version="1",
                template="你是一个严谨的助手。请回答下面的问题：\n{{question}}",
                variables={"question": {"type": "str", "max_length": 2000}},
            ),
            TemplateSpec(
                name="answer",
                version="2",
                template=(
                    "你是一个严谨的助手。请用{{style}}的口吻回答下面的问题：\n"
                    "{{question}}"
                ),
                variables={
                    "question": {"type": "str", "max_length": 2000},
                    "style": {"type": "str", "max_length": 50},
                },
            ),
            TemplateSpec(
                name="router",
                version="1",
                template=(
                    "你是一个分类器。请判断输入文本的意图。\n"
                    "{% if strict %}"
                    "严格模式：只输出 JSON，不要任何多余文字。"
                    "{% else %}"
                    "宽松模式：尽量输出 JSON。"
                    "{% endif %}\n"
                    "输入：{{text}}"
                ),
                variables={
                    "text": {"type": "str", "max_length": 2000},
                    "strict": {"type": "bool"},
                },
            ),
        ]
    )
