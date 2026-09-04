# 流式调用 TTFT / GEN / E2E

## 目标
实现一个真实的 LLM 流式调用。

## 业务约束
- 定义一个 StreamMetrics，包含 started_at / first_event_at / first_token_at / completed_at 四个属性和打点时间的计算方法
- 用 AsyncOpenAI 对 deepseek-v4-flash 发一个 stream=True 请求
遍历 chunk 流时打点:mark_first_event / mark_text_delta(首个非空 delta)/ mark_completed

## 需要修改
- 新增单文件: C:\baidunetdiskdownload\STUDY\Agent\repository\ai-agent-fullstack-training\practice/2_ttft.py(单一 Python 类)
- 除以上外不改动任何代码

## 验收标准
- 输入用户请求，在控制台流式输出LLM返回的结果
- 跑完打印四个数字:first_event_seconds / ttft_seconds / generation_seconds / total_seconds
- git diff 不含上述新增以外的改动
