"""治理层：验收 2/3/4（unknown_model / unknown_prompt_template /
missing_prompt_variable），模板沙箱渲染与白名单等价性。"""

import pytest

from conftest import FakeProvider, chat_payload, get_json, make_app, post_json
from gateway.models import ModelCatalog, ModelSpec
from gateway.prompts import TemplateRegistry, TemplateSpec, render


def test_unknown_model_rejected():
    provider = FakeProvider([])
    app = make_app(provider)

    response = post_json(app, "/chat", chat_payload(model="nope-model"))

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "unknown_model"
    assert error["call_id"].startswith("call-")
    assert provider.calls == []  # 白名单外不进入执行层


def test_unknown_prompt_template_rejected():
    provider = FakeProvider([])
    app = make_app(provider)

    response = post_json(app, "/chat", chat_payload(prompt_name="ghost", version="9"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_prompt_template"
    assert provider.calls == []


def test_unknown_template_version_rejected():
    provider = FakeProvider([])
    app = make_app(provider)

    response = post_json(app, "/chat", chat_payload(prompt_name="answer", version="99"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_prompt_template"


def test_missing_prompt_variable_rejected():
    # answer@2 声明了 question + style，缺 style
    provider = FakeProvider([])
    app = make_app(provider)

    response = post_json(
        app, "/chat", chat_payload(variables={"question": "什么是幂等？"})
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_prompt_variable"
    assert provider.calls == []


def test_overlong_prompt_variable_rejected():
    provider = FakeProvider([])
    app = make_app(provider)

    response = post_json(
        app,
        "/chat",
        chat_payload(variables={"question": "长" * 2001, "style": "专业"}),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_prompt_variable"
    assert provider.calls == []


def test_malformed_body_returns_envelope():
    provider = FakeProvider([])
    app = make_app(provider)

    response = post_json(app, "/chat", {"messages": []})  # 缺 model/prompt

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["call_id"].startswith("call-")
    assert provider.calls == []


def test_rejected_requests_still_recorded():
    provider = FakeProvider([])
    app = make_app(provider)
    post_json(app, "/chat", chat_payload(model="nope-model"))

    traces = get_json(app, "/trace").json()["traces"]
    assert len(traces) == 1
    assert traces[0]["status"] == "failed"
    assert traces[0]["error_code"] == "unknown_model"


# ---- 模板沙箱渲染 ----


def test_render_conditionals():
    from gateway.prompts import default_templates

    registry = default_templates()
    tpl = registry.get("router", "1")
    strict_out = render(tpl, {"text": "退货运费谁出？", "strict": True})
    loose_out = render(tpl, {"text": "退货运费谁出？", "strict": False})
    assert "严格模式" in strict_out and "宽松模式" not in strict_out
    assert "宽松模式" in loose_out and "严格模式" not in loose_out
    assert "退货运费谁出？" in strict_out


def test_render_variable_substitution_only():
    from gateway.prompts import default_templates

    registry = default_templates()
    tpl = registry.get("answer", "2")
    out = render(tpl, {"question": "什么是重试风暴？", "style": "轻松"})
    assert "轻松" in out and "什么是重试风暴？" in out


def test_template_with_undeclared_variable_fails_registration():
    with pytest.raises(ValueError):
        TemplateRegistry(
            [
                TemplateSpec(
                    name="bad",
                    version="1",
                    template="{{ghost}}",
                    variables={},
                )
            ]
        )


# ---- 模型白名单与能力等价 ----


def test_fallback_must_be_capability_equivalent():
    caps_a = frozenset({"text", "structured"})
    caps_b = frozenset({"text", "structured", "tools"})
    with pytest.raises(ValueError):
        ModelCatalog(
            [
                ModelSpec(name="a", capabilities=caps_a, fallback="b"),
                ModelSpec(name="b", capabilities=caps_b),
            ]
        )


def test_fallback_must_exist_in_catalog():
    with pytest.raises(ValueError):
        ModelCatalog([ModelSpec(name="a", capabilities=frozenset({"text"}), fallback="ghost")])


def test_chain_is_primary_plus_at_most_one_fallback():
    from gateway.models import default_catalog

    catalog = default_catalog()
    assert [s.name for s in catalog.chain("chat-lite")] == [
        "chat-lite",
        "chat-lite-backup",
    ]
    assert [s.name for s in catalog.chain("chat-pro")] == ["chat-pro"]
