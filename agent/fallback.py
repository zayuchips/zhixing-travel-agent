# -*- coding: utf-8 -*-
"""降级链路：Pi 不可用时，用规则理解 + 直接调工具，保证「没有模型 Key 也能演示」。

主路径是 Pi（见 orchestrator）。这条路径存在的理由只有一个：
答辩现场模型额度挂了或没网，仍然能证明「工具是真调用的、数据是真的」。
所以它宁可说得朴素，也绝不编造景点、坐标或价格。

与 Pi 链路的输出结构完全一致：(reply, steps, cards)，前端不需要区分。
"""
from mcp import registry

from . import nlu


def respond(text):
    u = nlu.parse(text)
    intent, slots = u["intent"], u["slots"]
    steps, cards, lines = [], [], []

    if intent in ("chitchat", "empty"):
        return _help(), steps, cards

    missing = nlu.missing_slots(intent, slots)
    if missing:
        return _ask(missing, slots), steps, cards

    # 排行程且给了出发地时，先算城际路线再排（两工具编排）
    if intent == "plan" and slots.get("origin"):
        d = _call(steps, "route.plan_route", {
            "origin": slots["origin"], "destination": slots["destination"],
            "mode": slots.get("mode", "driving"),
        })
        if d:
            cards.append({"type": "route", "data": d})
            lines.append(_route_line(d))

    if intent == "weather":
        d = _call(steps, "weather.get_forecast", {
            "city": slots["destination"], "days": int(slots.get("days") or 3),
        })
        if d:
            cards.append({"type": "weather", "data": d})
            lines.append(_weather_text(d))
    elif intent == "route":
        d = _call(steps, "route.plan_route", {
            "origin": slots["origin"], "destination": slots["destination"],
            "mode": slots.get("mode", "driving"),
        })
        if d:
            cards.append({"type": "route", "data": d})
            lines.append(_route_text(d))
    elif intent == "plan":
        d = _call(steps, "itinerary.build_plan", {
            "destination": slots["destination"],
            "days": int(slots.get("days") or 2),
            "interests": slots.get("interests") or [],
            "budget": slots.get("budget"),
            "start_date": slots.get("start_date"),
        })
        if d:
            cards.append({"type": "itinerary", "data": d})
            lines.append(_plan_text(d))

    if not cards:
        return ("工具这次没有成功返回结果，我没拿到可用的真实数据，所以不给你编一个答案。\n"
                "可以稍后再试，或换个说法（比如「长沙天气」）。"), steps, cards

    steps.append({"title": "编排回复", "kind": "compose",
                  "detail": "把工具返回的结构化数据拼成可读结论，并附卡片"})
    return "\n\n".join(l for l in lines if l), steps, cards


def _call(steps, name, args):
    args = {k: v for k, v in args.items() if v not in (None, "", [])}
    result, elapsed = registry.call_tool(name, args)
    ok = not result.get("isError")
    data = result.get("structuredContent") if ok else None
    steps.append({
        "title": "调用工具 %s" % name, "kind": "tool", "tool": name,
        "arguments": args, "ok": ok, "elapsed_ms": int(elapsed * 1000),
        "summary": _summarize(name, data) if ok else (result.get("content") or [{}])[0].get("text", ""),
    })
    return data if ok else None


def _summarize(name, data):
    if not data:
        return ""
    if name == "weather.get_forecast":
        d = (data.get("days") or [{}])[0]
        return "%s：%s %s~%s℃ 降水 %s%%" % (data.get("city"), d.get("weather"),
                                          d.get("tmin"), d.get("tmax"), d.get("rain_prob"))
    if name == "route.plan_route":
        return "%s → %s：%s 公里" % (data.get("origin"), data.get("destination"),
                                   data.get("distance_km"))
    if name == "itinerary.build_plan":
        s = data.get("summary") or {}
        return "%s %s 天，%s 个景点，预计 %s 元" % (
            data.get("destination"), data.get("days_count"),
            sum(len(d.get("attractions") or []) for d in data.get("days") or []),
            s.get("total_cost"))
    return "已返回结构化结果"


def _dur(minutes):
    minutes = int(minutes or 0)
    if minutes < 60:
        return "%d 分钟" % minutes
    h, m = divmod(minutes, 60)
    return "%d 小时 %d 分" % (h, m) if m else "%d 小时" % h


