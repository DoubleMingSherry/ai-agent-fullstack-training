# Adapter + SSE Encoder

## 目标
实现一个 Mini Adapter + SSE Encoder，并模拟chunk进行测试验证。

## 业务约束
- 定义一个 Adapter，不得引入openai，而是接收一个 chunk 迭代器作为参数
- 伪造 chunk 只需要一个带 choices[0].delta.content / finish_reason / usage 属性的对象，不需要真正调用大模型
- Adapter 解析/归一化chunk，产生内部事件(Harness 事件); 其唯一代码表示为 dict,必须含 type 与 seq 字段
- 定义一个 Encoder ，将 Adapter 归一化的输出转换成 SSE bytes
- Encoder 需保证 1.中文 Unicode 往返不丢字； 2.换行符(\n 与 \r\n)在 payload 内部正确转义，不得破坏帧结构； 3.引号/反斜杠 不破坏 SSE 结构； 4.空字符串 delta 不漂移 seq； 5.大 Payload > 64 KB 单事件

## 需要修改
- 新增单文件: C:\baidunetdiskdownload\STUDY\Agent\repository\ai-agent-fullstack-training\practice\3_adapter.py
- Adapter 和 Encoder 必须是两个互相独立的定义
- 除以上外不改动任何代码

## 验收标准
- 输入 5 类伪造 chunk（role-only 空 delta / 带 delta 文本 / finish_reason / usage / 非法空 chunk）,输出对应的内部事件（Harness 事件，dict 表示）：type 必须在 {"text.delta","model.finished","model.usage"} 内，且每个事件带递增 seq （1.role-only 和 空chunk：不产生任何事件，seq不递增； 2.text.delta：内容逐字保留，空字符串 delta 不得消耗seq； 3.finish_reason="stop"： model.finished； 4.usage 非空：model.usage）
- encode_sse(event) -> bytes：输出符合 id/event/data 三行 + 空行结尾
  的帧；data 是合法 JSON
- 心跳场景：encode_heartbeat() 输出以 ": " 开头的注释行,且解析器
  （或肉眼）确认它不构成事件
- 全部用断言/pytest 验证,运行过程不发起任何网络请求
- 如果发现"想测就得先发请求",说明 HTTP 调用混进了 Adapter，证明设计有误
- git diff 不含上述新增以外的改动
