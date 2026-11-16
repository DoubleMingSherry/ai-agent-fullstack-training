# 4-6 RAG 服务（LlamaIndex + BGE + BM25 混合检索）

参考 4-4 的实现，拆成两个独立脚本：入库（文档 → 向量库）和查询（带参数调用 RAG）。
向量库用 LlamaIndex `SimpleVectorStore`（JSON 落盘，零服务依赖），BM25 语料节点
pickle 存在同一目录，两边共用同一批分块节点。

## 结构

```
rag/
├── ingest.py          # 第一部分：文档 → BGE 向量库 + BM25 索引
├── query.py           # 第二部分：带参数查询，混合检索 + LLM 生成
├── requirements.txt
├── corpus/            # 语料（已从 4-4 迁入，本目录自包含）
├── storage/           # 入库产物（vector_store、docstore、bm25_nodes.pkl）
└── rag/
    ├── config.py      # 路径 / 模型 / MiniMax key 读取
    ├── docload.py     # md/py/html/csv/pdf 解析 → Document（沿用 4-4）
    ├── local_embed.py # BAAI/bge-small-zh-v1.5（fastembed，512 维）
    ├── store.py       # 分块 + SimpleVectorStore 落盘 / 加载
    ├── hybrid.py      # BM25（jieba 分词）+ RRF 融合
    ├── answer.py      # 带编号证据的上下文装配与提示词
    └── llm.py         # MiniMax chat（OpenAI 形状，沿用 4-4 的绕坑写法）
```

## 用法

```bash
pip install -r requirements.txt

# 入库：默认吃本目录的 corpus/，写 storage/
python ingest.py
python ingest.py --corpus ./corpus --store ./storage --overwrite --chunk-size 384   

# 查询：默认 hybrid（BGE 向量 + BM25 各召回 20 条，RRF 融合取 top_k）
python query.py -q "会话的有效期是多少分钟？"
python query.py -q "SessionExpiryError 在哪" --mode bm25 --top-k 3
python query.py -q "..." --mode vector            # 只用向量
python query.py -q "..." --weights 0.7,0.3        # RRF 加权（向量,BM25）
python query.py -q "..." --doc-type code          # 元数据过滤
python query.py -q "..." --no-llm                 # 只看召回，不调模型
python query.py -q "..." --show-evidence          # 打印证据全文
```

生成侧 LLM 沿用 MiniMax（key 读 `~/.hermes/.env` 的 `MINIMAX_CN_API_KEY`）；
没有 key 时用 `--no-llm`，检索链路不受影响。
