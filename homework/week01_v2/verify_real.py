"""Real-model smoke script for the mini LLM gateway.

Runs, against the REAL endpoints configured via environment variables, the
four call shapes for BOTH real logical models and prints the observability
evidence of every call (classified token usage / TTFT / latency / cost).

Call shapes per model:  plain text, streaming, structured output, template ref.

Credentials come exclusively from environment variables (never hard-coded):

    RESPONSES_API_KEY  / RESPONSES_BASE_URL   (deepseek-v4-pro  -> Responses API)
    MESSAGES_API_KEY   / MESSAGES_BASE_URL    (deepseek-v4-flash -> Messages API)

Usage:
    python verify_real.py

Exit codes: 0 = all four shapes succeeded on both models;
            2 = missing credentials (nothing was sent);
            1 = an upstream/gateway error occurred.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, AsyncIterator, Dict, List

import httpx

from gateway.app import create_real_app

MODELS = ["deepseek-v4-pro", "deepseek-v4-flash"]

PLAIN_MESSAGES = [{"role": "user", "content": "Reply with one short sentence about the weather."}]

WEATHER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "temp_c": {"type": "number"},
        "conditions": {"type": "string"},
    },
    "required": ["city", "temp_c", "conditions"],
}

CHAT_V2_PROMPT = {
    "name": "chat",
    "version": 2,
    "variables": {"role": "travel assistant", "domain": "weather", "style": "brief"},
}


def _print_stats(label: str, payload: Dict[str, Any], requested: Any = None) -> bool:
    """Print one call's evidence; return True when the backup model served it."""
    usage = payload.get("usage") or {}
    ttft = payload.get("ttft_ms")
    model_used = payload.get("model_used")
    print(f"  [{label}]")
    print(
        f"    model_used={model_used}  adapter={payload.get('adapter')}  "
        f"attempts={payload.get('attempts')}"
    )
    fallback_used = bool(requested) and bool(model_used) and model_used != requested
    if fallback_used:
        print(
            f"    ⚠ 主模型 {requested} 上游失败，本次由备用模型 {model_used} 完成"
            f"（attempts={payload.get('attempts')}）"
        )
    print(
        "    tokens: input={input_tokens} output={output_tokens} cache_read={cache_read_input_tokens} "
        "cache_create={cache_creation_input_tokens}".format(**usage)
    )
    # TTFT 只存在于流式调用；非流式无“首 Token”，如实打印 n/a。
    # 注意：格式说明符不能直接挂在条件表达式上（str 不接受 .1f），先算好字符串。
    ttft_label = "n/a (non-streaming)" if ttft is None else f"{ttft:.1f}"
    print(
        f"    cost=${payload.get('cost', 0):.6f}  latency_ms={payload.get('latency_ms'):.1f}  "
        f"ttft_ms={ttft_label}"
    )
    prompt = payload.get("prompt")
    if prompt:
        print(
            f"    prompt: name={prompt['name']} v{prompt['version']} hash={prompt['hash'][:16]}…"
        )
    if payload.get("structured") is not None:
        print(f"    structured={payload.get('structured')}")
    print(f"    preview={str(payload.get('text'))[:80]!r}")
    return fallback_used


def _ensure_ok(resp: httpx.Response, label: str) -> None:
    """Turn gateway/upstream failures into a readable envelope message."""
    if resp.status_code < 400:
        return
    code: Any = "http_error"
    message: Any = resp.text[:300]
    call_id: Any = None
    try:
        error = resp.json().get("error") or {}
        code = error.get("code", code)
        message = error.get("message", message)
        call_id = error.get("call_id")
    except Exception:  # noqa: BLE001 - non-JSON error body
        pass
    hint = ""
    if code in ("upstream_error", "fallback_exhausted"):
        hint = (
            "\n    提示：HTTP 502 = 网关没能从上游拿到结果（主/备模型均失败）。"
            "多半是 API Key / Base URL 不对或上游不可达："
            "检查 RESPONSES_API_KEY / MESSAGES_API_KEY（以及 *_BASE_URL），"
            "改完环境变量后需重启 uvicorn 才生效。"
        )
    raise RuntimeError(
        f"[{label}] gateway HTTP {resp.status_code} -> code={code} "
        f"message={message!r} call_id={call_id}{hint}"
    )


