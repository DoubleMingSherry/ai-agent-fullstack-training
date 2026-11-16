"""安装：python -m pip install -U litellm
运行：python litellm_deepseek_demo.py
只需已有环境变量 DEEPSEEK_API_KEY；不需要命令行参数或 Gateway 服务。
"""
import asyncio
import json
import os
from pathlib import Path
from time import perf_counter
from datetime import datetime

# 使用随包提供的模型资料，避免导入时下载远程价格表。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
import litellm
from litellm import Router
from litellm.caching.caching import Cache
from litellm.router import RetryPolicy

ALIAS = "agent-default"
MODEL = "deepseek-flash"
OUTPUT = 256
RECORDS = []


def make_router(key, *, rpm=60, parallel=2, retries=1):
    return Router(
        model_list=[{
            "model_name": ALIAS,
            "litellm_params": {
                # 用 OpenAI 兼容适配器调用真实 DeepSeek，不使用 OpenAI 模型。
                "model": f"openai/{MODEL}",
                "api_base": "https://api.deepseek.com",
                "api_key": key,
                "rpm": rpm,
                "max_parallel_requests": parallel,
            },
        }],
        cache_responses=True,
        enable_pre_call_checks=True,
        routing_strategy="usage-based-routing-v2",
        num_retries=retries,
        retry_after=1,
        timeout=60,
        stream_timeout=60,
        disable_cooldowns=True,
        retry_policy=RetryPolicy(
            DefaultRetries=0,
            AuthenticationErrorRetries=0,
            BadRequestErrorRetries=0,
            TimeoutErrorRetries=retries,
            RateLimitErrorRetries=retries,
            InternalServerErrorRetries=retries,
            ServiceUnavailableErrorRetries=retries,
        ),
    )


def messages(revision="before-fix", question=None):
    # 版本、权限及工具状态进入输入：状态不同，不应命中旧回答。
    code = "return now <= expires_at" if revision == "before-fix" else "return now < expires_at"
    evidence = "恰好到期测试：预期 False，实际 True。" if revision == "before-fix" else "三个边界测试均通过。"
    return [
        {"role": "system", "content": (
            "用中文简短回答，只分析，不修改文件。"
            f"代码版本={revision}；权限=只读；工具快照=无工具-v1。"
        )},
        {"role": "user", "content": question or (
            "规则：到期立即失效，不得修改现有测试。\n"
            f"当前源码：{code}\n当前证据：{evidence}\n"
            "指出边界判断是否正确，并说明依据。"
        )},
    ]


async def call(router, label, input_messages, *, use_cache=True, print_text=True):
    start = perf_counter()
    first = None
    stream = None
    text_parts = []
    record = {"label": label, "route": ALIAS, "target_model": MODEL,
              "cache_enabled": use_cache, "cache_hit": None,
              "usage": None, "finish_reason": None}
    print(f"\n--- {label} ---")
    try:
        stream = await router.acompletion(
            model=ALIAS,
            messages=input_messages,
            temperature=0,
            max_tokens=OUTPUT,
            stream=True,
            stream_options={"include_usage": True},
            extra_body={"thinking": {"type": "disabled"}},
            caching=use_cache,
            # 限流/并发实验禁用读写缓存，确保请求走真实调用路径。
            cache={"no-cache": not use_cache, "no-store": not use_cache},
        )
        # LiteLLM 当前流式缓存实现用此标记；不是根据速度猜测命中。
        record["cache_hit"] = getattr(stream, "custom_llm_provider", None) == "cached_response"
        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                record["usage"] = chunk.usage.model_dump()
            for choice in chunk.choices or []:
                if choice.finish_reason:
                    record["finish_reason"] = choice.finish_reason
                part = getattr(choice.delta, "content", None)
                if part:
                    if first is None:
                        first = perf_counter()
                    text_parts.append(part)
                    if print_text:
                        print(part, end="", flush=True)
        record["status"] = "ok"
    except Exception as error:
        # 流式输出途中失败时，不重放整段请求；保留部分结果和失败记录。
        record.update(status="error", error_type=type(error).__name__,
                      http_status=getattr(error, "status_code", None))
    finally:
        if stream is not None:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
        record.update(
            first_text_ms=None if first is None else round((first-start)*1000),
            total_ms=round((perf_counter()-start)*1000),
            response="".join(text_parts),
        )
        RECORDS.append(record)
        print("\n调用记录：" + json.dumps({k: v for k, v in record.items() if k != "response"}, ensure_ascii=False))
    return record


async def main():
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("请先设置 DEEPSEEK_API_KEY，然后直接运行本文件。")
    # 进程内响应缓存；退出程序后消失，不需要 Redis。
    litellm.cache = Cache(type="local", ttl=300)
    router = make_router(key)
    print(f"路由：{ALIAS} → DeepSeek/{MODEL}；thinking=disabled")
    print("演示输入是固定教学材料；所有模型响应均来自真实调用或此前真实响应的缓存。")
    try:
        first = await call(router, "1-首次请求", messages())
        if first["status"] != "ok":
            print("首次调用失败，停止后续实验。检查模型名、API 权限和网络。")
            return
        # 当前 LiteLLM 的流式缓存写入可能由异步回调完成。
        await asyncio.sleep(1)
        second = await call(router, "2-相同输入检查缓存", messages())
        print("缓存检查：", "命中" if second["cache_hit"] else "未命中，请检查当前 LiteLLM 缓存行为")
        changed = await call(router, "3-版本改变重新调用", messages("after-fix"))
        print("版本变化检查：", "未复用旧缓存" if changed["cache_hit"] is False else "请检查缓存记录")

        # 两个不同请求同时进入只有一个并发名额的部署，不自动重试。
        # 不硬编码结果：不同 LiteLLM 版本可能拒绝或等待，记录实际行为。
        concurrent = make_router(key, parallel=1, retries=0)
        results = await asyncio.gather(*[
            call(concurrent, f"4-并发请求-{i}", messages(question=f"并发实验{i}：用一句话解释会话到期立即失效。"),
                 use_cache=False, print_text=False)
            for i in (1, 2)
        ])
        print("并发实验结果：", [r["status"] for r in results])

        # 独立 Router 的一分钟请求限额为 1，不等待下一分钟，也不重试。
        limited = make_router(key, rpm=1, retries=0)
        for i in (1, 2):
            await call(limited, f"5-限流请求-{i}", messages(question=f"限流实验{i}：只回答 OK。"),
                       use_cache=False, print_text=False)
        print("限流实验以记录为准；若恰好跨越分钟边界，两次可能都通过。")
    finally:
        folder = Path(__file__).resolve().parent / "litellm_runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        folder.mkdir(parents=True, exist_ok=True)
        report = {
            "route": {ALIAS: f"DeepSeek/{MODEL}"},
            "retry_policy": "常规请求对超时、429、5xx最多重试1次；认证和参数错误不重试；实验请求不重试",
            "records": RECORDS,
            "note": "缓存命中的 usage 是原响应记录，不是本次新增消耗；没有第二个模型，因此不配置回退。",
        }
        (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("报告：", folder / "report.json")


if __name__ == "__main__":
    asyncio.run(main())
