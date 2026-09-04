"""出口校验层：流式 JSON 增量解析。

Structured Streaming 边界：不等全部 chunk 到齐，逐块扫描已有前缀，
报告已完整解析出的顶层键；部分 JSON 无法做 Schema 校验，
流结束后才做最终校验（structured.validate_structured）。
"""

from __future__ import annotations

import json
from typing import Any

_INCOMPLETE = object()
_SKIP_CHARS = " \t\r\n,:{"


class IncrementalJsonParser:
    """对一个逐块到达的顶层 JSON object 做增量解析。

    ``feed`` 返回本次新完成的顶层键值对（值完整时才交付）：
    - 字符串值在闭引号处交付；对象/数组值在括号平衡处交付；
    - 数字标量必须等到终结符（, 或 }）才交付，避免把前缀当完整值；
    - true/false/null 等字面量按定长匹配交付。
    任何前缀都无法保证整体合法，最终合法性仍以流结束后的双层校验为准。
    """

    def __init__(self) -> None:
        self._buf = ""
        self._pos = 0
        self._done_keys: set[str] = set()
        self.complete = False  # 顶层 '}' 已出现

    def feed(self, delta: str) -> list[tuple[str, Any]]:
        self._buf += delta
        fresh: list[tuple[str, Any]] = []
        while True:
            i = self._pos
            while i < len(self._buf) and self._buf[i] in _SKIP_CHARS:
                i += 1
            if i >= len(self._buf):
                self._pos = i
                break
            if self._buf[i] == "}":
                self.complete = True
                self._pos = i + 1
                break
            if self._buf[i] != '"':
                self._pos = i + 1  # 非预期字符：跳过，等待流结束后的最终校验
                continue
            token_start = i  # 当前 "键:值" 候选的起点；不完整时退回这里重扫
            key, after_key = self._scan_string(i)
            if key is None:
                self._pos = token_start
                break  # 键字符串尚未完整，等下一个 chunk
            j = self._skip_ws(after_key)
            if j >= len(self._buf) or self._buf[j] != ":":
                self._pos = token_start
                break
            j = self._skip_ws(j + 1)
            if j >= len(self._buf):
                self._pos = token_start
                break
            value, after_value = self._scan_value(j)
            if value is _INCOMPLETE:
                self._pos = token_start
                break
            if key not in self._done_keys:
                self._done_keys.add(key)
                fresh.append((key, value))
            self._pos = after_value
        return fresh

    def _skip_ws(self, i: int) -> int:
        buf = self._buf
        while i < len(buf) and buf[i] in " \t\r\n":
            i += 1
        return i

    def _scan_string(self, start: int) -> tuple[str | None, int]:
        """扫描完整 JSON 字符串，返回 (解码值, 闭引号后位置)；不完整返回 (None, start)。"""
        i = start + 1
        escape = False
        while i < len(self._buf):
            ch = self._buf[i]
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                try:
                    return json.loads(self._buf[start : i + 1]), i + 1
                except json.JSONDecodeError:
                    return None, i + 1
            i += 1
        return None, start

    def _scan_value(self, start: int) -> tuple[Any, int]:
        """从 start 扫描一个完整 JSON 值；前缀不完整返回 (_INCOMPLETE, start)。"""
        buf = self._buf
        ch = buf[start]
        if ch == '"':
            return self._scan_string(start)
        if ch in "{[":
            end = self._match_bracket(start)
            if end is None:
                return _INCOMPLETE, start
            try:
                return json.loads(buf[start : end + 1]), end + 1
            except json.JSONDecodeError:
                return _INCOMPLETE, start
        for literal in ("true", "false", "null"):
            end = start + len(literal)
            if buf[start:end] == literal:
                return {"true": True, "false": False, "null": None}[literal], end
        # 数字：必须等到 , 或 } 才算完整
        j = start
        while j < len(buf) and buf[j] not in ",}":
            j += 1
        if j >= len(buf):
            return _INCOMPLETE, start
        try:
            return json.loads(buf[start:j].strip()), j
        except json.JSONDecodeError:
            return _INCOMPLETE, start

    def _match_bracket(self, start: int) -> int | None:
        """返回与 buf[start] 的 {/[ 匹配的闭括号位置（跳过字符串字面量）。"""
        buf = self._buf
        close_ch = "}" if buf[start] == "{" else "]"
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(buf)):
            ch = buf[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0 and ch == close_ch:
                    return i
        return None
