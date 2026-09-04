"""
流式调用 TTFT / GEN / E2E（任务级 Spec 实现）。

流程：
1. 用 AsyncOpenAI 对 deepseek-v4-flash 发一个 stream=True 请求
2. 遍历 chunk 流打点：mark_first_event / mark_text_delta（首个非空 delta）/ mark_completed
3. 控制台流式输出 LLM 返回的结果
4. 跑完打印四个数字：first_event_seconds / ttft_seconds / generation_seconds / total_seconds

运行：先设置环境变量 DEEPSEEK_API_KEY，然后 `python practice/2_ttft.py`。
"""

import asyncio
import os
import time

from openai import AsyncOpenAI


class StreamMetrics:
    """流式调用的打点器：记录关键时间点并计算各阶段耗时。"""

    def __init__(self):
        self.started_at: float | None = None      # 发起请求时刻
        self.first_event_at: float | None = None  # 收到第一个 chunk 的时刻
        self.first_token_at: float | None = None  # 收到首个非空文本 delta 的时刻
        self.completed_at: float | None = None    # 流结束时刻

    # ---------- 打点方法 ----------

    def mark_started(self) -> None:
        """发起请求时打点。"""
        self.started_at = time.perf_counter()

    def mark_first_event(self) -> None:
        """收到第一个 chunk（任意事件）时打点，只记第一次。"""
        if self.first_event_at is None:
            self.first_event_at = time.perf_counter()

    def mark_text_delta(self) -> None:
        """收到首个非空文本 delta 时打点，只记第一次。"""
        if self.first_token_at is None:
            self.first_token_at = time.perf_counter()

    def mark_completed(self) -> None:
        """流结束时打点。"""
        self.completed_at = time.perf_counter()

    # ---------- 耗时计算 ----------

    def first_event_seconds(self) -> float | None:
        """首事件耗时：从发请求到收到第一个 chunk。"""
        if self.started_at is None or self.first_event_at is None:
            return None
        return self.first_event_at - self.started_at

    def ttft_seconds(self) -> float | None:
        """TTFT（Time To First Token）：从发请求到收到首个非空文本 delta。"""
        if self.started_at is None or self.first_token_at is None:
            return None
        return self.first_token_at - self.started_at

    def generation_seconds(self) -> float | None:
        """生成耗时：从首 token 到流结束。"""
        if self.first_token_at is None or self.completed_at is None:
            return None
        return self.completed_at - self.first_token_at

    def total_seconds(self) -> float | None:
        """端到端耗时：从发请求到流结束。"""
        if self.started_at is None or self.completed_at is None:
            return None
        return self.completed_at - self.started_at


class StreamRunner:
    """发起一次真实的流式 LLM 调用，边收边打印，并收集 StreamMetrics。"""

    MODEL = "deepseek-v4-flash"

    def __init__(self, api_key: str | None = None):
        # 沿用 OpenAI SDK 的兼容调用方式访问 DeepSeek。
        self.client = AsyncOpenAI(
            api_key=api_key or os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )

    async def run(self, user_input: str) -> StreamMetrics:
        """流式调用 deepseek-v4-flash，控制台实时输出，返回打点结果。"""
        metrics = StreamMetrics()
        metrics.mark_started()

        stream = await self.client.chat.completions.create(
            model=self.MODEL,
            messages=[{"role": "user", "content": user_input}],
            stream=True,
            extra_body={"thinking": {"type": "disabled"}},
        )

        async for chunk in stream:
            # 任意 chunk 到达即记首事件。
            metrics.mark_first_event()

            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                # 首个非空文本 delta 记 TTFT。
                metrics.mark_text_delta()
                print(delta, end="", flush=True)

        metrics.mark_completed()
        print()  # 收尾换行
        return metrics


async def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("请先设置 DEEPSEEK_API_KEY")

    runner = StreamRunner()
    metrics = await runner.run("用三句话介绍一下流式输出的好处。")

    print(f"first_event_seconds: {metrics.first_event_seconds():.4f}")
    print(f"ttft_seconds: {metrics.ttft_seconds():.4f}")
    print(f"generation_seconds: {metrics.generation_seconds():.4f}")
    print(f"total_seconds: {metrics.total_seconds():.4f}")


if __name__ == "__main__":
    asyncio.run(main())
