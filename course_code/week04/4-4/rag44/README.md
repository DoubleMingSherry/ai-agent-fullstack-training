# 4.4 RAG 教学代码

Python 3.11/3.12。资料是教学夹具，不代表读取了你的实际仓库。
主链路由 LlamaIndex 分块与检索，Milvus 存储向量，OpenAI-compatible Gateway
提供真实 Embedding 和 DeepSeek 流式生成；没有 Fake Provider。

## 安装与配置

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

配置下列环境变量；不要把真实密钥提交到代码仓库：

| 变量 | 含义 |
| --- | --- |
| LLM_GATEWAY_URL | 前几节的 LiteLLM 网关地址，带 /v1 |
| LLM_GATEWAY_KEY | 网关密钥 |
| LLM_MODEL | 默认 agent-default，须实际路由至课程指定的 deepseek-v4-flash |
| EMBED_GATEWAY_URL | 提供 /embeddings 的 OpenAI-compatible 地址，带 /v1 |
| EMBED_GATEWAY_KEY | Embedding 服务密钥 |
| EMBED_MODEL | 实际部署的 Embedding 模型/网关别名，必须同时适合文档和查询 |
| MILVUS_URI | 默认脚本目录的 milvus.db；服务器可设 http://localhost:19530 |
| MILVUS_TOKEN | 服务器需要认证时设置，Lite 可省略 |

生成模型与 Embedding 模型是两个接口，不得把 DeepSeek 聊天模型名称直接当作 Embedding 模型。
思考关闭参数沿用课程 `extra_body={"thinking":{"type":"disabled"}}`；网关必须支持并透传。

## 按顺序运行

```bash
python rag.py --build
python rag.py --retrieve-only
python rag.py
python rag.py --mode hybrid --retrieve-only
python run_questions.py
python evaluate.py
# 要比较答案时，实际调用两种策略下的生成模型：
python run_questions.py --answer
```

`traces.jsonl` 记录分块 ID、最终证据、问题、策略、阶段时延及模型实际返回的 usage。
检索事件和答案事件用 run_id 配对。Embedding usage 单独记录，正式项目应补 request_id/run_id
并与网关 Trace 对账。费用未知时 cost 为 null；不可据此宣布零成本。
Embedding 客户端对可重试错误沿用 SDK 的两次退避重试；流式生成禁自动重试，避免重放。

## 评估标注

`evaluate.py` 的 GOLD 按标题定位正确证据，只适合本教学资料。
从检索/答案 Trace 复制实际 run_id，在同目录创建 `labels.json`：

```json
{
  "替换成真实run_id": {
    "answer_correct": true,
    "factual_claims": 3,
    "unsupported_claims": 0
  }
}
```

以上数字只是标注格式示例，不是已测结果。逐句对照证据后填写。
没有人工标注时答案正确率和无依据断言率返回 null。
无答案问题不进入正例召回率分母；仍进入答案验收，正确行为是明确缺少证据。
用于正式验收的问题集应从真实需求采样，固定文档版本、分块策略和正确证据标注。
改变分块后，要重新映射 gold，不能拿旧 node_id 计算新方案的召回率。

## 存储与边界

- collection 指纹由模型名称、实际维度和分块 ID 组成；改资料/模型名称后使用新 collection。
- 同名 Embedding 别名背后模型变更也需要更新别名或数据库命名空间。
- 建库不 overwrite 已有 collection，检测到已有数据时拒绝重复写入。
- 如果写入中断形成部分数据，先检查该 collection 的记录，再显式清理这个教学 collection 后重建；不可当成完整索引使用。
- 本地 BM25 演示每次按同一范围重建索引，便于教学；生产应持久化并与向量索引同步发布。
- 3500 字符预算是教学简化；正式上下文预算必须按生成模型 tokenizer 计算。
- citation_ids_valid 只检查编号存在，semantic_verification=not_run 表示还没有验证语义支持。
- retrieval.latency_s 只包含检索与筛选；answer.latency_s 包含生成，ttfb_s 是生成请求到首个文本。
  CLI 启动、维度探测、载入模型、Embedding 与 Rerank 阶段要分别统计，再测整条请求的总延迟。
- 示例未接入自动 Cross Encoder、查询改写或生产 ACL；稿件解释这些改进的适用条件。
- 示例不执行补丁、不运行业务测试，资料中的命令不是测试通过回执。

## 本次验证

已验证 Python 编译、6 个有效 Markdown 分块、BM25 本地召回、证据筛选、Milvus Lite 连接、写入读取往返和项目元数据过滤。
真实 Embedding、DeepSeek 网关流式回答和实际质量指标：未联调，缺少环境配置。
