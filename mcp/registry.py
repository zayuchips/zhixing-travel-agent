# -*- coding: utf-8 -*-
"""工具注册表：MCP 的 tools/list 与 tools/call 都落到这里。"""
import json
import os
import time
import traceback

from . import protocol

# 同一轮里模型偶尔会对同一工具、同一参数连调两次（常见是 itinerary）。
# 短时缓存让第二次直接返回上次结果，既省外部接口调用，也把「别再调了」写进结果文本。
_CACHE = {}
_CACHE_TTL = float(os.environ.get("TOOL_CACHE_TTL", "60"))


class ToolError(Exception):
    """工具内部可预期的失败（参数不对、查不到城市），会以 isError=True 返回。"""


class Tool(object):
    def __init__(self, name, description, input_schema, handler):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler

    def to_mcp(self):
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


_REGISTRY = {}
_CALL_LOG = []


def register(name, description, input_schema):
    def deco(fn):
        _REGISTRY[name] = Tool(name, description, input_schema, fn)
        return fn
    return deco


def list_tools():
    return [_REGISTRY[k].to_mcp() for k in sorted(_REGISTRY)]


def _cache_key(name, args):
    return name + "|" + json.dumps(args or {}, sort_keys=True,
                                   ensure_ascii=False, default=str)


def _cached_result(result):
    """浅拷贝一份，在给模型看的文本前加一句提示。structuredContent 保持原样。"""
    out = dict(result)
    out["content"] = [dict(c) for c in (result.get("content") or [])]
    if out["content"] and out["content"][0].get("type") == "text":
        out["content"][0]["text"] = (
            "〔本轮已经用相同参数查过，结果与上次完全相同，请直接使用，不要重复调用这个工具〕\n"
            + out["content"][0]["text"])
    return out


def call_tool(name, arguments, trace=None):
    """调用工具，返回 (mcp_result, 耗时秒)。

    统一在这里做：参数存在性校验、短时结果缓存、异常兜底、调用轨迹记录。
    轨迹会原样返回给前端「思考过程」面板，这是本项目「可解释」的基础。
    """
    started = time.time()
    tool = _REGISTRY.get(name)
    if tool is None:
        return protocol.tool_result("未知工具：%s" % name, is_error=True), 0.0

    missing = [k for k in (tool.input_schema.get("required") or []) if k not in (arguments or {})]
    if missing:
        msg = "缺少必填参数：%s" % ", ".join(missing)
        _log(name, arguments, False, time.time() - started, msg)
        return protocol.tool_result(msg, is_error=True), time.time() - started

    key = _cache_key(name, arguments)
    now = time.time()
    if _CACHE_TTL > 0:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            _log(name, arguments, True, 0.0, None, cached=True)
            return _cached_result(hit[1]), 0.0

    try:
        payload = tool.handler(**(arguments or {}))
        elapsed = time.time() - started
        result = protocol.tool_result(payload)
        if _CACHE_TTL > 0:
            _CACHE[key] = (time.time(), result)
            if len(_CACHE) > 200:
                for stale in [k for k, (ts, _) in _CACHE.items()
                              if now - ts >= _CACHE_TTL][:100]:
                    _CACHE.pop(stale, None)
        _log(name, arguments, True, elapsed, None)
        return result, elapsed
    except ToolError as e:
        elapsed = time.time() - started
        _log(name, arguments, False, elapsed, str(e))
        return protocol.tool_result(str(e), is_error=True), elapsed
    except Exception as e:  # noqa: BLE001  任何工具崩溃都不能把整个 Web 应用带下去
        elapsed = time.time() - started
        detail = traceback.format_exc(limit=2)
        _log(name, arguments, False, elapsed, "%s: %s" % (type(e).__name__, e))
        return protocol.tool_result("工具执行异常：%s（%s）" % (e, detail.strip().splitlines()[-1]), is_error=True), elapsed


def _log(name, args, success, elapsed, error, cached=False):
    _CALL_LOG.append({
        "tool": name, "arguments": args, "success": success,
        "elapsed_ms": int(elapsed * 1000), "error": error,
        "cached": cached,
    })
    del _CALL_LOG[:-200]


def recent_calls(limit=20):
    return list(reversed(_CALL_LOG[-limit:]))


def describe_for_prompt():
    """给大模型看的工具说明（真实 LLM 走 function calling 时用同一份 schema）。"""
    lines = []
    for t in list_tools():
        lines.append("- %s: %s；参数 %s" % (t["name"], t["description"],
                                            list((t["inputSchema"].get("properties") or {}).keys())))
    return "\n".join(lines)
