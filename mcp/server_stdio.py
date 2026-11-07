# -*- coding: utf-8 -*-
"""独立 MCP Server（stdio 传输），可被 Claude Desktop 等标准 MCP 客户端挂载。

和 Web 应用共用同一份工具注册表 —— 这就是「协议」的价值：
同一套工具，内部智能体能用，外部客户端也能用，不需要复制代码。

用法（Claude Desktop 的 claude_desktop_config.json）：
    {
      "mcpServers": {
        "zhixing-travel": {
          "command": "python3",
          "args": ["/绝对路径/zhixing-travel-agent/mcp/server_stdio.py"]
        }
      }
    }

手动试一下：
    echo '{"jsonrpc":"2.0","id":1,"method":"initialize"}' | python3 mcp/server_stdio.py
    echo '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | python3 mcp/server_stdio.py

协议：MCP 2024-11-05，stdio 传输使用「一行一个 JSON」。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import protocol  # noqa: E402
from mcp import registry  # noqa: E402
from mcp.tools import register_all  # noqa: E402


def handle(req):
    """处理一条 JSON-RPC 请求，返回响应 dict（通知类请求返回 None）。"""
    if not isinstance(req, dict):
        return protocol.err(None, protocol.INVALID_REQUEST, "不是合法的 JSON-RPC 对象")
    if "id" not in req:  # notification，例如 notifications/initialized，不需要回包
        return None
    rid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if method == "initialize":
        return protocol.ok(rid, {
            "protocolVersion": protocol.PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "zhixing-travel-agent", "version": "1.0.0"},
        })
    if method == "tools/list":
        return protocol.ok(rid, {"tools": registry.list_tools()})
    if method == "tools/call":
        name = params.get("name")
        if not name:
            return protocol.err(rid, protocol.INVALID_PARAMS, "缺少 params.name")
        result, _ = registry.call_tool(name, params.get("arguments") or {})
        return protocol.ok(rid, result)
    if method == "ping":
        return protocol.ok(rid, {})
    return protocol.err(rid, protocol.METHOD_NOT_FOUND, "未知方法：%s" % method)


def serve(stdin, stdout):
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            stdout.write(json.dumps(protocol.err(None, protocol.PARSE_ERROR, "JSON 解析失败")) + "\n")
            stdout.flush()
            continue
        try:
            resp = handle(req)
        except Exception as e:  # noqa: BLE001
            resp = protocol.err(req.get("id"), protocol.INTERNAL_ERROR, "内部错误：%s" % e)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


def main():
    register_all()
    sys.stderr.write("zhixing MCP server ready (stdio), tools=%s\n"
                     % ", ".join(t["name"] for t in registry.list_tools()))
    try:
        serve(sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