def _route_line(d):
    est = "（路网服务不可用，按直线距离估算）" if d.get("estimated") else ""
    return "**%s → %s**：全程 %s 公里，%s约 %s%s。" % (
        d["origin"], d["destination"], d["distance_km"], d["mode_label"],
        _dur(d["duration_min"]), est)


def _route_text(d):
    out = [_route_line(d),
           "出行建议：%s；%s。" % (d.get("advice", ""), d.get("departure_hint", "")),
           "数据来源：%s。" % d.get("source")]
    return "\n".join(out)


def _weather_text(d):
    out = ["**%s**（%s）未来 %d 天预报，数据来自 open-meteo 实时接口：" % (
        d["city"], d.get("region") or "", len(d["days"]))]
    for w in d["days"]:
        out.append("• %s %s　%s　%s~%s℃　降水 %s%%　%s" % (
            w["date"], w.get("weekday", ""), w["weather"], w["tmin"], w["tmax"],
            w.get("rain_prob"), w.get("advice")))
    rainy = [w for w in d["days"] if w.get("rainy")]
    if rainy:
        out.append("这 %d 天里有 %d 天可能下雨，户外行程建议放在没雨的日子。" % (len(d["days"]), len(rainy)))
    return "\n".join(out)


def _plan_text(d):
    s = d.get("summary") or {}
    head = "**%s** %d 天行程" % (d["destination"], d["days_count"])
    if d.get("poi_source") == "local":
        head += "（点位来自本地示例数据，未接高德）"
    elif d.get("poi_source") == "template":
        head += "（该城市未收录，当前是通用框架）"
    if d.get("interests"):
        head += "，按「%s」优先" % "、".join(d["interests"])
    out = [head + "："]

    for day in d["days"]:
        w = ""
        if day.get("tmax") is not None:
            w = "　%s %s~%s℃" % (day.get("weather"), day.get("tmin"), day.get("tmax"))
        out.append("**D%d · %s %s**%s" % (day["index"], day["date"], day.get("weekday", ""), w))
        for a in day.get("attractions") or []:
            cost = "门票 %s 元" % a["ticket_price"] if a.get("ticket_price") else "免费/未标价"
            out.append("• %s %s（%s）" % (a["time"], a["name"], cost))
        meals = "、".join("%s %s" % (m["slot"], m["name"]) for m in day.get("meals") or [])
        if meals:
            out.append("• 三餐：%s" % meals)
        if day.get("hotel"):
            out.append("• 住宿：%s（约 %s 元/晚，估算）" % (
                day["hotel"]["name"], day["hotel"]["price_per_night"]))
        elif day.get("accommodation"):
            out.append("• 住宿：%s" % day["accommodation"])
        if day.get("note"):
            out.append("　› %s" % day["note"])

    out.append("**预算**：门票 %d + 住宿 %d + 餐饮 %d + 交通 %d = **%d 元**（人均每日约 %d 元）。" % (
        s.get("ticket_cost", 0), s.get("hotel_cost", 0), s.get("food_cost", 0),
        s.get("transport_cost", 0), s.get("total_cost", 0), s.get("per_day", 0)))
    if "budget_ok" in s:
        out.append("预算 %d 元：%s。" % (s["budget"], "够用" if s["budget_ok"]
                                        else "偏紧，建议砍掉收费景点或压缩一天"))
    for tip in (d.get("tips") or [])[:2]:
        out.append("› %s" % tip)
    return "\n".join(out)


def _ask(missing, slots):
    names = {"destination": "目的地城市", "origin": "出发城市"}
    need = "、".join(names.get(m, m) for m in missing)
    known = []
    if slots.get("days"):
        known.append("%d 天" % slots["days"])
    if slots.get("budget"):
        known.append("预算 %d 元" % slots["budget"])
    tail = "（已经记下：%s）" % "、".join(known) if known else ""
    return ("还差一个信息我才能动手：**%s**%s。\n"
            "比如「长沙未来三天天气」「从广州到长沙怎么走」「帮我排长沙 3 天行程，预算 1500，想逛博物馆和吃小吃」"
            % (need, tail))


def _help():
    return ("我是**智行**，一个能说也会做的出行助手。你跟我说人话，我去调真实接口干活：\n"
            "• **查天气**：长沙未来三天会不会下雨\n"
            "• **算路线**：从广州到长沙多远、开车多久\n"
            "• **排行程**：帮我排长沙 3 天行程，预算 1500，想逛博物馆和吃小吃\n"
            "天气、路线、地点都是实时数据，不是我编的。")
