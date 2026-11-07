# -*- coding: utf-8 -*-
"""复核 Agent：行程排完后，换一个干净视角检查约束有没有真的满足。

为什么要独立跑一次，而不是让主会话自己复查：
主会话的模型已经「认领」了这份行程，让它自查容易自我确认（说「都符合」）。
这里只给它约束和结果，不问它怎么排的，它才挑得出毛病。

它不改行程，只输出问题清单——自动改错比不改更糟。
"""
import json
import re
import urllib.request

import config

_SYS = """你是行程审核员。只做一件事：检查这份行程有没有违反用户的明确要求。

只报真问题，不要提改进建议，不要复述行程。
每个问题一句话，说清「哪天/哪项 + 什么问题」。

输出 JSON 数组，每项 {"level": "warn" 或 "info", "text": "..."}。
warn 用于违反明确要求（天数不对、预算超、某天没安排、想去的地方没去、雨天排户外）。
info 用于值得提醒但不违规（比如住宿是估算值）。
没有问题就输出 []。只输出 JSON。"""


def _digest(plan):
    """把行程压成紧凑摘要，省 token。"""
    s = plan.get("summary") or {}
    lines = ["目的地：%s｜天数：%s｜日期：%s 起" % (
        plan.get("destination"), plan.get("days_count"), plan.get("start_date"))]
    for day in plan.get("days") or []:
        names = [a.get("name") for a in day.get("attractions") or []]
        rain = "有雨(%s%%)" % day.get("rain_prob") if (day.get("rain_prob") or 0) >= 60 else "无雨"
        hotel = (day.get("hotel") or {}).get("name") or day.get("accommodation") or "—"
        lines.append("D%s %s %s：景点 %s；住宿 %s" % (
            day.get("index"), day.get("date"), rain, "、".join(names) or "（空）", hotel))
    lines.append("预算：合计 %s（门票 %s / 住宿 %s / 餐饮 %s / 交通 %s），用户预算 %s" % (
        s.get("total_cost"), s.get("ticket_cost"), s.get("hotel_cost"),
        s.get("food_cost"), s.get("transport_cost"), s.get("budget") or "未给"))
    return "\n".join(lines)


def review(plan, user_text):
    """返回问题列表；失败就返回空（复核是加分项，不能影响主流程）。"""
    if not (config.LLM_API_KEY and config.LLM_BASE_URL and config.LLM_MODEL):
        return []
    if not plan or not plan.get("days"):
        return []
    try:
        return _call(user_text, _digest(plan))
    except Exception:  # noqa: BLE001
        return []


def _call(user_text, digest):
    body = json.dumps({
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": _SYS},
            {"role": "user", "content": "用户原话：%s\n\n行程：\n%s" % (user_text, digest)},
        ],
        "temperature": 0,
        "max_tokens": 400,
    }).encode("utf-8")

    req = urllib.request.Request(
        config.LLM_BASE_URL.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + config.LLM_API_KEY,
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read().decode())
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        return []
    items = json.loads(m.group())
    if not isinstance(items, list):
        return []
    out = []
    for it in items[:4]:
        text = str(it.get("text") or "").strip()
        if text and len(text) <= 80:
            out.append({"level": "warn" if it.get("level") == "warn" else "info", "text": text})
    return out
