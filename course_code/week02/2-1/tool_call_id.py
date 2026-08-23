{
  "role": "assistant",
  "tool_calls": [
    {
      "id": "call_A",
      "function": {
        "name": "get_order",
        "arguments": "{\"order_id\":\"ord_123\"}"
      }
    },
    {
      "id": "call_B",
      "function": {
        "name": "get_order",
        "arguments": "{\"order_id\":\"ord_456\"}"
      }
    }
  ]
}



########
########
########
{
  "role": "tool",
  "tool_call_id": "call_B",
  "content": "{\"order_id\":\"ord_456\",\"status\":\"paid\"}"
}

{
  "role": "tool",
  "tool_call_id": "call_A",
  "content": "{\"error\":{\"code\":\"TIMEOUT\"}}"
}
