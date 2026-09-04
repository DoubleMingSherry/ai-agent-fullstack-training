"""Mini LLM Gateway：统一入口访问不同模型的迷你网关。

五层结构（见 my_gateway.md）：
1. 接口层   protocol.py + app.py（FastAPI 入口、Pydantic 协议、统一错误 envelope）
2. 治理层   models.py / prompts.py / ratelimit.py（白名单、模板治理、限流）
3. 执行层   provider.py / executor.py（Provider Adapter、重试/fallback、流式转发）
4. 出口校验层 structured.py / streamjson.py（双层校验、修复边界）
5. 可观测层 trace.py（CallTrace 与 /trace 查询）
"""

__version__ = "0.1.0"
