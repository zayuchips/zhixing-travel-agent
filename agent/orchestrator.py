# -*- coding: utf-8 -*-
"""智能体编排层：把用户的话交给 Pi 引擎，再把 Pi 的事件流翻成前端能画的东西。

链路：
    用户输入 → Pi（自己决定调哪个工具、按什么顺序）→ MCP 工具 → 结构化结果
    → 这里把 tool_execution_* 翻成 steps、把 structuredContent 翻成 cards

为什么保留这一步而不是让前端直接连 Pi：
    Pi 说的是 JSONL 事件流，浏览器拿不到子进程；而且工具结果要落库、要按用户隔离。
    这一层就是「协议适配 + 落库」。
"""
import os
import time

import config
from . import checker, fallback, guide, memory, pi_bridge
from . import attachments as attach_mod

# 降级时附在回复末尾的说明。不用小字糊弄过去——用户必须知道这不是智能体在思考。
_NOTE = ("\n\n---\n> 当前为**降级模式**：由本地规则编排 + 真实工具调用生成，不是 Pi 智能体。"
         "配好模型凭据后会切回。")

# 模型凭据类硬错误（401/403）冷却一段时间，别让每轮对话都白等一次失败
_MODEL_DOWN_UNTIL = 0.0
_MODEL_DOWN_TTL = 120.0
_HARD_ERROR = ("401", "403", "unauthorized", "invalid api key", "forbidden", "not allowed")


def _model_down():
    return time.time() < _MODEL_DOWN_UNTIL


def _mark_model_down(msg):
    global _MODEL_DOWN_UNTIL
    if any(k in (msg or "").lower() for k in _HARD_ERROR):
        _MODEL_DOWN_UNTIL = time.time() + _MODEL_DOWN_TTL


def _dur(minutes):
    minutes = int(minutes or 0)
    if minutes < 60:
        return "%d 分钟" % minutes
    h, m = divmod(minutes, 60)
    return "%d 小时 %d 分" % (h, m) if m else "%d 小时" % h


def _engine():
    ok, reason = pi_bridge.available()
    return {
        "llm_enabled": ok,
        "model": config.PI_MODEL or "pi 内置默认",
        "provider": "pi",
        "label": "Pi agent harness（RPC）",
        "reason": reason,
    }


