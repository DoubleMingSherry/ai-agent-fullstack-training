"""Reproduce DeepSeek Responses API max_output_tokens behavior.

Set DS_API_KEY, install ``openai``, then run this file. Raw HTTP uses only the
Python standard library and can be selected with ``--transport raw``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


BASE_URL = "https://api.deepseek.com"
PROMPT = """请详细分析 NVIDIA 最近的基本面和消息面。
尽量充分展开分析，包括营收、利润、数据中心业务、AI 芯片竞争格局、
近期重大消息、潜在风险和未来展望。"""
INSTRUCTIONS = "你是美股市场投资助手。没有可靠证据支持的事实，不要猜测。"


@dataclass(frozen=True)
class Case:
    name: str
    reasoning: str
    web_search: bool


CASES = (
    Case("1", "none", False),
    Case("2", "high", False),
    Case("3", "none", True),
    Case("4", "high", True),
)


def request_body(case: Case, model: str, limit: int) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "instructions": INSTRUCTIONS,
        "input": PROMPT,
        "max_output_tokens": limit,
        "reasoning": {"effort": case.reasoning},
    }
    if case.web_search:
        body["tools"] = [{"type": "web_search"}]
    return body


def call_sdk(body: dict[str, Any], api_key: str, timeout: float) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("SDK transport requires: python -m pip install 'openai>=1,<3'") from exc

    client = OpenAI(
        api_key=api_key,
        base_url=BASE_URL,
        timeout=timeout,
        max_retries=0,
    )
    response = client.responses.create(**body)
    return response.model_dump(mode="json")


def call_raw(body: dict[str, Any], api_key: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{BASE_URL}/v1/responses",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {response_body}") from exc


def summarize(
    case: Case,
    transport: str,
    body: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any]:
    usage = response.get("usage") or {}
    details = usage.get("output_tokens_details") or {}
    output_tokens = usage.get("output_tokens")
    reasoning_tokens = details.get("reasoning_tokens") or 0
    limit = body["max_output_tokens"]
    item_types = [item.get("type") for item in response.get("output") or []]
    return {
        "case": case.name,
        "transport": transport,
        "request": body,
        "response": {
            "id": response.get("id"),
            "status": response.get("status"),
            "incomplete_details": response.get("incomplete_details"),
            "error": response.get("error"),
        },
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": output_tokens,
            "total_tokens": usage.get("total_tokens"),
            "reasoning_tokens": reasoning_tokens,
            "approx_visible_tokens": (
                output_tokens - reasoning_tokens
                if isinstance(output_tokens, int)
                else None
            ),
        },
        "output_item_types": item_types,
        "output_item_type_counts": {
            item_type: item_types.count(item_type) for item_type in sorted(set(item_types))
        },
        "exceeded": isinstance(output_tokens, int) and output_tokens > limit,
    }


def print_table(results: list[dict[str, Any]]) -> None:
    print("| Case | Transport | Reasoning | Web Search | Limit | Output Tokens | Reasoning Tokens | Status | Exceeded |")
    print("|---|---|---|---|---:|---:|---:|---|---|")
    for result in results:
        request = result["request"]
        usage = result["usage"]
        response = result["response"]
        print(
            f"| {result['case']} | {result['transport']} | "
            f"{request['reasoning']['effort']} | {'yes' if request.get('tools') else 'no'} | "
            f"{request['max_output_tokens']} | {usage['output_tokens']} | "
            f"{usage['reasoning_tokens']} | {response['status']} | {result['exceeded']} |"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("sdk", "raw", "both"), default="both")
    parser.add_argument("--case", choices=("all", "1", "2", "3", "4"), default="all")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path, help="Optional JSONL result path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.getenv("DS_API_KEY")
    if not api_key:
        print("DS_API_KEY is not set; no API requests were sent.", file=sys.stderr)
        return 2
    if args.limit < 1:
        print("--limit must be positive", file=sys.stderr)
        return 2

    transports: dict[str, Callable[[dict[str, Any], str, float], dict[str, Any]]] = {
        "sdk": call_sdk,
        "raw": call_raw,
    }
    selected_transports = transports if args.transport == "both" else {args.transport: transports[args.transport]}
    selected_cases = CASES if args.case == "all" else tuple(case for case in CASES if case.name == args.case)
    results: list[dict[str, Any]] = []

    for case in selected_cases:
        body = request_body(case, args.model, args.limit)
        for transport, call in selected_transports.items():
            print(f"Running case {case.name} via {transport}...", file=sys.stderr)
            try:
                response = call(body, api_key, args.timeout)
                result = summarize(case, transport, body, response)
            except Exception as exc:  # Preserve other cases when one request fails.
                result = {
                    "case": case.name,
                    "transport": transport,
                    "request": body,
                    "response": {"status": None, "incomplete_details": None, "error": str(exc)},
                    "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "reasoning_tokens": None, "approx_visible_tokens": None},
                    "output_item_types": [],
                    "output_item_type_counts": {},
                    "exceeded": False,
                }
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), file=sys.stderr)

    print_table(results)
    if args.output:
        args.output.write_text(
            "".join(json.dumps(result, ensure_ascii=False) + "\n" for result in results),
            encoding="utf-8",
        )
        print(f"Wrote {args.output}", file=sys.stderr)
    return 1 if any(result["response"]["error"] for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
