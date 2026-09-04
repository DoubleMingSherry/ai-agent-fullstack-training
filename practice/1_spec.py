"""
Agent Loop Demo（任务级 Spec 实现）。

流程：
1. 用户输入进入上下文
2. 调 deepseek-v4-flash 判断：直接回答，还是需要调用工具（返回结构化 JSON）
3. 需要工具时执行模拟工具（固定返回值），把结果回填上下文
4. 循环，直到模型给出最终答案
5. 每一步打印：调用了哪个工具、输入是什么、输出是什么

运行：先设置环境变量 DEEPSEEK_API_KEY，然后 `python practice/1_spec.py`。
"""

import json
import os
import re

from openai import OpenAI


class AgentLoop:
    """一个最小可运行的 Agent Loop：LLM 决策 + 模拟工具执行 + 结果回填。"""

    # 这段系统提示词就是“工具调用规则”：让模型输出结构化 JSON 供 loop 解析。
    SYSTEM = (
        "你是会使用工具的助手。你只能输出一个 JSON 对象，不要输出其他内容。\n"
        "可用工具：\n"
        '1. get_date：查询当前日期/星期几，参数 {}；'
        '调用时输出 {"type":"tool_call","name":"get_date","arguments":{}}\n'
        '2. get_weather：查询某个城市当天的天气，参数 {"city":"城市名"}；'
        '调用时输出 {"type":"tool_call","name":"get_weather","arguments":{"city":"城市名"}}\n'
        "如果需要多个信息，每轮只输出一个 tool_call，拿到结果后再决定下一步。\n"
        "当所有需要的信息都拿到后，输出最终回答："
        '{"type":"final_answer","content":"自然语言回答"}\n'
        "最终回答要简洁，格式参考：“今天星期X，{城市}天气{天气}，气温{温度}。”，不要加多余的词。"
    )

    MODEL = "deepseek-v4-flash"

    def _to_llm(self, messages: list[dict]) -> list[dict]:
        """把内部消息格式转成 LLM 输入格式（toolResult 转成 user 角色）。"""
        out = []
        for message in messages:
            if message["role"] == "toolResult":
                out.append(
                    {
                        "role": "user",
                        "content": f"工具返回结果: {message['content']}",
                    }
                )
            else:
                out.append(message)
        return out

    def __init__(self, api_key: str | None = None):
        # 沿用 OpenAI SDK 的兼容调用方式访问 DeepSeek。
        self.client = OpenAI(
            api_key=api_key or os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )

    # ---------- 模拟工具（不调用真实 API，固定返回） ----------

    def tool_get_date(self, args: dict) -> dict:
        """模拟查询当前日期，固定返回星期六。"""
        return {"weekday": "星期六", "date": "2026-08-29"}

    def tool_get_weather(self, args: dict) -> dict:
        """模拟查询气象台，固定返回晴朗 24~30 度。"""
        city = args.get("city", "北京")
        return {"city": city, "weather": "晴朗", "temperature": "24~30度"}

    TOOLS = {
        "get_date": tool_get_date,
        "get_weather": tool_get_weather,
    }

    # ---------- LLM 决策：返回结构化数据 ----------

    def llm_decide(self, messages: list[dict]) -> dict:
        """
        调 deepseek-v4-flash，让模型根据上下文决定当前这一步做什么。

        返回结构化数据（供后续 loop 使用）：
        {"type":"tool_call","name":...,"arguments":{...}}
        或 {"type":"final_answer","content":"..."}
        """
        response = self.client.chat.completions.create(
            model=self.MODEL,
            messages=[{"role": "system", "content": self.SYSTEM}, *self._to_llm(messages)],
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = response.choices[0].message.content or ""

        # 从模型输出里提取 JSON；解析失败时按最终回答兜底。
        try:
            match = re.search(r"\{.*\}", raw, re.S)
            payload = json.loads(match.group(0)) if match else {}
        except Exception:
            payload = {}
        if payload.get("type") in ("tool_call", "final_answer"):
            return payload
        return {"type": "final_answer", "content": raw}

    # ---------- 主循环 ----------

    def run(self, user_input: str) -> dict:
        """
        执行 Agent Loop，返回结构化结果。

        每一步的执行结果（调用了哪个工具、输入、输出）会打印到终端，
        并记录在返回值的 steps 里。
        """
        messages: list[dict] = [{"role": "user", "content": user_input}]
        steps: list[dict] = []

        print(f"[user] {user_input}")

        while True:
            decision = self.llm_decide(messages)
            print(f"[llm_decision] {json.dumps(decision, ensure_ascii=False)}")

            if decision["type"] == "final_answer":
                final_answer = decision["content"]
                break

            # 执行工具，并记录这一步的输入输出。
            name = decision["name"]
            arguments = decision.get("arguments", {})
            if name not in self.TOOLS:
                raise ValueError(f"未知工具: {name}")

            handler = self.TOOLS[name]
            result = handler(self, arguments)

            print(f"[tool_start] {name} 输入: {json.dumps(arguments, ensure_ascii=False)}")
            print(f"[tool_end] {name} 输出: {json.dumps(result, ensure_ascii=False)}")

            steps.append({"tool": name, "input": arguments, "output": result})
            messages.append(
                {
                    "role": "toolResult",
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        print(f"[final_answer] {final_answer}")
        return {
            "final_answer": final_answer,
            "steps": steps,
            "messages": messages,
        }


if __name__ == "__main__":
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("请先设置 DEEPSEEK_API_KEY")

    loop = AgentLoop()
    loop.run("今天是星期几？北京的天气如何？")
