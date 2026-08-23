import os

from openai import OpenAI

from search_orders_tool import SEARCH_ORDERS


client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)

messages = [
    {
        "role": "system",
        "content": (
            "你是订单助手。需要实时订单数据时调用工具；"
            "不要编造订单，修改订单前必须先获得明确目标。"
        ),
    },
    {
        "role": "user",
        "content": "查一下我昨天创建、还没有支付的订单。",
    },
]

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    tools=[SEARCH_ORDERS.to_model_tool()],
    tool_choice="auto",
    extra_body={"thinking": {"type": "disabled"}},
)

assistant = response.choices[0].message

for call in assistant.tool_calls or []:
    print(call.id)
    print(call.function.name)
    print(call.function.arguments)
