"""Prompt-template governance (layer 2) with a restricted sandbox renderer.

Registry built-ins map (name, version) -> content + sha256 hash; every trace
keeps the name/version/hash actually used so behaviour is replayable.

The renderer is intentionally restricted — **no eval/exec, no arbitrary Python
expressions**.  Only two constructs exist:

* ``{{ variable_name }}``  — plain variable substitution
* ``{% if ... %}/{% elif ... %}/{% else %}/{% endif %}`` — conditional branches
  whose conditions are limited to ``not``/``and``/``or``, ``==``/``!=``
  comparisons between a variable and a literal (or another variable), plus
  bare-identifier truthiness.

Variables are schema-validated *before* rendering (missing / too long are
rejected and never reach the upstream request).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .errors import GatewayError

# --------------------------------------------------------------------------
# template syntax errors (internal content is trusted; these signal a bug)
# --------------------------------------------------------------------------
class TemplateSyntaxError(ValueError):
    pass


_MISSING = object()

# --------------------------------------------------------------------------
# condition language:  cond := or_ ; or_ := and_ ('or' and_)* ...
# --------------------------------------------------------------------------
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_STR_RE = re.compile(r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")")
_WORD_RE = re.compile(r"\S+")


class _TokenStream:
    def __init__(self, text: str) -> None:
        self.tokens = _WORD_RE.findall(text)
        self.pos = 0

    def peek(self) -> Optional[str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> str:
        tok = self.peek()
        if tok is None:
            raise TemplateSyntaxError("unexpected end of condition")
        self.pos += 1
        return tok

    def at_end(self) -> bool:
        return self.pos >= len(self.tokens)


def _parse_string(token: str) -> str:
    m = _STR_RE.fullmatch(token)
    if not m:
        raise TemplateSyntaxError(f"bad string literal {token!r}")
    inner = token[1:-1]
    return inner.encode("utf-8").decode("unicode_escape")


def _parse_primary(ts: _TokenStream) -> Any:
    tok = ts.next()
    if tok in ("True", "true"):
        return True
    if tok in ("False", "false"):
        return False
    if tok in ("None", "null"):
        return None
    if _STR_RE.fullmatch(tok):
        return _parse_string(tok)
    if _NUM_RE.fullmatch(tok):
        return float(tok) if "." in tok else int(tok)
    if _IDENT_RE.fullmatch(tok):
        return ("var", tok)
    raise TemplateSyntaxError(f"unexpected token {tok!r} in condition")


def _parse_comparison(ts: _TokenStream) -> Any:
    left = _parse_primary(ts)
    op = ts.peek()
    if op in ("==", "!="):
        ts.next()
        right = _parse_primary(ts)
        return (op, left, right)
    return left


def _parse_not(ts: _TokenStream) -> Any:
    if ts.peek() == "not":
        ts.next()
        return ("not", _parse_not(ts))
    return _parse_comparison(ts)


def _parse_and(ts: _TokenStream) -> Any:
    node = _parse_not(ts)
    while ts.peek() == "and":
        ts.next()
        node = ("and", node, _parse_not(ts))
    return node


def _parse_or(ts: _TokenStream) -> Any:
    node = _parse_and(ts)
    while ts.peek() == "or":
        ts.next()
        node = ("or", node, _parse_and(ts))
    return node


def parse_condition(text: str) -> Any:
    ts = _TokenStream(text)
    if ts.at_end():
        raise TemplateSyntaxError("empty condition")
    node = _parse_or(ts)
    if not ts.at_end():
        raise TemplateSyntaxError(f"trailing tokens in condition {text!r}")
    return node


# --------------------------------------------------------------------------
# template body parser -> AST  (recursive, tag-driven)
# --------------------------------------------------------------------------
_TAG_START_RE = re.compile(r"\{\{|\{%")

# AST node types: ("text", str) | ("var", name) | ("if", branches, else_items)
#   branches: list[(condition, items)]; else_items: list
Items = List[Any]


def _parse_block(text: str, start: int, stop: set) -> Tuple[Items, Optional[str], Optional[str], int]:
    """Parse ``text[start:]``; stop at the first tag whose keyword is in ``stop``.

    Returns (items, stop_keyword, stop_argument, next_index).
    """
    items: Items = []
    i = start
    n = len(text)
    while True:
        m = _TAG_START_RE.search(text, i)
        if m is None:
            tail = text[i:]
            if stop:
                raise TemplateSyntaxError(f"expected {'/'.join(sorted(stop))} before end of template")
            if tail:
                items.append(("text", tail))
            return items, None, None, n
        if m.start() > i:
            items.append(("text", text[i:m.start()]))
        opener = m.group(0)
        closer = "}}" if opener == "{{" else "%}"
        close_at = text.find(closer, m.end())
        if close_at < 0:
            raise TemplateSyntaxError(f"unclosed {opener!r} at offset {m.start()}")
        inner = text[m.end():close_at].strip()
        after = close_at + len(closer)
        if opener == "{{":
            if not _IDENT_RE.fullmatch(inner):
                raise TemplateSyntaxError(f"bad variable reference {inner!r}")
            items.append(("var", inner))
            i = after
            continue
        # {% ... %} control tag
        parts = inner.split(None, 1)
        kw = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
        if kw in stop:
            return items, kw, arg, after
        if kw == "if":
            cond1 = parse_condition(arg)
            then_items, sw, sw_arg, ni = _parse_block(text, after, {"elif", "else", "endif"})
            branches: List[Tuple[Any, Items]] = [(cond1, then_items)]
            else_items: Items = []
            while sw == "elif":
                branches.append((parse_condition(sw_arg or ""), []))
                sub_items, sw, sw_arg, ni = _parse_block(text, ni, {"elif", "else", "endif"})
                branches[-1] = (branches[-1][0], sub_items)
            if sw == "else":
                else_items, sw, sw_arg, ni = _parse_block(text, ni, {"endif"})
            if sw != "endif":
                raise TemplateSyntaxError("missing {% endif %}")
            items.append(("if", branches, else_items))
            i = ni
            continue
        if kw in ("elif", "else", "endif"):
            raise TemplateSyntaxError(f"unexpected {{% {kw} %}}")
        raise TemplateSyntaxError(f"unknown template tag {kw!r}")


def parse_template(content: str) -> Items:
    items, sw, _arg, _ni = _parse_block(content, 0, set())
    return items


# --------------------------------------------------------------------------
# value resolution & rendering (sandbox: only registered variables reach here)
# --------------------------------------------------------------------------
def _lookup(name: str, variables: Dict[str, str]) -> Any:
    return variables.get(name, _MISSING)


def _truthy(value: Any) -> bool:
    if value is _MISSING or value is None:
        return False
    if isinstance(value, str):
        return value != ""
    return bool(value)


def _eval_cond(node: Any, variables: Dict[str, str]) -> bool:
    kind = node[0] if isinstance(node, tuple) else None
    if isinstance(node, tuple) and kind in ("var",):
        return _truthy(_lookup(node[1], variables))
    if kind == "not":
        return not _eval_cond(node[1], variables)
    if kind == "and":
        return _eval_cond(node[1], variables) and _eval_cond(node[2], variables)
    if kind == "or":
        return _eval_cond(node[1], variables) or _eval_cond(node[2], variables)
    if kind == "==":
        return _eq_value(node[1], node[2], variables)
    if kind == "!=":
        return not _eq_value(node[1], node[2], variables)
    # plain literal
    return _truthy(node)


def _resolve(operand: Any, variables: Dict[str, str]) -> Any:
    if isinstance(operand, tuple) and operand and operand[0] == "var":
        return _lookup(operand[1], variables)
    return operand


def _eq_value(left: Any, right: Any, variables: Dict[str, str]) -> bool:
    a, b = _resolve(left, variables), _resolve(right, variables)
    if a is _MISSING:
        a = None
    if b is _MISSING:
        b = None
    # numbers may arrive as int/float from JSON schema parsing
    try:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
            return float(a) == float(b)
    except (TypeError, ValueError):
        pass
    return a == b


def _render_items(items: Items, variables: Dict[str, str], out: List[str]) -> None:
    for node in items:
        kind = node[0]
        if kind == "text":
            out.append(node[1])
        elif kind == "var":
            value = _lookup(node[1], variables)
            if value is _MISSING:
                raise GatewayError(
                    "missing_prompt_variable",
                    f"template variable {node[1]!r} is referenced but not provided",
                )
            out.append(str(value))
        elif kind == "if":
            branches, else_items = node[1], node[2]
            chosen: Optional[Items] = None
            for cond, sub in branches:
                if _eval_cond(cond, variables):
                    chosen = sub
                    break
            if chosen is None:
                chosen = else_items
            _render_items(chosen or [], variables, out)
        else:  # pragma: no cover - parser cannot produce other kinds
            raise GatewayError("internal_error", f"unknown template node {kind!r}")


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
@dataclass
class PromptTemplate:
    name: str
    version: int
    content: str
    required: Tuple[str, ...] = ()
    optional: Tuple[str, ...] = ()
    max_length: int = 4_000

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def compile(self) -> Items:
        return parse_template(self.content)

    def validate_variables(self, variables: Dict[str, str]) -> None:
        """Schema-validate input variables before rendering (no upstream call)."""
        missing = [v for v in self.required if v not in variables or variables[v] == ""]
        if missing:
            raise GatewayError(
                "missing_prompt_variable",
                f"template {self.name} v{self.version} requires variable(s): {', '.join(missing)}",
            )
        for name, value in variables.items():
            if name not in self.required and name not in self.optional:
                continue  # unknown extra variables are ignored (never rendered)
            if len(value) > self.max_length:
                raise GatewayError(
                    "invalid_prompt_variable",
                    f"variable {name!r} too long ({len(value)} > {self.max_length})",
                )

    def render(self, variables: Dict[str, str]) -> str:
        self.validate_variables(variables)
        out: List[str] = []
        _render_items(self.compile(), variables, out)
        return "".join(out)


def _builtin_chat_v2() -> str:
    return (
        "You are {{role}} with deep expertise in {{domain}}.\n"
        "{% if style == 'brief' %}Be brief: short, direct answers, no filler."
        "{% elif style == 'detailed' %}Be thorough: structured, detailed explanations."
        "{% else %}Balance brevity and detail for the audience."
        "{% endif %}"
    )


def _builtin_qa_v1() -> str:
    return (
        "Answer in {{language}}.\n"
        "{% if format == 'bullets' %}Use bullet points."
        "{% elif format == 'table' %}Present the answer as a table."
        "{% else %}Use plain prose."
        "{% endif %}\n"
        "Question: {{question}}"
    )


class TemplateRegistry:
    """(name, version) -> PromptTemplate.  version=None resolves the newest."""

    def __init__(self, templates: Sequence[PromptTemplate]) -> None:
        self._by_name: Dict[str, Dict[int, PromptTemplate]] = {}
        for tpl in templates:
            self._by_name.setdefault(tpl.name, {})[tpl.version] = tpl
        # compile everything eagerly: syntax bugs are caught at startup, not per call
        for name, versions in self._by_name.items():
            for tpl in versions.values():
                tpl.compile()

    def resolve(self, name: str, version: Optional[int] = None) -> PromptTemplate:
        versions = self._by_name.get(name)
        if not versions:
            raise GatewayError("unknown_prompt_template", f"unknown prompt template {name!r}")
        if version is None:
            version = max(versions)
        tpl = versions.get(version)
        if tpl is None:
            raise GatewayError(
                "unknown_prompt_template",
                f"prompt template {name!r} has no version {version}",
            )
        return tpl

    def names(self) -> List[str]:
        return sorted(self._by_name)


def builtin_templates() -> TemplateRegistry:
    return TemplateRegistry(
        [
            PromptTemplate(name="chat", version=1, content="You are a helpful AI assistant. Answer concisely and accurately."),
            PromptTemplate(
                name="chat",
                version=2,
                content=_builtin_chat_v2(),
                required=("role", "domain"),
                optional=("style",),
            ),
            PromptTemplate(
                name="qa",
                version=1,
                content=_builtin_qa_v1(),
                required=("language", "question"),
                optional=("format",),
            ),
        ]
    )
