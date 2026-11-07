# -*- coding: utf-8 -*-
"""智能体编排层：把「说」和「做」串起来。

一次对话的固定链路（每一步都会记进 steps，前端「思考过程」面板原样展示）：

    解析意图/槽位  →  校验缺什么  →  调用 MCP 工具  →  把结构化结果编排成话  →  落库

为什么要展示 steps：
    智能体最容易骗人的地方是「看起来回答了」。把工具名、参数、耗时、真实返回值
    摆出来，用户才能判断这句结论是查来的还是编的。这是本项目的产品主张。
"""
import time

import llm
from . import nlu
from mcp import registry
from mcp.tools import register_all

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _dur(minutes):
    minutes = int(minutes or 0)
    if minutes < 60:
        return "%d 分钟" % minutes
    h, m = divmod(minutes, 60)
    return "%d 小时 %d 分" % (h, m) if m else "%d 小时" % h


class Agent(object):
    def __init__(self):
        register_all()

    # ---------------- 对外入口 ----------------
    def respond(self, text, history=None):
        started = time.time()
        steps = []

        u = self._understand(text, steps)
        intent, slots = u["intent"], u["slots"]

        if intent in ("chitchat", "empty"):
            reply = self._reply_help()
            steps.append({"title": "不需要工具", "kind": "thought",
                          "detail": "这句话没有出行意图，直接给出能力说明"})
            return self._pack(reply, steps, [], intent, slots, started)

        missing = nlu.missing_slots(intent, slots)
        if missing:
            reply = self._ask_for(intent, missing, slots)
            steps.append({"title": "信息不足", "kind": "thought",
                          "detail": "缺少 %s，向用户追问而不是瞎猜" % "、".join(missing)})
            return self._pack(reply, steps, [], intent, slots, started)

        cards = []
        lines = []

        # 多工具编排：既有出发地又排行程时，先算路线再排行程
        if intent == "plan" and slots.get("origin"):
            data = self._call(steps, "route.plan_route", {
                "origin": slots["origin"], "destination": slots["destination"],
                "mode": slots.get("mode", "driving"),
            })
            if data:
                cards.append({"type": "route", "data": data})
                lines.append(self._route_sentence(data))

        if intent == "weather":
            data = self._call(steps, "weather.get_forecast", {
                "city": slots["destination"], "days": int(slots.get("days") or 3),
            })
            if data:
                cards.append({"type": "weather", "data": data})
                lines.append(self._weather_text(data))
        elif intent == "route":
            data = self._call(steps, "route.plan_route", {
                "origin": slots["origin"], "destination": slots["destination"],
                "mode": slots.get("mode", "driving"),
            })
            if data:
                cards.append({"type": "route", "data": data})
                lines.append(self._route_text(data))
        elif intent == "plan":
            data = self._call(steps, "itinerary.build_plan", {
                "destination": slots["destination"],
                "days": int(slots.get("days") or 2),
                "interests": slots.get("interests") or [],
                "budget": slots.get("budget"),
                "start_date": slots.get("start_date"),
            })
            if data:
                cards.append({"type": "itinerary", "data": data})
                lines.append(self._plan_text(data))

        if not cards:
            reply = ("工具这次没有成功返回结果，我没有拿到可用的真实数据，"
                     "所以不给你编一个答案。可以稍后再试，或换个说法（比如「长沙天气」）。")
            return self._pack(reply, steps, [], intent, slots, started)

        steps.append({"title": "编排回复", "kind": "compose",
                      "detail": "把工具返回的结构化数据写成可读结论，并把卡片一起返回前端"})
        reply = "\n\n".join([l for l in lines if l])
        reply = self._maybe_polish(reply, steps)
        return self._pack(reply, steps, cards, intent, slots, started)

    # ---------------- 理解 ----------------
    def _understand(self, text, steps):
        rule = nlu.parse(text)
        provider = llm.get_provider()
        source = "rules"
        if provider.name != "demo":
            try:
                prompt = (
                    "你是出行助手的理解模块。把用户的话解析成 JSON，字段：\n"
                    "intent: weather|route|plan|chitchat\n"
                    "destination/origin: 城市名；days: 整数 1-7；budget: 数字；\n"
                    "interests: 数组，取值 美食/历史/博物馆/自然/夜景/购物/亲子/拍照；\n"
                    "start_date: YYYY-MM-DD；mode: driving|walking|cycling\n"
                    "只输出 JSON。今天日期 %s。" % time.strftime("%Y-%m-%d")
                )
                got = provider.extract_json(prompt, text)
                if isinstance(got, dict):
                    intent = got.get("intent") or rule["intent"]
                    slots = dict(rule["slots"])
                    for k in ("destination", "origin", "days", "budget", "start_date", "mode", "interests"):
                        v = got.get(k)
                        if v not in (None, "", [], "null"):
                            slots[k] = v
                    rule = {"intent": intent, "slots": slots, "source": provider.label}
                    source = provider.label
            except Exception as e:  # noqa: BLE001  模型出错不能让对话挂掉
                steps.append({"title": "大模型不可用，已回退规则引擎", "kind": "warn",
                              "detail": "%s：%s" % (type(e).__name__, e)})
                source = "rules（回退）"

        steps.append({
            "title": "理解这句话", "kind": "thought",
            "detail": "意图=%s；槽位=%s；来源=%s" % (
                rule["intent"],
                "、".join("%s=%s" % (k, v) for k, v in rule["slots"].items()) or "空",
                source),
        })
        return rule

    # ---------------- 工具调用 ----------------
    def _call(self, steps, name, args):
        args = {k: v for k, v in args.items() if v not in (None, "", [])}
        result, elapsed = registry.call_tool(name, args)
        ok = not result.get("isError")
        data = result.get("structuredContent") if ok else None
        summary = self._summarize(name, data) if ok else (result.get("content") or [{}])[0].get("text", "")
        steps.append({
            "title": "调用工具 %s" % name, "kind": "tool", "tool": name,
            "arguments": args, "ok": ok, "elapsed_ms": int(elapsed * 1000),
            "summary": summary,
        })
        return data if ok else None

    @staticmethod
    def _summarize(name, data):
        if not data:
            return ""
        if name == "weather.get_forecast":
            d = (data.get("days") or [{}])[0]
            return "%s：%s %s~%s℃ 降水 %s%%" % (data.get("city"), d.get("weather"),
                                              d.get("tmin"), d.get("tmax"), d.get("rain_prob"))
        if name == "route.plan_route":
            return "%s → %s：%s 公里 / %s" % (data.get("origin"), data.get("destination"),
                                            data.get("distance_km"), _dur(data.get("duration_min")))
        if name == "itinerary.build_plan":
            return "%s %s 天，%s 个点位，预计 %s 元" % (
                data.get("destination"), data.get("days_count"),
                sum(len(d.get("items") or []) for d in data.get("days") or []),
                (data.get("summary") or {}).get("total_cost"))
        return "已返回结构化结果"

    # ---------------- 表达 ----------------
    def _route_sentence(self, d):
        est = "（路网服务不可用，按直线距离估算）" if d.get("estimated") else ""
        return "**%s → %s**：全程 %s 公里，%s约 %s%s。" % (
            d["origin"], d["destination"], d["distance_km"], d["mode_label"],
            _dur(d["duration_min"]), est)

    def _weather_text(self, d):
        out = ["**%s**（%s）未来 %d 天的预报，数据来自 open-meteo 实时接口：" % (
            d["city"], d.get("region") or "", len(d["days"]))]
        for w in d["days"]:
            out.append("• %s %s　%s　%s~%s℃　降水 %s%%　%s" % (
                w["date"], w.get("weekday", ""), w["weather"], w["tmin"], w["tmax"],
                w.get("rain_prob"), w.get("advice")))
        rain_days = [w for w in d["days"] if w.get("rainy")]
        if rain_days:
            out.append("这 %d 天里有 %d 天可能下雨，户外行程建议放在没雨的日子，或准备室内备选。" % (
                len(d["days"]), len(rain_days)))
        else:
            out.append("这几天降水概率都不高，适合安排户外行程。")
        return "\n".join(out)

    def _route_text(self, d):
        out = [self._route_sentence(d),
               "出行建议：%s；%s。" % (d.get("advice", ""), d.get("departure_hint", "")),
               "数据来源：%s。" % d.get("source")]
        if d.get("mode") == "driving":
            out.insert(1, "直线距离 %s 公里，实际路网 %s 公里。" % (d["straight_km"], d["distance_km"]))
        return "\n".join(out)

    def _plan_text(self, d):
        s = d.get("summary") or {}
        head = "**%s**（%s）%d 天行程" % (d["destination"], d.get("tagline") or "", d["days_count"])
        if d.get("interests"):
            head += "，按你的兴趣「%s」优先排" % "、".join(d["interests"])
        out = [head + "："]
        for day in d["days"]:
            weather = ""
            if day.get("tmax") is not None:
                weather = "　%s %s~%s℃" % (day.get("weather"), day.get("tmin"), day.get("tmax"))
            out.append("**D%s · %s %s**%s　%s" % (day["index"], day["date"], day.get("weekday", ""), weather, day.get("theme", "")))
            for it in day["items"]:
                cost = "免费" if not it.get("cost") else "%s 元" % it["cost"]
                out.append("• %s %s（%s，约 %s 小时，%s）%s" % (
                    it["time"], it["name"], it["cat"], it.get("hours"), cost,
                    ("　" + it["tip"]) if it.get("tip") else ""))
            if day.get("note"):
                out.append("　› %s" % day["note"])
        out.append("**花费估算**：门票 %d + 餐饮 %d + 住宿 %d = **%d 元**（人均每日约 %d 元）。" % (
            s.get("ticket_cost", 0), s.get("food_cost", 0), s.get("hotel_cost", 0),
            s.get("total_cost", 0), s.get("per_day", 0)))
        if "budget_ok" in s:
            out.append("预算 %d 元：%s。" % (s["budget"], "够用" if s["budget_ok"] else "偏紧，建议砍掉收费景点或压缩一天"))
        for tip in (d.get("tips") or [])[:3]:
            out.append("› %s" % tip)
        return "\n".join(out)

    def _ask_for(self, intent, missing, slots):
        names = {"destination": "目的地城市", "origin": "出发城市"}
        need = "、".join(names.get(m, m) for m in missing)
        known = []
        if slots.get("days"):
            known.append("%d 天" % slots["days"])
        if slots.get("budget"):
            known.append("预算 %d 元" % slots["budget"])
        tail = "（已经记下：%s）" % "、".join(known) if known else ""
        example = "比如「长沙未来三天天气」「从广州到长沙怎么走」「帮我排长沙 3 天行程，预算 1500，想逛博物馆和吃小吃」"
        return "还差一个信息我才能动手：**%s**%s。\n%s" % (need, tail, example)

    def _reply_help(self):
        return ("我是**智行**，一个能说也会做的出行助手。你跟我说人话，我去调真实接口干活：\n"
                "• **查天气**：长沙未来三天会不会下雨\n"
                "• **算路线**：从广州到长沙多远、开车多久\n"
                "• **排行程**：帮我排长沙 3 天行程，预算 1500，想逛博物馆和吃小吃\n"
                "天气和路线都是实时数据（open-meteo / OSRM），不是我编的。")

    def _maybe_polish(self, reply, steps):
        """接了大模型时，让它把语气改得更自然；失败就保持原文，不冒险。"""
        provider = llm.get_provider()
        if provider.name == "demo":
            return reply
        try:
            polished = provider.chat(
                "你是出行助手的表达模块。把下面的结果改写成自然、口语化的中文，"
                "保持所有数字、日期、地名、金额完全不变，不要新增任何事实。直接输出正文。",
                [{"role": "user", "content": reply}], temperature=0.2)
            if polished and len(polished) > 20:
                steps.append({"title": "润色表达", "kind": "compose", "detail": "由 %s 改写语气，事实未变" % provider.label})
                return polished.strip()
        except Exception as e:  # noqa: BLE001
            steps.append({"title": "润色失败，使用原文", "kind": "warn", "detail": str(e)})
        return reply

    @staticmethod
    def _pack(reply, steps, cards, intent, slots, started):
        return {
            "reply": reply, "steps": steps, "cards": cards,
            "intent": intent, "slots": slots,
            "elapsed_ms": int((time.time() - started) * 1000),
            "engine": llm.status(),
        }
