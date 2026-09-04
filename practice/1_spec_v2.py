"""
Agent Loop Demo（任务级 Spec v2 实现）。

在 v1 基础上增强可观测性与健壮性：
1. 用户输入进入上下文
2. 调 deepseek-v4-flash，模型只允许输出两种结构化 JSON：
   {"type":"tool_call",...} 或 {"type":"final_answer",...}
   代码只按 type 字段分支，不从自然语言里猜意图
3. 解析失败时打印失败日志与原始输出，重试 1 次；仍失败则报错终止
4. tool_call 则执行模拟工具（固定返回），把结果回填上下文，进入下一轮
5. 循环有步数上限 max_steps=5，超限报“步数超限”并终止，不会死循环
6. 每一步打印：决策内容、调用了哪个工具、输入、输出

运行：先设置环境变量 DEEPSEEK_API_KEY，然后 `python practice/1_spec_v2.py`。
"""

import json
import os
import re

from openai import OpenAI


class AgentLoopV2:
    """一个最小可运行的 Agent Loop：结构化决策 + 模拟工具 + 步数/解析兜底。"""

    # 这段系统提示词就是“结构化协议”：模型只能输出两种 JSON 之一。
    SYSTEM = (
        "你是会使用工具的助手。你只能输出一个 JSON 对象，不要输出任何其他内容。\n"
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
    MAX_STEPS = 5
    PARSE_ATTEMPTS = 2  # 首次 + 重试 1 次

    def __init__(self, api_key: str | None = None, max_steps: int = MAX_STEPS):
        # 沿用 OpenAI SDK 的兼容调用方式访问 DeepSeek。
        self.client = OpenAI(
            api_key=api_key or os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )
        self.max_steps = max_steps

    # ---------- 消息格式转换 ----------

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

    # ---------- 模拟工具（不调用任何真实外部 API，固定返回） ----------

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

    # ---------- LLM 决策：返回原始输出，由 _parse_decision 做结构化校验 ----------

    def llm_decide(self, messages: list[dict]) -> str:
        """
        调 deepseek-v4-flash，返回模型的原始输出文本。

        这里是“模型真正做决策”的地方；是否合法的结构化决策
        由 _parse_decision 按协议校验，代码只按 type 字段分支。
        """
        response = self.client.chat.completions.create(
            model=self.MODEL,
            messages=[{"role": "system", "content": self.SYSTEM}, *self._to_llm(messages)],
            extra_body={"thinking": {"type": "disabled"}},
        )
        return response.choices[0].message.content or ""

    def _parse_decision(self, raw: str) -> dict | None:
        """
        把模型原始输出解析成结构化决策。

        只接受协议里的两种形态，其余一律视为解析失败（返回 None）：
        - {"type":"tool_call","name":...,"arguments":{...}}
        - {"type":"final_answer","content":"..."}
        绝不把原始文本当作最终答案兜底。
        """
        try:
            match = re.search(r"\{.*\}", raw, re.S)
            payload = json.loads(match.group(0)) if match else None
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None
        if payload.get("type") == "tool_call":
            if isinstance(payload.get("name"), str) and isinstance(payload.get("arguments", {}), dict):
                return payload
            return None
        if payload.get("type") == "final_answer":
            if isinstance(payload.get("content"), str):
                return payload
            return None
        return None

    # ---------- 主循环 ----------

    def run(self, user_input: str) -> dict:
        """
        执行 Agent Loop，返回结构化结果。

        每一步的执行结果（决策内容、调用了哪个工具、输入、输出）会
        打印到终端，并记录在返回值的 steps 里。
        """
        messages: list[dict] = [{"role": "user", "content": user_input}]
        steps: list[dict] = []
        final_answer = None

        print(f"[user] {user_input}")

        for step in range(1, self.max_steps + 1):
            print(f"[step {step}/{self.max_steps}]")

            # Step 1. 调 LLM 拿决策；解析失败打印日志并重试 1 次，仍失败则报错终止。
            decision = None
            for attempt in range(1, self.PARSE_ATTEMPTS + 1):
                raw = self.llm_decide(messages)
                decision = self._parse_decision(raw)
                if decision is not None:
                    break
                print(f"[parse_error] 第 {attempt} 次解析失败，原始输出: {raw}")
            if decision is None:
                raise RuntimeError("模型输出连续解析失败，终止运行")

            print(f"[llm_decision] {json.dumps(decision, ensure_ascii=False)}")

            # Step 2. 只按 type 字段分支。
            if decision["type"] == "final_answer":
                final_answer = decision["content"]
                break

            # Step 3. 执行工具，并记录这一步的输入输出。
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
        else:
            raise RuntimeError(f"步数超限: 已达最大步数 {self.max_steps}，终止运行")

        print(f"[final_answer] {final_answer}")
        return {
            "final_answer": final_answer,
            "steps": steps,
            "messages": messages,
        }


if __name__ == "__main__":
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("请先设置 DEEPSEEK_API_KEY")

    loop = AgentLoopV2()
    loop.run("今天是星期几？北京的天气如何？")
