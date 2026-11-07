# -*- coding: utf-8 -*-
"""专家会诊：把一次「深度规划」拆成 4 个角色，各自独立出意见。

和上一版「复核 Agent」的区别：
    复核只有 1 个角色，在行程排完之后挑错。
    会诊是在排行程之前开会——景点专家先说该去哪些、天气专家说哪天不适宜、
    酒店专家说住哪一片、规划师最后串成路线。角色之间互相独立，不共享上下文。

调用方是主智能体（Pi）。它看到工具描述，自己判断用户是不是要「完整攻略/深度规划」，
要才调；日常问一句天气不会走这条重路径。

为什么不用 4 个 Pi 子进程：Pi 子进程会再拉起 MCP server，形成递归。
这里每个专家就是「一次 LLM 调用 + 一个角色提示词」，数据由 Python 先查好喂进去，
既便宜又不递归。
"""
import json
import urllib.request

import config
from ..registry import ToolError, register
from . import hotel as hotel_tool
from . import poi as poi_tool
from . import weather as weather_tool

_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "目的地城市"},
        "days": {"type": "integer", "description": "计划游玩天数"},
        "interests": {"type": "array", "items": {"type": "string"},
                      "description": "用户偏好，如 古建筑/美食/博物馆"},
        "budget": {"type": "number", "description": "总预算（元），可不填"},
        "origin": {"type": "string", "description": "出发城市，可省略"},
    },
    "required": ["city", "days"],
}

ROLES = [
    ("景点专家", """你是资深景点顾问。基于给定的候选地点，挑出最值得去的，并说明理由。
输出要求：3-6 条要点，每条一行，格式「地点名 —— 一句话理由（建议时长）」。
只从候选里挑，不要编造不存在的地方。若候选里有明显不适合的（如纯住宅区、加油站），直接说排除。"""),

    ("天气专家", """你是出行天气顾问。基于给出的逐日预报，判断哪天适合户外、哪天该安排室内，
并给出穿衣与携带建议。
输出要求：3-5 条要点，每条一行，带上具体日期和温度。不要泛泛而谈。"""),

    ("酒店专家", """你是住宿顾问。基于给定的酒店候选，推荐住哪一片区域、选哪一档，
并说明理由（位置/价格/评分）。
输出要求：2-4 条要点，每条一行。价格一律注明是估算值。信息缺口要如实说。"""),
]

CONCURRENCY = 3


def _ask(role, system, context):
    if not (config.LLM_API_KEY and config.LLM_BASE_URL):
        raise ToolError("没配模型 Key，专家会诊开不了")
    body = json.dumps({
        "model": config.LLM_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": context}],
        "temperature": 0.3,
        # 这个模型是推理型：思考也计 token。给少了会出现
        # 「finish_reason=length、content 为空」的假沉默，所以留足余量
        "max_tokens": 3000,
    }).encode("utf-8")
    req = urllib.request.Request(
        config.LLM_BASE_URL.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + config.LLM_API_KEY,
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return text.strip()


def _context(city, days, interests, budget, pois, weather, hotels):
    lines = ["目的地：%s　天数：%s 天　偏好：%s　预算：%s" % (
        city, days, "、".join(interests) if interests else "无特别偏好",
        ("%s 元" % budget) if budget else "未指定")]

    lines.append("\n[候选景点 %d 个]" % len(pois))
    for p in pois[:10]:
        bits = [p.get("name") or "", (p.get("type") or "").split(";")[-1]]
        if p.get("rating"):
            bits.append("评分 %s" % p["rating"])
        if p.get("address"):
            bits.append(p["address"])
        lines.append("- " + "　".join([b for b in bits if b]))

    if weather:
        lines.append("\n[逐日天气]")
        for w in weather[:7]:
            lines.append("- %s %s：%s，%s~%s℃，降水 %s%%" % (
                w.get("date"), w.get("weekday") or "", w.get("weather"),
                w.get("tmin"), w.get("tmax"), w.get("rain_prob")))

    if hotels:
        lines.append("\n[酒店候选（价格为按档位估算，非实时房价）]")
        for h in hotels[:5]:
            lines.append("- %s　评分 %s　约 %s 元/晚　%s" % (
                h.get("name"), h.get("rating") or "无", h.get("price_per_night"),
                ("距首个景点 %s km" % h["distance_km"]) if h.get("distance_km") is not None else ""))

    lines.append("\n请只基于以上信息作答。信息不足就直接说缺什么，不要编。")
    return "\n".join(lines)


@register("expert.convene",
          "召集景点、天气、酒店三位专家并行会诊，各自给出独立意见（排程仍由 itinerary 工具完成）。"
          "适用于用户要「完整攻略」「深度规划」「认真安排」这类需要多角度权衡的请求；"
          "日常单点提问（今天天气、多远）不要用这个，太慢。", _SCHEMA)
def convene(city, days, interests=None, budget=None, origin=None):
    city = (city or "").strip()
    if not city:
        raise ToolError("city 不能为空")
    try:
        days = max(1, min(int(days or 2), 15))
    except (TypeError, ValueError):
        days = 2
    interests = [str(i) for i in (interests or [])]

    # ---- 先把数据查齐，别让专家去猜 ----
    pois, seen = [], set()
    kws = ["景点"] + interests[:2]
    for kw in kws:
        try:
            r = poi_tool.search_places(city, kw, limit=12)
        except ToolError:
            continue
        for it in r.get("items") or []:
            key = it.get("id") or it.get("name")
            if key and key not in seen and it.get("name"):
                seen.add(key)
                pois.append(it)

    if not pois:
        raise ToolError("「%s」没搜到可用点位，专家会诊没有素材（检查 AMAP_KEY）" % city)

    weather = []
    try:
        weather = weather_tool.get_forecast(city, days).get("days") or []
    except ToolError:
        pass

    hotels = []
    try:
        anchor = next((p for p in pois if p.get("lng") is not None), None)
        args = {"city": city, "level": "舒适", "limit": 5}
        if anchor:
            args["near_lng"], args["near_lat"] = anchor["lng"], anchor["lat"]
        hotels = hotel_tool.search(**args).get("items") or []
    except ToolError:
        pass

    ctx = _context(city, days, interests, budget, pois, weather, hotels)

    # ---- 三位专家互不依赖，并行发言；顺序乱了也没关系，按 ROLES 顺序整理输出 ----
    from concurrent.futures import ThreadPoolExecutor

    def one(item):
        role, system = item
        try:
            return role, _ask(role, system, ctx)
        except Exception as e:  # noqa: BLE001 单个专家挂掉不影响其他人
            return role, "（这位专家没发言成功：%s）" % type(e).__name__

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        results = list(pool.map(one, ROLES))

    opinions = [{"role": r, "text": t} for r, t in results if t]

    return {
        "city": city, "days": days,
        "interests": interests, "budget": budget,
        "poi_count": len(pois), "weather_days": len(weather), "hotel_count": len(hotels),
        "experts": opinions,
        "note": "三位专家并行发言，意见可能不一致；行程以 itinerary 卡片为准，冲突处按实际情况取舍。",
    }
