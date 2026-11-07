# -*- coding: utf-8 -*-
"""MCP（Model Context Protocol）协议的最小可用实现。

为什么自己写而不是装 SDK：
    作业要求「智能体要能嵌入你开发的软件系统」，我们要的是一个**可控、可讲清楚**的
    工具调用层。MCP 的消息格式本质上就是 JSON-RPC 2.0，核心只有四个方法：

        initialize      握手，返回协议版本与能力
        tools/list      列出可用工具（名字 + 描述 + JSON Schema 参数）
        tools/call      调用某个工具，返回 content 列表
        ping            探活

    本项目里同一份 registry 被两处复用：
        1) 进程内：agent 直接调用（Web 应用的主链路）
        2) 独立进程：mcp/server_stdio.py 作为标准 MCP Server，
           可以被 Claude Desktop / 任意 MCP 客户端挂载（外部复用）

协议版本：2024-11-05
"""
import json

PROTOCOL_VERSION = "2024-11-05"
JSONRPC = "2.0"

# JSON-RPC 2.0 标准错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def ok(msg_id, result):
    return {"jsonrpc": JSONRPC, "id": msg_id, "result": result}


def err(msg_id, code, message, data=None):
    e = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": JSONRPC, "id": msg_id, "error": e}


def text_content(text):
    """MCP 的 content 块：目前官方定义有 text / image / resource 三种。"""
    return {"type": "text", "text": text}


def tool_result(payload, is_error=False):
    """把工具的结构化返回值包装成 MCP tools/call 的结果。

    MCP 规定 content 是「给人/模型看的」，structuredContent 是「给程序看的」。
    两个都给，前端画卡片用 structuredContent，模型总结用 content 里的文本。
    """
    if isinstance(payload, str):
        text = payload
        structured = {"text": payload}
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        structured = payload
    return {"content": [text_content(text)], "structuredContent": structured, "isError": is_error}