async def _run_one(client: httpx.AsyncClient, model: str) -> int:
    """Run the four call shapes; return how many were served by the backup."""
    print(f"\n=== {model} ===")
    fallbacks = 0

    # 1) plain (non-streaming)
    resp = await client.post(
        "/chat", json={"model": model, "messages": PLAIN_MESSAGES}
    )
    _ensure_ok(resp, "plain chat")
    fallbacks += int(_print_stats("plain chat", resp.json(), requested=model))

    # 2) streaming
    deltas: List[str] = []
    done: Dict[str, Any] = {}
    async with client.stream(
        "POST", "/stream", json={"model": model, "messages": PLAIN_MESSAGES}
    ) as resp:
        _ensure_ok(resp, "stream")
        async for event in _iter_sse_frames(resp.aiter_lines()):
            if event["event"] == "content.delta":
                deltas.append(event["data"]["delta"])
            elif event["event"] == "content.done":
                done = event["data"]
            elif event["event"] == "response.failed":
                raise RuntimeError(f"stream failed: {event['data']}")
    done["text"] = "".join(deltas)
    fallbacks += int(_print_stats(f"stream ({len(deltas)} deltas)", done, requested=model))

    # 3) structured output (schema + plain)
    resp = await client.post(
        "/chat",
        json={
            "model": model,
            "messages": PLAIN_MESSAGES,
            "schema": WEATHER_SCHEMA,
        },
    )
    _ensure_ok(resp, "structured output")
    fallbacks += int(_print_stats("structured output", resp.json(), requested=model))

    # 4) template reference (chat v2 with variables)
    resp = await client.post(
        "/chat",
        json={
            "model": model,
            "messages": PLAIN_MESSAGES,
            "prompt": CHAT_V2_PROMPT,
        },
    )
    _ensure_ok(resp, "template ref (chat v2)")
    fallbacks += int(_print_stats("template ref (chat v2)", resp.json(), requested=model))

    if fallbacks:
        print(
            f"  ⚠ {model} 有 {fallbacks}/4 次调用由备用模型完成"
            f"—— 主通道没有直连成功，见文件末尾的排查提示"
        )
    return fallbacks


async def _iter_sse_frames(lines: AsyncIterator[str]) -> AsyncIterator[Dict[str, Any]]:
    """Re-assemble ``event: <name>`` + ``data: <json>`` SSE lines into events."""
    name: Any = None
    async for line in lines:
        line = line.rstrip("\r")
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            yield {"event": name, "data": json.loads(line[len("data:"):].strip())}
            name = None


async def _main() -> int:
    from gateway.config import messages_credentials, responses_credentials

    # 默认：进程内自建网关（无需先启动 uvicorn，trace 随进程退出而消失）。
    # 若设置 VERIFY_GATEWAY_URL，则直连外部运行中的网关 —— 调用与 trace
    # 都会记在那台服务器上，之后可用 GET /trace 查询。
    gateway_url = os.getenv("VERIFY_GATEWAY_URL") or None

    if gateway_url is None and not (responses_credentials()[0] and messages_credentials()[0]):
        print(
            "verify_real.py needs REAL credentials — nothing was sent.\n"
            "Set both protocol keys first, e.g. (Windows PowerShell):\n"
            "  $env:RESPONSES_API_KEY='<key>'      # deepseek-v4-pro (Responses API)\n"
            "  $env:MESSAGES_API_KEY='<key>'       # deepseek-v4-flash (Messages API)\n"
            "Optionally set RESPONSES_BASE_URL / MESSAGES_BASE_URL to point at a gateway.\n"
            "Or point at an already-running gateway: $env:VERIFY_GATEWAY_URL='http://127.0.0.1:8000'\n"
        )
        return 2

    if gateway_url is not None:
        # 直连外部网关：凭证与 trace 都在那台服务器上
        client_ctx = httpx.AsyncClient(base_url=gateway_url, timeout=180.0)
    else:
        app = create_real_app()
        client_ctx = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        )

    async with client_ctx as client:
        fallback_total = 0
        for model in MODELS:
            fallback_total += await _run_one(client, model)
        print("\n=== trace check ===")
        traces = (await client.get("/trace")).json()["traces"]
        print(f"  {len(traces)} trace(s) recorded; newest 3 call ids:")
        for t in traces[:3]:
            ttft = "n/a" if t["ttft_ms"] is None else f"{t['ttft_ms']:.1f}ms"
            print(
                f"    {t['call_id']} status={t['status']} model_used={t['model_used']} "
                f"attempts={t['attempts']} ttft={ttft}"
            )
    if fallback_total:
        print(
            "\n⚠ 注意：有调用由备用模型完成（model_used 与请求模型不一致，attempts>1）。\n"
            "   deepseek-v4-pro 若全部 fallback 到 deepseek-v4-flash，说明它的 Responses 通道未直连成功，\n"
            "   请单独排查 RESPONSES_API_KEY / RESPONSES_BASE_URL，或先确认模型在 Responses 通道可用。"
        )
    print("\nverify_real: OK (all shapes completed on both real models)")
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(_main())
    except RuntimeError as exc:  # 网关 envelope 类错误：可读输出，不打印整段堆栈
        print(f"\nverify_real FAILED: {exc}", file=sys.stderr)
        exit_code = 1
    sys.exit(exit_code)
