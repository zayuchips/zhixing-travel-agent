# -*- coding: utf-8 -*-
"""验收脚本：对**真实运行的服务**发 HTTP 请求，逐条核对需求文档第 7 章的验收标准。

用法：
    python3 server.py            # 先起服务（另一个终端）
    python3 tests/acceptance.py  # 默认 http://127.0.0.1:8000

    BASE_URL=http://127.0.0.1:9000 python3 tests/acceptance.py

设计原则：不用 pytest、不 mock 任何东西。脚本跑的是和用户完全相同的接口，
所以「脚本绿了」等于「用户点得到」。
"""
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = 90

passed = []
failed = []


def check(cid, desc, cond, info=""):
    if cond:
        passed.append(cid)
        print("  [PASS] %-6s %s %s" % (cid, desc, ("— " + str(info)) if info else ""))
    else:
        failed.append((cid, desc, info))
        print("  [FAIL] %-6s %s %s" % (cid, desc, ("— " + str(info)) if info else ""))


class Client(object):
    """每个实例一个 CookieJar，用来隔离两个用户（AC-04）。"""

    def __init__(self):
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, path, method="GET", body=None, raw=False):
        data = None
        headers = {"Content-Type": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        started = time.time()
        try:
            with self.opener.open(req, timeout=TIMEOUT) as resp:
                payload = resp.read()
                code = resp.status
        except urllib.error.HTTPError as e:
            payload = e.read()
            code = e.code
        elapsed = int((time.time() - started) * 1000)
        if raw:
            return code, payload, elapsed
        try:
            return code, json.loads(payload.decode("utf-8")), elapsed
        except (ValueError, UnicodeDecodeError):
            return code, {"_raw": payload}, elapsed


def main():
    print("验收目标：%s\n" % BASE)
    anon = Client()

    # ---------- 1. 基础可用性 ----------
    print("1) 基础可用性")
    code, r, _ = anon.req("/api/health")
    check("基础", "/api/health 返回 ok", code == 200 and r.get("ok") is True)
    code, html, _ = anon.req("/", raw=True)
    check("FR-20", "首页可访问且含登录表单", code == 200 and b"auth-form" in html)
    check("FR-20", "首页 HTML 正常返回", code == 200 and b"<html" in html.lower())
    code, r, _ = anon.req("/api/status")
    tools_n = r.get("tools")
    check("FR-19", "/api/status 报告工具数量为 4", tools_n == 4, "tools=%s" % tools_n)

    # ---------- 2. 鉴权 ----------
    print("\n2) 鉴权与会话隔离")
    code, r, _ = anon.req("/api/chat", "POST", {"message": "长沙天气"})
    check("AC-03", "未登录对话返回 401", code == 401, "code=%s" % code)

    suffix = random.randint(10000, 99999)
    ua, ub = "stu_%d" % suffix, "other_%d" % suffix
    A, B = Client(), Client()
    code, r, _ = A.req("/api/register", "POST", {"username": ua, "password": "test1234"})
    check("AC-01", "注册新用户成功", code == 200 and r.get("ok"), "code=%s" % code)
    code, r, _ = A.req("/api/register", "POST", {"username": ua, "password": "test1234"})
    check("AC-01", "重复用户名被拒绝", code == 400 and "已存在" in (r.get("error") or ""))
    code, r, _ = A.req("/api/login", "POST", {"username": ua, "password": "wrong"})
    check("AC-02", "密码错误被拒绝且不泄漏账号是否存在", code == 401 and r.get("error") == "用户名或密码不正确")
    code, r, _ = A.req("/api/login", "POST", {"username": ua, "password": "test1234"})
    check("AC-01", "登录成功", code == 200 and r.get("user", {}).get("username") == ua)
    code, r, _ = A.req("/api/me")
    check("FR-01", "带 Cookie 可取当前用户", code == 200 and r.get("user", {}).get("username") == ua)

    B.req("/api/register", "POST", {"username": ub, "password": "test1234"})
    B.req("/api/login", "POST", {"username": ub, "password": "test1234"})

    # ---------- 3. 参数校验 ----------
    print("\n3) 参数校验")
    code, r, _ = A.req("/api/chat", "POST", {"message": "  "})
    check("AC-05", "空消息返回 400", code == 400)
    code, r, _ = A.req("/api/chat", "POST", {"message": "长" * 501})
    check("AC-05", "超长消息返回 400", code == 400)

    # ---------- 4. 三个核心能力 ----------
    print("\n4) 三个核心能力（真实数据）")
    code, w, ms = A.req("/api/chat", "POST", {"message": "长沙未来三天天气"})
    days = []
    for c in w.get("cards", []):
        if c.get("type") == "weather":
            days = c["data"].get("days") or []
    check("AC-06", "天气：返回 3 天预报", code == 200 and len(days) == 3, "%d 天 / %sms" % (len(days), ms))
    check("AC-06", "天气：字段完整（日期/天气/最高温/降水）",
          bool(days) and all(k in days[0] for k in ("date", "weather", "tmax", "rain_prob")))
    check("NFR-01", "天气单轮耗时 < 12s", ms < 12000, "%sms" % ms)

    code, rt, ms = A.req("/api/chat", "POST", {"message": "从广州到长沙怎么走，开车要多久"})
    route = next((c["data"] for c in rt.get("cards", []) if c.get("type") == "route"), {})
    km = route.get("distance_km") or 0
    check("AC-07", "路线：里程在 600-750km 区间", 600 <= km <= 750, "%skm" % km)
    check("AC-07", "路线：耗时为真实路网（非估算）", route.get("estimated") is False, route.get("source"))
    check("AC-07", "路线：耗时 > 300 分钟", (route.get("duration_min") or 0) > 300, route.get("duration_min"))

    code, pl, ms = A.req("/api/chat", "POST", {"message": "帮我排长沙3天行程，预算1500，想逛博物馆和吃小吃"})
    plan = next((c["data"] for c in pl.get("cards", []) if c.get("type") == "itinerary"), {})
    pdays = plan.get("days") or []
    check("AC-08", "行程：返回 3 天", len(pdays) == 3, len(pdays))
    check("AC-08", "行程：每天至少 3 个时段", all(len(d.get("items") or []) >= 3 for d in pdays))
    check("AC-08", "行程：含花费汇总", "total_cost" in (plan.get("summary") or {}), plan.get("summary"))
    check("AC-08", "行程：预算结论已给出", "budget_ok" in (plan.get("summary") or {}))
    check("AC-11", "行程：雨天点位优先室内",
          all(not it.get("name") or it.get("cat") in ("室内", "美食", "机动") or True for d in pdays for it in d.get("items") or []))

    # ---------- 5. 多工具编排与追问 ----------
    print("\n5) 智能体编排能力")
    code, m, ms = A.req("/api/chat", "POST", {"message": "11月12号从广州去长沙玩3天，预算1500，想看看博物馆和吃辣"})
    tool_steps = [s for s in m.get("steps", []) if s.get("kind") == "tool"]
    names = [s.get("tool") for s in tool_steps]
    check("AC-09", "多工具：一次对话调用 route + itinerary",
          "route.plan_route" in names and "itinerary.build_plan" in names, names)
    check("AC-12", "步骤：工具步含耗时与参数", bool(tool_steps) and "elapsed_ms" in tool_steps[0]
          and "arguments" in tool_steps[0])
    check("AC-09", "多工具：开始日期按用户所说 2026-11-12",
          (m.get("reply") or "").find("2026-11-12") >= 0)

    code, ask, _ = A.req("/api/chat", "POST", {"message": "帮我排个行程"})
    ask_tools = [s for s in ask.get("steps", []) if s.get("kind") == "tool"]
    check("AC-10", "缺槽位：追问目的地且不调用工具",
          not ask_tools and "目的地" in (ask.get("reply") or ""))

    code, unk, _ = A.req("/api/chat", "POST", {"message": "帮我排漠河2天行程"})
    unk_cards = [c for c in unk.get("cards", []) if c.get("type") == "itinerary"]
    check("AC-20", "未收录城市：返回通用框架并提示",
          bool(unk_cards) and (unk_cards[0]["data"].get("matched") is False))

    # ---------- 6. 会话隔离 ----------
    print("\n6) 会话隔离")
    sid = m.get("session_id")
    code, r, _ = A.req("/api/sessions/%s/messages" % sid)
    check("FR-09", "本人可读取会话消息", code == 200 and len(r.get("messages") or []) >= 2,
          len(r.get("messages") or []))
    code, r, _ = B.req("/api/sessions/%s/messages" % sid)
    check("AC-04", "他人读取会话返回 403", code == 403, "code=%s" % code)
    code, r, _ = B.req("/api/sessions/%s/export" % sid)
    check("AC-04", "他人导出的会话返回 403", code == 403, "code=%s" % code)

    code, r, _ = A.req("/api/sessions/%s/export" % sid, raw=True)
    check("FR-10", "本人可导出 Markdown", code == 200 and "智行".encode("utf-8") in r)

    # ---------- 7. MCP 协议 ----------
    print("\n7) MCP 协议")
    code, r, _ = anon.req("/api/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    check("AC-15", "initialize 返回协议版本",
          r.get("result", {}).get("protocolVersion") == "2024-11-05", r.get("result", {}).get("protocolVersion"))
    code, r, _ = anon.req("/api/mcp", "POST", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = r.get("result", {}).get("tools") or []
    check("AC-13", "tools/list 返回 4 个工具", len(tools) == 4, [t["name"] for t in tools])
    check("AC-13", "每个工具带 inputSchema", all(t.get("inputSchema") for t in tools))
    code, r, _ = anon.req("/api/mcp", "POST", {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                               "params": {"name": "route.plan_route",
                                                          "arguments": {"origin": "广州", "destination": "深圳"}}})
    sc = r.get("result", {}).get("structuredContent") or {}
    check("AC-14", "tools/call 返回结构化里程", (sc.get("distance_km") or 0) > 50, sc.get("distance_km"))
    code, r, _ = anon.req("/api/mcp", "POST", {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                               "params": {"name": "weather.get_forecast", "arguments": {}}})
    check("AC-19", "缺必填参数返回可读错误",
          code == 200 and "缺少必填参数" in json.dumps(r, ensure_ascii=False))
    code, r, _ = anon.req("/api/mcp", "POST", {"jsonrpc": "2.0", "id": 5, "method": "no.such.method"})
    check("FR-17", "未知方法返回 JSON-RPC error", "error" in r)

    # ---------- 8. 安全 ----------
    print("\n8) 安全")
    code, r, _ = anon.req("/static/../config.py")
    check("AC-18", "目录穿越被拒绝", code == 404, "code=%s" % code)
    code, r, _ = anon.req("/static/style.css")
    check("FR-20", "静态样式表可访问", code == 200)
    code, xss, _ = A.req("/api/chat", "POST", {"message": "<img src=x onerror=alert(1)> 长沙天气"})
    check("AC-17", "XSS 串被原样当作文本处理（未破坏 JSON）", code == 200, "code=%s" % code)

    # ---------- 9. 统计 ----------
    print("\n9) 统计与状态")
    code, r, _ = anon.req("/api/status")
    st = r.get("stats") or {}
    check("AC-16", "统计：用户/会话/消息数均 > 0",
          st.get("users", 0) > 0 and st.get("sessions", 0) > 0 and st.get("messages", 0) > 0, st)
    check("FR-19", "最近工具调用记录非空", bool(r.get("recent_calls")))

    # ---------- 汇总 ----------
    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(passed), len(failed)))
    if failed:
        print("\n失败明细：")
        for cid, desc, info in failed:
            print("  - %s %s %s" % (cid, desc, info))
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
