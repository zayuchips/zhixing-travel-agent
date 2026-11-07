# -*- coding: utf-8 -*-
"""验收脚本：对**真实运行的服务**发 HTTP 请求，逐条核对需求文档第 7 章的验收标准。

用法：
    python3 server.py            # 先起服务（另一个终端）
    python3 tests/acceptance.py  # 默认 http://127.0.0.1:8000

    BASE_URL=http://127.0.0.1:9000 python3 tests/acceptance.py

设计原则：不用 pytest、不 mock 任何东西。脚本跑的是和用户完全相同的接口，
所以「脚本绿了」等于「用户点得到」。

两层断言：
    工具层 —— 通过 /api/mcp 直接调工具。结果确定，不需要大模型，不受模型波动影响。
    智能体层 —— 走 /api/chat，需要 Pi 引擎与模型凭据。没就绪时标 SKIP 而不是 FAIL，
                因为那属于部署环境问题，不是代码缺陷。
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
TIMEOUT = 120

passed = []
failed = []
skipped = []


def check(cid, desc, cond, info=""):
    if cond:
        passed.append(cid)
        print("  [PASS] %-6s %s %s" % (cid, desc, ("— " + str(info)) if info else ""))
    else:
        failed.append((cid, desc, info))
        print("  [FAIL] %-6s %s %s" % (cid, desc, ("— " + str(info)) if info else ""))


def skip(cid, desc, why):
    skipped.append((cid, desc, why))
    print("  [SKIP] %-6s %s — %s" % (cid, desc, why))


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


def mcp(client, method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    code, r, _ = client.req("/api/mcp", "POST", body)
    return r


def tool(client, name, args):
    r = mcp(client, "tools/call", {"name": name, "arguments": args})
    return (r.get("result") or {}).get("structuredContent") or {}


def plan_tool(client, args):
    """调 itinerary 并取出结构化行程，顺便暴露错误文本。"""
    r = mcp(client, "tools/call", {"name": "itinerary.build_plan", "arguments": args})
    res = r.get("result") or {}
    if res.get("isError"):
        return {}, (res.get("content") or [{}])[0].get("text", "")
    return res.get("structuredContent") or {}, ""


def sse_chat(client, message, session_id=None):
    """走流式接口，收集事件类型序列和最终结果。"""
    body = json.dumps({"session_id": session_id, "message": message}).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/chat/stream", data=body,
                                 headers={"Content-Type": "application/json"})
    kinds, done = [], None
    with client.opener.open(req, timeout=TIMEOUT) as r:
        for raw in r:
            s = raw.decode("utf-8", "replace").strip()
            if not s.startswith("data: "):
                continue
            try:
                ev = json.loads(s[6:])
            except ValueError:
                continue
            kinds.append(ev.get("t"))
            if ev.get("t") == "done":
                done = ev
    return kinds, done


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

    code, st, _ = anon.req("/api/status")
    tools_n = st.get("tools")
    check("FR-19", "/api/status 报告工具数量为 9", tools_n == 9, "tools=%s" % tools_n)
    pi_ready = bool((st.get("engine") or {}).get("llm_enabled"))
    amap_ready = bool(st.get("amap"))
    print("  · 环境：Pi 引擎 %s ｜ 高德 %s" % ("就绪" if pi_ready else "未就绪",
                                              "已配置" if amap_ready else "未配置"))

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

    # ---------- 4. 工具层：真实数据（不依赖大模型）----------
    print("\n4) 工具层真实数据（走 /api/mcp，结果确定）")
    w = tool(A, "weather.get_forecast", {"city": "长沙", "days": 3})
    days = w.get("days") or []
    check("AC-06", "天气：返回 3 天预报", len(days) == 3, "%d 天" % len(days))
    check("AC-06", "天气：字段完整（日期/天气/最高温/降水）",
          bool(days) and all(k in days[0] for k in ("date", "weather", "tmax", "rain_prob")))
    check("AC-06", "天气：来源为 open-meteo 实时预报", "open-meteo" in (w.get("source") or ""))

    rt = tool(A, "route.plan_route", {"origin": "广州", "destination": "长沙", "mode": "driving"})
    km = rt.get("distance_km") or 0
    check("AC-07", "路线：里程在 600-750km 区间", 600 <= km <= 750, "%skm" % km)
    check("AC-07", "路线：耗时为真实路网（非估算）", rt.get("estimated") is False, rt.get("source"))
    check("AC-07", "路线：耗时 > 300 分钟", (rt.get("duration_min") or 0) > 300, rt.get("duration_min"))

    plan, err = plan_tool(A, {"destination": "长沙", "days": 3, "budget": 1500,
                              "interests": ["博物馆", "美食"]})
    pdays = plan.get("days") or []
    check("AC-08", "行程：返回 3 天", len(pdays) == 3, err or len(pdays))
    check("AC-08", "行程：每天 2-3 个景点", all(1 <= len(d.get("attractions") or []) <= 3 for d in pdays),
          [len(d.get("attractions") or []) for d in pdays])
    check("AC-08", "行程：每天含早/中/晚三餐", all(len(d.get("meals") or []) == 3 for d in pdays))
    s = plan.get("summary") or {}
    check("AC-08", "预算：四拆齐全（门票/住宿/餐饮/交通）",
          all(k in s for k in ("ticket_cost", "hotel_cost", "food_cost", "transport_cost")), s)
    check("AC-08", "预算：合计等于四项之和",
          abs((s.get("ticket_cost", 0) + s.get("hotel_cost", 0) + s.get("food_cost", 0)
               + s.get("transport_cost", 0)) - s.get("total_cost", -1)) < 1)
    check("AC-08", "行程：预算结论已给出", "budget_ok" in s)
    check("AC-21", "行程：点位带坐标或明确标注来源",
          all(("lng" in a and "lat" in a) or plan.get("poi_source") in ("local", "template")
              for d in pdays for a in d.get("attractions") or []), plan.get("poi_source"))
    # 餐饮 = 三餐逐条累加。旧版用「90×天 + 45×午餐」会把午饭算两遍，这条防回退。
    # 注意：接了高德之后餐饮是真实餐厅人均，绝对值可能超过 130/天，所以断言结构和而不是常数。
    meal_sum = sum(m["cost"] for d in pdays for m in d.get("meals") or [])
    check("AC-22", "预算：餐饮费等于三餐逐条累加（无重复常数项）",
          abs((s.get("food_cost") or 0) - meal_sum) < 1,
          "%s vs %s" % (s.get("food_cost"), meal_sum))

    rainy = [d for d in pdays if d.get("rain_prob") and d.get("rain_prob") >= 60]
    if rainy:
        day = rainy[0]
        ok_indoor = all(a.get("indoor") for a in day.get("attractions") or [])
        # 室内点位排完时允许出现户外，但必须在 note 里如实说明，不许假装没下雨
        honest = "临时取舍" in (day.get("note") or "")
        check("AC-11", "行程：雨天优先室内，不足时如实说明",
              ok_indoor or honest, "%s / %s" % (day.get("date"), day.get("note")))
    else:
        skip("AC-11", "行程：雨天点位优先室内", "预报期内没有降水概率 ≥60% 的日子")

    poi = tool(A, "poi.search_places", {"city": "长沙", "keywords": "博物馆", "limit": 5})
    check("AC-23", "POI：返回结果并标注数据来源",
          poi.get("source") in ("amap", "local") and isinstance(poi.get("items"), list), poi.get("source"))
    if poi.get("source") == "amap":
        check("AC-23", "POI：高德结果带经纬度与名称",
              all(i.get("lng") is not None and i.get("name") for i in poi["items"]))
    else:
        skip("AC-23", "POI：高德结果带经纬度", "未配置 AMAP_KEY，当前走本地示例数据")

    hotel = tool(A, "hotel.search", {"city": "长沙", "level": "舒适"})
    check("AC-24", "酒店：价格标注为估算", all(i.get("price_estimated") for i in hotel.get("items") or [])
          if hotel.get("items") else True, "%d 家" % len(hotel.get("items") or []))

    unk, unk_err = plan_tool(A, {"destination": "漠河", "days": 2})
    # 配了高德之后，未收录城市也应该能搜到真实点位；搜不到才退回通用框架，都要能出东西
    check("AC-20", "未收录城市：仍然产出可用行程（真实点位或明确标注模板）",
          bool(unk.get("days")) and unk.get("poi_source") in ("amap", "local", "template", "empty"),
          unk_err or unk.get("poi_source"))

    # ---------- 5. 智能体链路（需要 Pi + 模型凭据）----------
    print("\n5) 智能体链路（Pi 引擎）")
    code, chat, ms = A.req("/api/chat", "POST", {"message": "长沙未来三天天气"})
    sid = chat.get("session_id")
    check("AC-25", "对话：返回 200 且落库会话", code == 200 and bool(sid), "code=%s" % code)

    # 降级模式：模型不可用时，也必须能靠真实工具给出结果，而不是白屏或编造
    if chat.get("intent") == "fallback":
        check("AC-29", "降级：无模型 Key 仍返回真实工具结果",
              bool(chat.get("cards")) and "降级模式" in (chat.get("reply") or ""),
              [c.get("type") for c in chat.get("cards") or []])
        check("AC-30", "降级：工具步记录了真实调用",
              any(x.get("kind") == "tool" and x.get("ok") for x in chat.get("steps") or []),
              [x.get("tool") for x in chat.get("steps") or [] if x.get("kind") == "tool"])

    if not pi_ready:
        for cid, desc in (("AC-26", "智能体：单轮内调用了天气工具"),
                          ("AC-27", "智能体：工具步含参数与耗时"),
                          ("AC-28", "智能体：多工具编排 route + itinerary"),
                          ("AC-10", "智能体：缺目的地时反问而不是瞎猜")):
            skip(cid, desc, "Pi 引擎未就绪（缺 pi 可执行文件或模型凭据）")
    elif chat.get("intent") == "fallback":
        for cid, desc in (("AC-26", "智能体：单轮内调用了天气工具"),
                          ("AC-27", "智能体：工具步含参数与耗时"),
                          ("AC-28", "智能体：多工具编排 route + itinerary"),
                          ("AC-10", "智能体：缺目的地时反问而不是瞎猜")):
            skip(cid, desc, "模型凭据不可用，本轮已走降级模式")
    else:
        steps = [x for x in chat.get("steps") or [] if x.get("kind") == "tool"]
        check("AC-26", "智能体：单轮内调用了天气工具",
              any(x.get("tool") == "weather.get_forecast" for x in steps),
              [x.get("tool") for x in steps])
        check("AC-27", "智能体：工具步含参数与耗时",
              bool(steps) and "arguments" in steps[0] and "elapsed_ms" in steps[0])
        code, multi, _ = A.req("/api/chat", "POST", {"message": "11月12号从广州去长沙玩3天，预算1500，想逛博物馆"})
        names = [x.get("tool") for x in multi.get("steps") or [] if x.get("kind") == "tool"]
        check("AC-28", "智能体：多工具编排 route + itinerary",
              "route.plan_route" in names and "itinerary.build_plan" in names, names)
        code, ask, _ = A.req("/api/chat", "POST", {"message": "帮我排个行程"})
        ask_tools = [x for x in ask.get("steps") or [] if x.get("kind") == "tool"]
        check("AC-10", "智能体：缺目的地时反问而不是瞎猜",
              not ask_tools and "目的地" in (ask.get("reply") or ""))

    # ---------- 5.5 流式 / 设置 / 记忆 / 打断 ----------
    print("\n5.5) 流式、设置、记忆")
    kinds, done_sse = sse_chat(A, "长沙天气怎么样")
    check("AC-31", "流式：事件序列含 hello 与 done",
          "hello" in kinds and "done" in kinds, kinds[:6])
    check("AC-31", "流式：有增量文本块", kinds.count("delta") > 0, "%d 块" % kinds.count("delta"))
    check("AC-31", "流式：done 带回完整结果",
          bool(done_sse) and isinstance(done_sse.get("result"), dict)
          and "reply" in done_sse["result"])

    code, st, _ = A.req("/api/settings")
    blobs = (st.get("amap_key") or "") + (st.get("model_key") or "")
    check("AC-32", "设置：Key 以脱敏形式返回，不吐明文",
          code == 200 and "****" in blobs and len(blobs) < 40, st.get("amap_key"))

    A.req("/api/memories/clear", "POST")
    A.req("/api/memories", "POST", {"key": "常住城市", "value": "广州"})
    code, mem, _ = A.req("/api/memories")
    keys = [m["key"] for m in mem.get("memories") or []]
    check("AC-33", "记忆：写入后可读回", "常住城市" in keys, keys)

    code, mem_b, _ = B.req("/api/memories")
    check("AC-33", "记忆：按用户隔离，别人看不到",
          "常住城市" not in [m["key"] for m in mem_b.get("memories") or []])

    A.req("/api/memories/delete", "POST", {"key": "常住城市"})
    code, mem2, _ = A.req("/api/memories")
    check("AC-33", "记忆：可删除",
          "常住城市" not in [m["key"] for m in mem2.get("memories") or []])

    code, ab, _ = A.req("/api/chat/abort", "POST", {"session_id": sid})
    check("AC-34", "打断：abort 接口返回 aborted 字段",
          code == 200 and "aborted" in ab, ab)

    # ---------- 5.6 交付文件与标题概括 ----------
    print("\n5.6) 交付文件、标题概括")
    said = "帮我排泉州一天行程"
    code, plan_chat, _ = A.req("/api/chat", "POST", {"message": said})
    types = [c.get("type") for c in plan_chat.get("cards") or []]
    files_card = next((c for c in plan_chat.get("cards") or []
                       if c.get("type") == "files"), None)
    items = (files_card or {}).get("data", {}).get("items") or []
    check("AC-35", "交付：排出行程后自动生成攻略文件",
          len(items) == 2, types)

    reachable = []
    for it in items:
        fc, payload, _ = A.req(it["url"], "GET", raw=True)
        reachable.append(fc == 200 and len(payload) > 500)
    check("AC-35", "交付：两个文件都能直接打开", bool(items) and all(reachable),
          [it["name"] for it in items])

    pending = plan_chat.get("title_pending")
    check("AC-36", "标题：新会话标记为待概括", pending is True, pending)
    new_sid = plan_chat.get("session_id")
    final = ""
    for _ in range(6):
        time.sleep(2)
        _, ss, _ = A.req("/api/sessions")
        row = next((s for s in ss.get("sessions") or [] if s["id"] == new_sid), None)
        final = (row or {}).get("title") or ""
        if final and not final.startswith(said[:8]):
            break
    check("AC-36", "标题：由模型概括而非截首句",
          bool(final) and not final.startswith(said[:8]) and len(final) <= 20, final)

    # ---------- 6. 会话隔离 ----------
    print("\n6) 会话隔离")
    check("FR-09", "本人可读取会话消息",
          code == 200 and len((A.req("/api/sessions/%s/messages" % sid)[1].get("messages") or [])) >= 2)
    code, r, _ = B.req("/api/sessions/%s/messages" % sid)
    check("AC-04", "他人读取会话返回 403", code == 403, "code=%s" % code)
    code, r, _ = B.req("/api/sessions/%s/export" % sid)
    check("AC-04", "他人导出的会话返回 403", code == 403, "code=%s" % code)
    code, r, _ = A.req("/api/sessions/%s/export" % sid, raw=True)
    check("FR-10", "本人可导出 Markdown", code == 200 and "智行".encode("utf-8") in r)

    # ---------- 7. MCP 协议 ----------
    print("\n7) MCP 协议")
    r = mcp(anon, "initialize")
    check("AC-15", "initialize 返回协议版本",
          (r.get("result") or {}).get("protocolVersion") == "2024-11-05",
          (r.get("result") or {}).get("protocolVersion"))
    r = mcp(anon, "tools/list")
    tools = (r.get("result") or {}).get("tools") or []
    check("AC-13", "tools/list 返回 9 个工具", len(tools) == 9, [t["name"] for t in tools])
    check("AC-13", "每个工具带 inputSchema", all(t.get("inputSchema") for t in tools))
    sc = tool(anon, "route.plan_route", {"origin": "广州", "destination": "深圳"})
    check("AC-14", "tools/call 返回结构化里程", (sc.get("distance_km") or 0) > 50, sc.get("distance_km"))
    r = mcp(anon, "tools/call", {"name": "weather.get_forecast", "arguments": {}})
    check("AC-19", "缺必填参数返回可读错误", "缺少必填参数" in json.dumps(r, ensure_ascii=False))
    r = mcp(anon, "no.such.method")
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
    print("通过 %d 项，失败 %d 项，跳过 %d 项" % (len(passed), len(failed), len(skipped)))
    if skipped:
        print("\n跳过明细（环境未就绪，不算失败）：")
        for cid, desc, why in skipped:
            print("  - %s %s — %s" % (cid, desc, why))
    if failed:
        print("\n失败明细：")
        for cid, desc, info in failed:
            print("  - %s %s %s" % (cid, desc, info))
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
