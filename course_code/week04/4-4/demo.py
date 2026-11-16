from pathlib import Path

# 跳过 NLTK 下载，避免网络问题导致的示例变慢或失败
import os
os.environ["LLAMA_INDEX_DISABLE_NLTK"] = "1"

# 消除 WARNING 警告信息，避免干扰阅读；生产环境按需设置日志级别
import logging
logging.basicConfig(level=logging.ERROR)

from llama_index.core import Document, VectorStoreIndex

# 复用项目里已经接好的 MiniMax embedding 与 LLM
from rag.minimax import MiniMaxEmbedding, build_llm

print("正在解析文件...")
# 直接读取 corpus/docs 下的 Markdown 文档，加载为 Document 对象
docs_dir = Path(__file__).resolve().parent / "corpus" / "docs"
documents = [
    Document(
        text=path.read_text(encoding="utf-8"),
        metadata={"file_path": str(path.relative_to(docs_dir.parent.parent))},
    )
    for path in sorted(docs_dir.glob("*.md"))
]

print("正在创建索引...")
# from_documents 方法包含切片与建立索引步骤；embedding 用 MiniMax embo-01
index = VectorStoreIndex.from_documents(
    documents,
    embed_model=MiniMaxEmbedding(model_name="embo-01"),
)

print("正在创建提问引擎...")
query_engine = index.as_query_engine(
    # 设置为流式输出
    streaming=True,
    # LLM 用 MiniMax-Text-01（OpenAI 形状接口，api_base / key 已在 build_llm 里配好）
    llm=build_llm(),
)

print("正在生成回复...")
streaming_response = query_engine.query("登录会话的有效期是多久？到期边界应该怎么判定？")
print("回答是：")
# 采用流式输出
streaming_response.print_response_stream()
print()
