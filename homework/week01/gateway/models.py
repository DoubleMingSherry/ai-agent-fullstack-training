"""治理层：模型白名单、能力校验、fallback 等价性与定价表。

- 平台对调用方只暴露逻辑模型名，白名单外的名字直接拒绝（unknown_model）。
- fallback 只允许发生在能力等价（能力集合相同）的模型之间，
  目录在构造期就校验声明，保证运行期不会出现跨能力档位的换模型。
- 定价表随白名单一起维护：Cost = 单价 × Token。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import GatewayError


@dataclass(frozen=True)
class ModelSpec:
    """一个白名单内逻辑模型的完整描述。"""

    name: str
    capabilities: frozenset[str]
    upstream_name: str | None = None  # 发给 Provider 的真实模型名，缺省同逻辑名
    input_price: float = 0.0  # 每 1K 输入 token 单价
    output_price: float = 0.0  # 每 1K 输出 token 单价
    fallback: str | None = None  # 声明的能力等价备用模型

    @property
    def upstream(self) -> str:
        return self.upstream_name or self.name

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            input_tokens / 1000 * self.input_price
            + output_tokens / 1000 * self.output_price,
            6,
        )


class ModelCatalog:
    """白名单目录：构造期完成 fallback 声明的等价性校验。"""

    def __init__(self, models: list[ModelSpec]) -> None:
        self._models: dict[str, ModelSpec] = {}
        for spec in models:
            if spec.name in self._models:
                raise ValueError(f"模型重复注册: {spec.name}")
            self._models[spec.name] = spec
        for spec in models:
            if spec.fallback is None:
                continue
            target = self._models.get(spec.fallback)
            if target is None:
                raise ValueError(f"{spec.name} 声明的备用模型不在白名单: {spec.fallback}")
            if target.capabilities != spec.capabilities:
                raise ValueError(
                    f"{spec.name} 与备用模型 {spec.fallback} 能力不等价，"
                    f"fallback 只允许发生在能力等价的模型之间"
                )

    def validate(self, name: str) -> ModelSpec:
        """白名单校验：不在名单内直接 unknown_model，不进入执行层。"""
        spec = self._models.get(name)
        if spec is None:
            raise GatewayError("unknown_model", f"模型不在平台白名单内: {name}")
        return spec

    def chain(self, name: str) -> list[ModelSpec]:
        """执行计划：主模型 + 至多 1 个能力等价备用模型。"""
        primary = self.validate(name)
        chain = [primary]
        if primary.fallback is not None:
            chain.append(self._models[primary.fallback])
        return chain


def default_catalog() -> ModelCatalog:
    """内置白名单：一组能力等价的主备模型 + 一个更高能力档位的模型。"""
    chat_caps = frozenset({"text", "structured"})
    pro_caps = frozenset({"text", "structured", "tools"})
    return ModelCatalog(
        [
            ModelSpec(
                name="chat-lite",
                capabilities=chat_caps,
                input_price=0.001,
                output_price=0.002,
                fallback="chat-lite-backup",
            ),
            ModelSpec(
                name="chat-lite-backup",
                capabilities=chat_caps,
                input_price=0.0011,
                output_price=0.0022,
            ),
            ModelSpec(
                name="chat-pro",
                capabilities=pro_caps,
                input_price=0.01,
                output_price=0.03,
            ),
        ]
    )
