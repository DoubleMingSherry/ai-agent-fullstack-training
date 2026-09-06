"""Prompt-template governance tests: multi-version registry, conditional
branching, sandbox restrictions, variable validation and hashes."""

from __future__ import annotations

import pytest

from gateway.errors import GatewayError
from gateway.templates import TemplateSyntaxError, builtin_templates, parse_template


def test_chat_is_multi_version_registry():
    registry = builtin_templates()
    v1 = registry.resolve("chat", 1)
    v2 = registry.resolve("chat", 2)
    assert v1.version != v2.version
    assert v1.hash != v2.hash
    assert v1.content != v2.content


def test_version_defaults_to_newest():
    registry = builtin_templates()
    assert registry.resolve("chat").version == 2


def test_conditional_branch_brief_vs_detailed_vs_default():
    tpl = builtin_templates().resolve("chat", 2)
    brief = tpl.render({"role": "r", "domain": "d", "style": "brief"})
    assert "Be brief" in brief and "Be thorough" not in brief
    detailed = tpl.render({"role": "r", "domain": "d", "style": "detailed"})
    assert "Be thorough" in detailed and "Be brief" not in detailed
    default = tpl.render({"role": "r", "domain": "d"})
    assert "Balance brevity and detail" in default


def test_qa_template_conditionals():
    tpl = builtin_templates().resolve("qa", 1)
    bullets = tpl.render({"language": "zh", "question": "q?", "format": "bullets"})
    assert "bullet points" in bullets and "Answer in zh" in bullets
    table = tpl.render({"language": "en", "question": "q?", "format": "table"})
    assert "as a table" in table
    prose = tpl.render({"language": "en", "question": "q?"})
    assert "plain prose" in prose


def test_unknown_extra_variables_are_ignored():
    tpl = builtin_templates().resolve("chat", 2)
    text = tpl.render({"role": "r", "domain": "d", "whatever": "ignored"})
    assert "ignored" not in text


def test_validation_codes_match_gateway_codes():
    tpl = builtin_templates().resolve("chat", 2)
    with pytest.raises(GatewayError) as exc:
        tpl.render({"role": "only"})
    assert exc.value.code == "missing_prompt_variable"
    with pytest.raises(GatewayError) as exc:
        tpl.render({"role": "r", "domain": "d" * 5000})
    assert exc.value.code == "invalid_prompt_variable"


def test_unknown_template_and_version_raise_registry_codes():
    registry = builtin_templates()
    with pytest.raises(GatewayError) as exc:
        registry.resolve("ghost")
    assert exc.value.code == "unknown_prompt_template"
    with pytest.raises(GatewayError) as exc:
        registry.resolve("chat", 99)
    assert exc.value.code == "unknown_prompt_template"


def test_renderer_rejects_arbitrary_tags():
    with pytest.raises(TemplateSyntaxError):
        parse_template("{% include 'other' %}")


def test_renderer_is_sandboxed_no_eval_exec():
    source = open("gateway/templates.py", encoding="utf-8").read()
    assert "eval(" not in source
    assert "exec(" not in source
    assert "__import__" not in source
