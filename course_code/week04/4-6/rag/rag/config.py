"""公共配置：路径、模型、存储位置。

参考 4-4：embedding 用本地 BGE（fastembed，ONNX），
向量库用 LlamaIndex SimpleVectorStore（落盘为 JSON，无需 Milvus 服务），
BM25 索引用 pickle 存在同一个存储目录里。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # 4-6/rag

# 默认语料：本目录内的 corpus/（已从 4-4 迁入，不依赖外部目录）
DEFAULT_CORPUS = ROOT / "corpus"

# 持久化目录：vector_store.json + bm25_nodes.pkl
DEFAULT_STORE = ROOT / "storage"

# BGE 中文检索模型（查询侧指令前缀由 fastembed 自动处理）
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
EMBED_DIM = 512

# 生成侧 LLM：沿用 4-4 的 MiniMax（chat 接口是 OpenAI 形状的）
LLM_MODEL = "MiniMax-Text-01"
API_BASE = "https://api.minimaxi.com/v1"


def api_key() -> str:
    """从 ~/.hermes/.env 读取 MiniMax key（与 4-4 一致）。"""
    env = Path.home() / ".hermes" / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("MINIMAX_CN_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("MINIMAX_CN_API_KEY not found in ~/.hermes/.env")
