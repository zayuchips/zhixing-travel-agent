# -*- coding: utf-8 -*-
"""智行 · 出行规划智能体 —— Web 服务入口（零第三方依赖）。

启动：
    python3 server.py            # http://127.0.0.1:8000
    PORT=8080 python3 server.py  # 换端口
    BIND=0.0.0.0 python3 server.py  # 允许局域网/手机访问（部署到服务器时用）

只用 Python 标准库：
    http.server.ThreadingHTTPServer  —— 多线程 Web 服务
    sqlite3                          —— 数据持久化
    urllib                           —— 调用外部工具与（可选）大模型
这样做的原因写在 docs/需求规格说明书.md 的「非功能需求-可部署性」里：
答辩机房里 pip 装不上包是常态，零依赖意味着 `python3 server.py` 就能跑。
"""
import json
import mimetypes
import os
import sys
import traceback
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import db  # noqa: E402
from agent import Agent  # noqa: E402
from agent import attachments, guide, pi_bridge, titler  # noqa: E402
from mcp import protocol  # noqa: E402
from mcp import registry  # noqa: E402
from mcp.tools import register_all  # noqa: E402

AGENT = Agent()
register_all()

DEMO_USER = ("demo", "demo1234")


def _mask(secret):
    """脱敏：只回显首尾各 4 位，够用户确认是哪个 Key，又不至于泄漏。"""
    if not secret:
        return ""
    if len(secret) <= 12:
        return secret[:2] + "****"
    return secret[:4] + "****" + secret[-4:]


def _ensure_demo_user():
    """给老师/助教准备一个体验账号，省得他们注册。"""
    uid, err = db.create_user(*DEMO_USER)
    if uid is None and "已存在" not in (err or ""):
        print("[warn] 体验账号创建失败：%s" % err)