class Agent(object):
    # ---------------- 对外入口 ----------------
    def respond(self, text, history=None, session_id=None, user_id=None, stream=None, files=None):
        started = time.time()
        # 异步抽取长期记忆，立即返回，不拖慢本轮
        memory.extract_async(user_id, text)

        ok, reason = pi_bridge.available()
        if not ok:
            reply, steps, cards = fallback.respond(text)
            steps.insert(0, {"title": "降级模式", "kind": "warn",
                             "detail": "Pi 未就绪（%s），改用本地规则编排；工具与数据仍然真实" % reason})
            bundle = self._deliver(cards)
            if bundle:
                cards.append(bundle)
            return self._pack(reply + _NOTE, steps, cards, "fallback", {}, started)

        if _model_down():
            reply, steps, cards = fallback.respond(text)
            steps.insert(0, {"title": "降级模式", "kind": "warn",
                             "detail": "模型最近调用失败，冷却期内直接走本地规则编排"})
            bundle = self._deliver(cards)
            if bundle:
                cards.append(bundle)
            return self._pack(reply + _NOTE, steps, cards, "fallback", {}, started)

        steps, cards, chunks = [], [], []
        pending = {}
        state = {"msg": "", "aborted": False}
        poi_merged = {}   # 多次 poi 搜索合并成一张卡，否则界面上会刷一排同款卡片

        def add_poi(data):
            if not poi_merged:
                card = {"type": "poi", "data": {"city": data.get("city"), "keywords": "多个关键词",
                                                "items": [], "source": data.get("source"), "count": 0}}
                poi_merged.update({"card": card})
                cards.append(card)
            card = poi_merged["card"]
            seen = {i.get("name") for i in card["data"]["items"]}
            for it in data.get("items") or []:
                if it.get("name") and it["name"] not in seen:
                    card["data"]["items"].append(it)
                    seen.add(it["name"])
            card["data"]["items"] = card["data"]["items"][:12]
            card["data"]["count"] = len(card["data"]["items"])

        def on_event(rec):
            # 流式模式：把原始事件抛给调用方（server 转成 SSE），同步模式 stream 为 None
            if stream:
                try:
                    stream(rec)
                except Exception:  # noqa: BLE001 前端断开不能让后端崩
                    pass
            kind = rec.get("type")
            if kind == "message_update":
                ev = rec.get("assistantMessageEvent") or {}
                if ev.get("type") == "text_delta":
                    chunks.append(ev.get("delta") or "")
            elif kind == "tool_execution_start":
                name = pi_bridge.pretty(rec.get("toolName"))
                step = {
                    "title": "调用工具 %s" % name, "kind": "tool", "tool": name,
                    "arguments": rec.get("args") or {}, "ok": True,
                    "elapsed_ms": 0, "summary": "执行中…",
                }
                pending[rec.get("toolCallId")] = step
                steps.append(step)
            elif kind == "tool_execution_end":
                tid = rec.get("toolCallId")
                name = pi_bridge.pretty(rec.get("toolName"))
                is_err = bool(rec.get("isError"))
                step = pending.pop(tid, None)
                if step is None:
                    step = {"title": "调用工具 %s" % name, "kind": "tool", "tool": name,
                            "arguments": {}, "ok": not is_err, "elapsed_ms": 0, "summary": ""}
                    steps.append(step)
                step["ok"] = not is_err
                step["elapsed_ms"] = int(rec.get("durationMs") or 0)
                data = pi_bridge.payload(rec.get("result"))
                if is_err:
                    step["summary"] = self._err_text(rec.get("result"))
                elif data is not None:
                    ctype = pi_bridge.card_type(rec.get("toolName"))
                    if ctype == "poi":
                        add_poi(data)
                    elif ctype:
                        cards.append({"type": ctype, "data": data})
                    step["summary"] = self._summarize(name, data)
                else:
                    step["summary"] = "已返回结构化结果"
            elif kind == "message_end":
                msg = rec.get("message") or {}
                if msg.get("stopReason") == "error":
                    state["msg"] = msg.get("errorMessage") or "未知错误"
                    _mark_model_down(state["msg"])
                    steps.append({"title": "模型调用失败", "kind": "warn",
                                  "detail": state["msg"]})
                    # 一个字都没吐出来就报错，说明是凭据/额度问题，等重试只是白等
                    if not "".join(chunks).strip():
                        return False
            elif kind == "agent_settled":
                if rec.get("aborted"):
                    state["aborted"] = True

        try:
            sess = pi_bridge.session(session_id if session_id is not None else "anon")
            prompt = text

            # 附件先注入：图片已经让视觉模型描述过，文档已抽正文，这里只是一段文字
            att_ctx = attach_mod.build_context(files or [])
            if att_ctx:
                prompt = att_ctx + "\n\n[本轮请求]\n" + prompt
                steps.append({"title": "读取附件", "kind": "thought",
                              "detail": "注入 %d 个附件的内容" % len(files or [])})

            mem = memory.context_for(user_id)
            if mem:
                prompt = ("[我对你的长期了解，来自之前的对话]\n%s\n\n" % mem) + prompt
                steps.append({"title": "带上长期记忆", "kind": "thought",
                              "detail": "注入 %d 条用户档案" % len(mem.splitlines())})

            done, err = sess.ask(prompt, on_event)
        except Exception as e:  # noqa: BLE001 Pi 挂了也不能让 Web 应用跟着挂
            done, err = False, "%s: %s" % (type(e).__name__, e)

        reply = "".join(chunks).strip()

        # 用户按了两次 ESC。已经吐出来的字留住，别把半截答案也吞掉
        if state["aborted"]:
            steps.append({"title": "用户打断", "kind": "warn",
                          "detail": "收到两次 ESC，已中止本轮生成"})
            reply = (reply + "\n\n_（已打断）_") if reply else "已打断。你可以接着说，或者换个问法。"
            return self._pack(reply, steps, cards, "aborted", {}, started)

        if not done:
            steps.append({"title": "本轮未正常结束", "kind": "warn", "detail": err})

        if not reply:
            # 模型报错或进程异常退出，都退回本地规则编排，保证用户还能拿到结果
            fb_reply, fb_steps, fb_cards = fallback.respond(text)
            steps.extend(fb_steps)
            steps.insert(0, {"title": "降级模式", "kind": "warn",
                             "detail": state["msg"] or err or "Pi 没有返回内容"})
            bundle = self._deliver(fb_cards)
            if bundle:
                fb_cards.append(bundle)
            return self._pack(fb_reply + _NOTE, steps, fb_cards, "fallback", {}, started)

        steps.append({"title": "编排回复", "kind": "compose",
                      "detail": "把工具返回的结构化数据与模型总结一起返回前端"})

        # 行程复核：用干净上下文再查一遍约束，避免"自己排的自己说好"
        for card in cards:
            if card.get("type") != "itinerary":
                continue
            issues = checker.review(card["data"], text)
            if issues:
                steps.append({"title": "复核行程", "kind": "thought",
                              "detail": "独立视角发现 %d 条需要注意" % len(issues)})
                reply += "\n\n**自检**\n" + "\n".join(
                    "- %s %s" % ("⚠" if i["level"] == "warn" else "·", i["text"])
                    for i in issues)
            break

        # 交付物：排出行程就落地成文件，用户要的是能发给同伴的成品
        bundle = self._deliver(cards)
        if bundle:
            cards.append(bundle)
            steps.append({"title": "生成攻略文件", "kind": "deliver",
                          "detail": "HTML 与 docx 已生成，点卡片可直接打开或下载"})

        return self._pack(reply, steps, cards, "pi", {}, started)

    @staticmethod
    def _deliver(cards):
        """有行程卡片就渲染成独立攻略文件，返回一张文件卡片。

        放在这里而不是让用户手点导出：对话里给完结论就该有成品，
        多一步点击就多一次流失。
        """
        plan = next((c["data"] for c in cards if c.get("type") == "itinerary"), None)
        if not plan:
            return None
        items = []
        for kind, save in (("html", guide.save_html), ("docx", guide.save_docx)):
            try:
                url, path = save(plan)
            except Exception:  # noqa: BLE001 文件生成失败不该让整轮对话崩掉
                continue
            items.append({
                "kind": kind,
                "url": url,
                "name": os.path.basename(path),
                "size": os.path.getsize(path),
                "label": "网页版攻略" if kind == "html" else "Word 文档",
            })
        if not items:
            return None
        return {"type": "files", "data": {
            "destination": plan.get("destination"),
            "days": plan.get("days_count"),
            "items": items,
            "note": "排版好的完整攻略，可直接发给同伴；Word 版能自己改。",
        }}

    # ---------------- 文字与摘要 ----------------
    @staticmethod
    def _err_text(result):
        if isinstance(result, dict):
            for c in result.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "text":
                    return (c.get("text") or "")[:200]
        return "工具返回错误"

    @staticmethod
    def _summarize(name, data):
        if not isinstance(data, dict) or not data:
            return "已返回结果"
        if name == "weather.get_forecast":
            d = (data.get("days") or [{}])[0]
            return "%s：%s %s~%s℃ 降水 %s%%" % (
                data.get("city"), d.get("weather"), d.get("tmin"), d.get("tmax"), d.get("rain_prob"))
        if name == "route.plan_route":
            return "%s → %s：%s 公里 / %s" % (
                data.get("origin"), data.get("destination"),
                data.get("distance_km"), _dur(data.get("duration_min")))
        if name == "itinerary.build_plan":
            s = data.get("summary") or {}
            return "%s %s 天，%s 个景点，预计 %s 元（来源 %s）" % (
                data.get("destination"), data.get("days_count"),
                sum(len(d.get("attractions") or []) for d in data.get("days") or []),
                s.get("total_cost"), data.get("poi_source"))
        if name == "hotel.search":
            items = data.get("items") or []
            if not items:
                return "%s：没有检索到酒店（来源 %s）" % (data.get("city"), data.get("source"))
            return "%s：%s %s 元/晚（估算）" % (
                data.get("city"), items[0].get("name"), items[0].get("price_per_night"))
        if name == "poi.search_places":
            return "%s「%s」找到 %s 个地点（来源 %s）" % (
                data.get("city"), data.get("keywords"), data.get("count"), data.get("source"))
        if name == "poi.get_detail":
            bits = []
            if data.get("rating"):
                bits.append("★" + str(data["rating"]))
            if data.get("open_time"):
                bits.append("营业 " + data["open_time"])
            if data.get("cost"):
                bits.append("人均 %s 元" % data["cost"])
            return "%s %s" % (data.get("name") or "地点", " · ".join(bits) or "已取到详情")
        return "已返回结构化结果"

    @staticmethod
    def _pack(reply, steps, cards, intent, slots, started):
        return {
            "reply": reply, "steps": steps, "cards": cards,
            "intent": intent, "slots": slots,
            "elapsed_ms": int((time.time() - started) * 1000),
            "engine": _engine(),
        }
