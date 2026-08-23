from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


Dispose = Callable[[], None]


@dataclass(frozen=True)
class PluginContext:
    tools: ToolRuntime

    def register_tool(self, tool: ToolDefinition) -> Dispose:
        return self.tools.register(tool)


class ToolPlugin(Protocol):
    name: str
    requires: frozenset[str]

    def apply(self, ctx: PluginContext) -> list[Dispose]: ...


class OrderToolsPlugin:
    name = "order-tools"
    requires = frozenset({"tools"})

    def apply(self, ctx: PluginContext) -> list[Dispose]:
        return [ctx.register_tool(SEARCH_ORDERS)]


class PluginManager:
    def __init__(self, ctx: PluginContext):
        self._ctx = ctx
        self._loaded: dict[str, list[Dispose]] = {}

    def load(self, plugin: ToolPlugin) -> None:
        available = frozenset({"tools"})
        missing = plugin.requires - available
        if missing:
            raise RuntimeError(f"missing services: {sorted(missing)}")
        if plugin.name in self._loaded:
            raise ValueError(f"duplicate plugin: {plugin.name}")
        self._loaded[plugin.name] = plugin.apply(self._ctx)

    def unload(self, plugin_name: str) -> None:
        disposers = self._loaded.pop(plugin_name, [])
        for dispose in reversed(disposers):
            dispose()


# 设计一个最小的 Agent Loop ，支持插件的加载和卸载
# 1 工具的定义必须与 Agent Loop 分离， 新增工具不修改循环
# 2 插件通过 service 获取能力， 不直接访问 Harness 内部实现
# 3 注册行为必须可撤销， 谁注册谁负责清理
# 4 权限、sandbox、超时、 Trace 通过稳定扩展点覆盖多类工具