class Handler(BaseHTTPRequestHandler):
    server_version = "Zhixing/" + config.APP_VERSION
    protocol_version = "HTTP/1.1"

    # ---------------- 基础 ----------------
    def log_message(self, fmt, *args):
        sys.stderr.write("%s  %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, code, body, content_type="application/json; charset=utf-8", extra_headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "no-cache")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200, headers=None):
        self._send(code, obj, extra_headers=headers)

    def _error(self, code, message):
        self._json({"ok": False, "error": message}, code)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None  # 由调用方返回 400

    def _token(self):
        raw = self.headers.get("Cookie") or ""
        c = SimpleCookie()
        c.load(raw)
        m = c.get("zx_token")
        return m.value if m else None

    def _user(self):
        return db.user_by_token(self._token())

    # ---------------- 路由 ----------------
    def do_GET(self):
        try:
            self._route("GET")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._error(500, "服务端异常")

    def do_POST(self):
        try:
            self._route("POST")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._error(500, "服务端异常")

    def do_DELETE(self):
        try:
            self._route("DELETE")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._error(500, "服务端异常")

    def do_HEAD(self):
        self.do_GET()

    def _route(self, method):
        path = unquote(urlparse(self.path).path)
        query = parse_qs(urlparse(self.path).query)

        if method == "GET" and (path == "/" or path == "/index.html"):
            return self._static("index.html")
        if method == "GET" and path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if method == "GET" and path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if not path.startswith("/api/"):
            return self._error(404, "页面不存在")

        if method == "GET" and path == "/api/health":
            return self._json({"ok": True, "version": config.APP_VERSION})
        if method == "GET" and path == "/api/status":
            pi_ok, pi_reason = pi_bridge.available()
            return self._json({
                "ok": True, "app": config.APP_NAME, "version": config.APP_VERSION,
                "engine": {
                    "llm_enabled": pi_ok,
                    "model": config.PI_MODEL or "pi 内置默认",
                    "provider": "pi",
                    "label": "Pi agent harness（RPC）",
                    "reason": pi_reason,
                },
                "amap": bool(config.AMAP_KEY),
                "tools": len(registry.list_tools()),
                "stats": db.stats(), "recent_calls": registry.recent_calls(8),
            })
        if method == "GET" and path == "/api/tools":
            return self._json({"ok": True, "tools": registry.list_tools()})

        if method == "POST" and path == "/api/mcp":
            return self._mcp()

        if method == "POST" and path == "/api/register":
            body = self._body()
            if body is None:
                return self._error(400, "请求体不是合法 JSON")
            uid, err = db.create_user(body.get("username"), body.get("password"))
            if err:
                return self._error(400, err)
            return self._json({"ok": True, "user": {"id": uid, "username": body.get("username")}})

        if method == "POST" and path == "/api/login":
            body = self._body() or {}
            user = db.verify_user(body.get("username"), body.get("password"))
            if not user:
                return self._error(401, "用户名或密码不正确")
            token = db.issue_token(user["id"])
            return self._json({"ok": True, "user": user},
                              headers={"Set-Cookie": "zx_token=%s; Path=/; HttpOnly; Max-Age=604800; SameSite=Lax" % token})

        if method == "POST" and path == "/api/logout":
            token = self._token()
            if token:
                with db._LOCK:  # noqa: SLF001  作业规模，简单处理
                    conn = db._connect()  # noqa: SLF001
                    conn.execute("DELETE FROM tokens WHERE token = ?", (token,))
                    conn.commit()
            return self._json({"ok": True}, headers={"Set-Cookie": "zx_token=; Path=/; Max-Age=0"})

        user = self._user()
        if not user:
            return self._error(401, "未登录")

        if method == "GET" and path == "/api/me":
            return self._json({"ok": True, "user": user})

        # ---------------- 设置页 ----------------
        if method == "GET" and path == "/api/settings":
            amap = config.AMAP_KEY or ""
            mkey = config.LLM_API_KEY or ""
            return self._json({
                "ok": True,
                "amap_key": _mask(amap), "amap_set": bool(amap),
                "model_key": _mask(mkey), "model_set": bool(mkey),
                "llm_model": config.LLM_MODEL, "pi_model": config.PI_MODEL,
                "provider": config.PI_PROVIDER,
                "engine_ready": pi_bridge.available()[0],
            })

        if method == "POST" and path == "/api/settings":
            body = self._body() or {}
            updates = {}
            for field, env_key in (("amap_key", "AMAP_KEY"),
                                   ("model_key", "DASHSCOPE_API_KEY"),
                                   ("pi_model", "PI_MODEL"),
                                   ("llm_model", "LLM_MODEL")):
                val = (body.get(field) or "").strip()
                if val:
                    updates[env_key] = val
            if body.get("model_key"):
                # 抽取记忆的轻量调用走 LLM_API_KEY，两个都写，避免只改一个不生效
                updates["LLM_API_KEY"] = body["model_key"].strip()
            if not updates:
                return self._error(400, "没有要保存的字段")
            changed = config.update_env(updates)
            pi_bridge.shutdown()  # provider/model 是启动参数，改了必须重建进程
            return self._json({"ok": True, "changed": sorted(changed)})

        # ---------------- 长期记忆 ----------------
        if method == "GET" and path == "/api/memories":
            return self._json({"ok": True, "memories": db.memories_get(user["id"])})

        if method == "POST" and path == "/api/memories":
            body = self._body() or {}
            key = (body.get("key") or "").strip()
            val = (body.get("value") or "").strip()
            if not key or not val:
                return self._error(400, "key 和 value 都不能为空")
            db.memory_set(user["id"], key, val, "manual")
            return self._json({"ok": True})

        if method == "POST" and path == "/api/memories/delete":
            body = self._body() or {}
            key = (body.get("key") or "").strip()
            return self._json({"ok": db.memory_del(user["id"], key)})

        if method == "POST" and path == "/api/memories/clear":
            return self._json({"ok": True, "removed": db.memories_clear(user["id"])})

        # ---------------- 打断 ----------------
        if method == "POST" and path == "/api/chat/abort":
            body = self._body() or {}
            return self._json({"ok": True, "aborted": pi_bridge.abort(body.get("session_id"))})

        # ---------------- 附件上传 ----------------
        if method == "POST" and path == "/api/upload":
            body = self._body()
            if body is None:
                return self._error(400, "请求体不是合法 JSON")
            if not body.get("data"):
                return self._error(400, "没有文件内容")
            try:
                fpath, ext, size = attachments.save(body.get("name"), body["data"])
                if ext in attachments.IMAGE_EXT:
                    kind = "图片"
                    text = attachments.describe_image(fpath, ext)
                else:
                    kind = "文档"
                    text = attachments.extract_text(fpath, ext)
            except attachments.AttachError as e:
                return self._error(400, str(e))
            except Exception as e:  # noqa: BLE001
                return self._error(400, "处理失败：%s: %s" % (type(e).__name__, e))
            return self._json({
                "ok": True, "name": os.path.basename(fpath), "kind": kind,
                "size": size, "text": (text or "")[:8000],
                "preview": (text or "").strip()[:100],
            })

        # ---------------- 攻略导出 ----------------
        if method == "POST" and path == "/api/guide":
            body = self._body() or {}
            plan = body.get("plan")
            if not isinstance(plan, dict) or not plan.get("days"):
                return self._error(400, "没有可导出的行程数据")
            fmt = "docx" if body.get("format") == "docx" else "html"
            try:
                if fmt == "docx":
                    url, dest = guide.save_docx(plan, body.get("title"))
                else:
                    url, dest = guide.save_html(plan, body.get("title"), body.get("highlight"))
            except Exception as e:  # noqa: BLE001
                return self._error(500, "生成失败：%s: %s" % (type(e).__name__, e))
            return self._json({"ok": True, "url": url, "format": fmt,
                               "size": os.path.getsize(dest)})

        if method == "GET" and path == "/api/sessions":
            return self._json({"ok": True, "sessions": db.list_sessions(user["id"])})

        if method == "POST" and path == "/api/sessions":
            sid = db.create_session(user["id"], (self._body() or {}).get("title") or "新对话")
            return self._json({"ok": True, "session": {"id": sid, "title": "新对话"}})

        if path.startswith("/api/sessions/"):
            parts = path.strip("/").split("/")
            try:
                sid = int(parts[2])
            except (IndexError, ValueError):
                return self._error(400, "会话 id 不合法")
            if db.session_owner(sid) != user["id"]:
                return self._error(403, "无权访问该会话")
            if method == "GET" and len(parts) == 4 and parts[3] == "messages":
                return self._json({"ok": True, "messages": db.list_messages(sid)})
            if method == "GET" and len(parts) == 4 and parts[3] == "export":
                return self._export(sid)
            if method == "POST" and len(parts) == 4 and parts[3] == "rename":
                body = self._body() or {}
                title = (body.get("title") or "").strip()
                if not title:
                    return self._error(400, "标题不能为空")
                db.rename_session(sid, user["id"], title[:30])
                return self._json({"ok": True})
            if method == "DELETE" and len(parts) == 3:
                db.delete_session(sid, user["id"])
                return self._json({"ok": True})

        if method == "POST" and path == "/api/chat/stream":
            body = self._body()
            if body is None:
                return self._error(400, "请求体不是合法 JSON")
            text = (body.get("message") or "").strip()
            if not text:
                return self._error(400, "消息不能为空")
            if len(text) > 500:
                return self._error(400, "消息太长了，请控制在 500 字以内")
            sid = body.get("session_id")
            if not sid:
                sid = db.create_session(user["id"])  # 标题由模型首轮后概括，先留空
            else:
                try:
                    sid = int(sid)
                except (TypeError, ValueError):
                    return self._error(400, "会话 id 不合法")
                if db.session_owner(sid) != user["id"]:
                    return self._error(403, "无权访问该会话")
            db.add_message(sid, "user", text)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            # HTTP/1.1 默认 keep-alive，而流式响应没有 Content-Length，
            # 不主动关连接的话客户端会一直等 EOF。这里明确收尾。
            self.close_connection = True

            def emit(obj):
                self.wfile.write(
                    ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()

            def on_rec(rec):
                """把 Pi 的原始事件压成前端需要的几种，省得前端认 Pi 的协议。"""
                kind = rec.get("type")
                if kind == "agent_start":
                    emit({"t": "stage", "v": "理解你的需求"})
                elif kind == "message_update":
                    ev = rec.get("assistantMessageEvent") or {}
                    if ev.get("type") == "text_delta":
                        emit({"t": "delta", "v": ev.get("delta") or ""})
                elif kind == "tool_execution_start":
                    emit({"t": "tool", "phase": "start",
                          "name": pi_bridge.pretty(rec.get("toolName")),
                          "args": rec.get("args") or {}})
                elif kind == "tool_execution_end":
                    emit({"t": "tool", "phase": "end",
                          "name": pi_bridge.pretty(rec.get("toolName")),
                          "ok": not rec.get("isError"),
                          "ms": int(rec.get("durationMs") or 0)})

            try:
                emit({"t": "hello", "session_id": sid})
                result = AGENT.respond(text, session_id=sid, user_id=user["id"],
                                      stream=on_rec, files=body.get("attachments"))
                db.add_message(sid, "assistant", result["reply"], result["steps"], result["cards"])
                sessions = db.list_sessions(user["id"])
                title = next((s["title"] for s in sessions if s["id"] == sid), "新对话")
                fresh = title == "新对话"
                if fresh:
                    db.rename_session(sid, user["id"], text[:18])
                    result["title_pending"] = True
                emit({"t": "done", "result": result, "session_id": sid})
                if fresh:
                    # 截断标题只是占位，模型概括的标题几秒后写库，前端会再拉一次列表
                    titler.name_async(sid, user["id"], text, result["reply"])
            except (BrokenPipeError, ConnectionResetError):
                pass  # 用户中途关页面，正常现象
            except Exception as e:  # noqa: BLE001
                try:
                    emit({"t": "error", "message": "%s: %s" % (type(e).__name__, e)})
                except Exception:  # noqa: BLE001
                    pass
            return

        if method == "POST" and path == "/api/chat":
            body = self._body()
            if body is None:
                return self._error(400, "请求体不是合法 JSON")
            text = (body.get("message") or "").strip()
            if not text:
                return self._error(400, "消息不能为空")
            if len(text) > 500:
                return self._error(400, "消息太长了，请控制在 500 字以内")
            sid = body.get("session_id")
            if not sid:
                sid = db.create_session(user["id"])  # 标题由模型首轮后概括，先留空
            else:
                try:
                    sid = int(sid)
                except (TypeError, ValueError):
                    return self._error(400, "会话 id 不合法")
                if db.session_owner(sid) != user["id"]:
                    return self._error(403, "无权访问该会话")
            db.add_message(sid, "user", text)
            result = AGENT.respond(text, session_id=sid, user_id=user["id"],
                                   files=body.get("attachments"))
            db.add_message(sid, "assistant", result["reply"], result["steps"], result["cards"])
            sessions = db.list_sessions(user["id"])
            title = next((s["title"] for s in sessions if s["id"] == sid), "新对话")
            fresh = title == "新对话"
            if fresh:
                db.rename_session(sid, user["id"], text[:18])
            result.update({"ok": True, "session_id": sid, "title_pending": fresh})
            if fresh:
                # 先给个临时标题让界面有东西显示，模型概括好的标题后台补上
                titler.name_async(sid, user["id"], text, result["reply"])
            return self._json(result)

        return self._error(404, "接口不存在：%s" % path)

    # ---------------- MCP over HTTP ----------------
    def _mcp(self):
        """把 MCP 的 JSON-RPC 方法暴露成 HTTP 端点。

        可以用 curl 直接体验（答辩现场演示「同一套工具，两种用法」）：
            curl -s localhost:8000/api/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
        """
        req = self._body()
        if not isinstance(req, dict):
            return self._json(protocol.err(None, protocol.PARSE_ERROR, "请求体不是合法 JSON"))
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        if method == "initialize":
            return self._json(protocol.ok(rid, {
                "protocolVersion": protocol.PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "zhixing-travel-agent", "version": config.APP_VERSION},
            }))
        if method == "tools/list":
            return self._json(protocol.ok(rid, {"tools": registry.list_tools()}))
        if method == "tools/call":
            name = params.get("name")
            if not name:
                return self._json(protocol.err(rid, protocol.INVALID_PARAMS, "缺少 params.name"))
            result, _ = registry.call_tool(name, params.get("arguments") or {})
            return self._json(protocol.ok(rid, result))
        if method == "ping":
            return self._json(protocol.ok(rid, {}))
        return self._json(protocol.err(rid, protocol.METHOD_NOT_FOUND, "未知方法：%s" % method))

    # ---------------- 静态文件与导出 ----------------
    def _static(self, rel):
        rel = os.path.normpath(unquote(rel)).lstrip("/")
        root = os.path.realpath(config.STATIC_DIR)
        full = os.path.realpath(os.path.join(root, rel))
        # 归一化之后再判断前缀，否则 "/static/../config.py" 会通过字符串前缀检查
        if not (full == root or full.startswith(root + os.sep)) or not os.path.isfile(full):
            return self._error(404, "静态资源不存在")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            data = f.read()
        return self._send(200, data, ctype)

    def _export(self, sid):
        msgs = db.list_messages(sid)
        lines = ["# 智行 · 出行会话导出", ""]
        for m in msgs:
            who = "我" if m["role"] == "user" else "智行"
            lines.append("## %s  %s" % (who, m["created_at"]))
            lines.append("")
            lines.append(m["content"])
            if m["role"] == "assistant" and m["steps"]:
                lines.append("")
                lines.append("<details><summary>工具调用轨迹（%d 步）</summary>" % len(m["steps"]))
                lines.append("")
                for s in m["steps"]:
                    lines.append("- **%s**：%s %s" % (s.get("title"), s.get("detail") or s.get("summary") or "",
                                                     "·%sms" % s["elapsed_ms"] if s.get("elapsed_ms") else ""))
                lines.append("")
                lines.append("</details>")
            lines.append("")
        body = "\n".join(lines)
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        data = body.encode("utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", 'attachment; filename="zhixing-session-%d.md"' % sid)
        self.end_headers()
        self.wfile.write(data)


def main():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    db._connect()  # noqa: SLF001  建表
    _ensure_demo_user()
    srv = ThreadingHTTPServer((config.BIND, config.PORT), Handler)
    srv.daemon_threads = True
    print("=" * 52)
    print(" %s  v%s" % (config.APP_NAME, config.APP_VERSION))
    print(" 地址: http://%s:%d" % ("127.0.0.1" if config.BIND == "0.0.0.0" else config.BIND, config.PORT))
    pi_ok, pi_reason = pi_bridge.available()
    print(" 引擎: %s" % ("Pi agent harness（RPC）" if pi_ok else "未就绪 —— %s" % pi_reason))
    print(" MCP 工具: %s" % ", ".join(t["name"] for t in registry.list_tools()))
    print(" 高德: %s" % ("已配置" if config.AMAP_KEY else "未配置（POI 走本地示例数据）"))
    print(" 体验账号: %s / %s" % DEMO_USER)
    print("=" * 52)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        pi_bridge.shutdown()
        srv.server_close()


if __name__ == "__main__":
    main()
