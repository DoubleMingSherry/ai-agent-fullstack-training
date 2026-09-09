我发现gateway中缺少对上下文超限导致的被截断的场景的判断，现在需要对代码做出修改，要求如下：
1. 两个适配器捕获停止原因：OpenAI Responses（incomplete_details / status）与 Anthropic Messages（stop_reason == "max_tokens"）各自归一化成统一标记，随 ProviderResult / ProviderDone 带出适配器（红线不变：原生对象不出门，带出来的仍是统一类型）。
2. Trace 记录截断状态：CallTrace 增加 truncated 字段，成功与失败调用都要落。
3. 错误分类：当请求带 json_schema 且停止原因是"长度截断"时，不要报 schema_validation_failed，改报新错误码 output_truncated（errors.py 注册，5xx 类），错误信息里写明三个处方：缩短上下文 / 提高 max_tokens / 拆小任务。
4. 测试：FakeProvider 加一个"模拟截断"脚本步骤；断言 (a) trace 里 truncated=True，(b) 带 Schema 的截断请求得到 output_truncated 而不是 schema_validation_failed，(c) 无 Schema 的截断不报错、正常返回。
5. 入站上下文预算守卫：用字符数估算 token（中文≈1 字 1 token，英文≈4 字符 1 token），超过配置预算直接 invalid_request（400）拒绝。校验前置，失败发生在正确的位置。
6. 完成后：pytest 全绿 + git diff 仅修改homework/week01_v2下的内容。