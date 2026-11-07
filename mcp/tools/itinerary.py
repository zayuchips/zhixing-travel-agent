# -*- coding: utf-8 -*-
"""MCP 工具四：itinerary.build_plan —— 按天数 / 预算 / 兴趣排出行程表。

这是本项目的「杀手级」工具：它不是把 POI 随机拼起来，而是
    1) 按兴趣给点位打分（美食/历史/自然/夜景/亲子…）
    2) 每 3 个点位按「上午-午餐-下午-晚上」排进一天
    3) 读到未来天气后，把雨天的户外点位替换成室内点位
    4) 算出门票 + 餐饮的总花费，和用户预算对比
"""
import datetime
import json
import os

import config
from .. import net  # noqa: F401  （保证包初始化顺序）
from ..registry import ToolError, register
from .weather import get_forecast

INTEREST_CATS = {
    "美食": ["美食"],
    "历史": ["古迹", "博物馆"],
    "博物馆": ["博物馆", "古迹"],
    "文化": ["博物馆", "古迹"],
    "自然": ["自然"],
    "爬山": ["自然"],
    "夜景": ["夜游", "地标"],
    "夜游": ["夜游"],
    "购物": ["购物", "夜游"],
    "亲子": ["亲子", "自然"],
    "遛娃": ["亲子", "自然"],
    "拍照": ["地标", "古迹", "自然"],
    "打卡": ["地标", "夜游"],
}
SLOTS = [("09:00", "上午"), ("12:00", "午餐"), ("14:30", "下午"), ("19:00", "晚上")]
FOOD_COST_PER_DAY = 90
HOTEL_COST_PER_NIGHT = 260

_SCHEMA = {
    "type": "object",
    "properties": {
        "destination": {"type": "string", "description": "目的地城市"},
        "days": {"type": "integer", "description": "游玩天数 1-7，默认 2"},
        "interests": {"type": "array", "items": {"type": "string"},
                      "description": "兴趣标签，如 美食/历史/自然/夜景/亲子/拍照"},
        "budget": {"type": "number", "description": "总预算（元），可不填"},
        "start_date": {"type": "string", "description": "出发日期 YYYY-MM-DD，默认明天"},
    },
    "required": ["destination"],
}

_CITIES = None


def _cities():
    global _CITIES
    if _CITIES is None:
        path = os.path.join(config.DATA_DIR, "cities.json")
        with open(path, "r", encoding="utf-8") as f:
            _CITIES = json.load(f)
    return _CITIES


def match_city(name):
    """城市名 / 别名 -> 标准城市 key；匹配不到返回 None。"""
    if not name:
        return None
    name = name.strip()
    for city, info in _cities().items():
        if name == city or city in name or name in city:
            return city
        for alias in info.get("aliases", []):
            if alias and alias.lower() == name.lower():
                return city
    return None


def _score(poi, cats):
    return 2 if poi["cat"] in cats else 0


@register("itinerary.build_plan",
          "根据目的地、天数、兴趣与预算生成逐日行程表（含天气提示、门票餐饮花费估算），返回结构化行程卡片。", _SCHEMA)
def build_plan(destination, days=2, interests=None, budget=None, start_date=None):
    try:
        days = max(1, min(int(days or 2), 7))
    except (TypeError, ValueError):
        days = 2
    interests = [str(i) for i in (interests or [])]
    cats = []
    for i in interests:
        cats.extend(INTEREST_CATS.get(i, []))

    city = match_city(destination)
    generic = False
    if city is None:
        # 未收录城市：给通用框架，并明确告知用户（不假装有精选点位）
        city = (destination or "").strip() or "目的地"
        generic = True
        pois = [
            {"name": "%s 老城区步行街" % city, "cat": "夜游", "indoor": False, "hours": 2, "cost": 0, "tip": "先摸清城市气质"},
            {"name": "%s 市博物馆" % city, "cat": "博物馆", "indoor": True, "hours": 2.5, "cost": 0, "tip": "免费为主，周一闭馆"},
            {"name": "%s 城市地标观景点" % city, "cat": "地标", "indoor": False, "hours": 2, "cost": 0, "tip": "傍晚去，看日落"},
            {"name": "%s 本地美食街" % city, "cat": "美食", "indoor": False, "hours": 1.5, "cost": 60, "tip": "避开景区门口"},
            {"name": "%s 城市公园" % city, "cat": "自然", "indoor": False, "hours": 2, "cost": 0, "tip": "适合清晨散步"},
            {"name": "%s 特色街区" % city, "cat": "购物", "indoor": True, "hours": 2, "cost": 0, "tip": "买伴手礼"},
        ]
        info = {"tagline": "通用行程框架", "foods": ["当地特色小吃"], "pois": pois}
    else:
        info = _cities()[city]
        pois = list(info["pois"])

    try:
        start = datetime.date.fromisoformat((start_date or "").strip())
    except ValueError:
        start = datetime.date.today() + datetime.timedelta(days=1)

    # 天气（拿不到不影响主流程）；覆盖到行程最后一天
    weather = {}
    if not generic:
        span = max(7, min((start - datetime.date.today()).days + days, 16))
        try:
            for w in get_forecast(city, span).get("days") or []:
                weather[w["date"]] = w
        except Exception:  # noqa: BLE001 天气失败不影响排行程
            weather = {}

    # 兴趣优先排序，其余按原顺序补位
    ranked = sorted(pois, key=lambda p: -_score(p, cats))
    used = set()
    plan_days = []
    foods = info.get("foods") or ["当地特色小吃"]

    for d in range(days):
        date = start + datetime.timedelta(days=d)
        ds = date.isoformat()
        w = weather.get(ds) or {}
        rainy = bool(w.get("rainy"))
        items = []
        outdoor_fallback = False
        flex_added = False
        for idx, (clock, slot) in enumerate(SLOTS):
            if slot == "午餐":
                items.append({
                    "time": clock, "slot": slot,
                    "name": "尝本地菜：%s" % foods[d % len(foods)],
                    "cat": "美食", "cost": 45, "hours": 1,
                    "tip": "避开景区门口，往居民区走 300 米",
                })
                continue
            # 每天上午/下午/晚上各取一个未用过的点位；雨天优先室内
            pick = None
            for p in ranked:
                if p["name"] in used:
                    continue
                if rainy and not p.get("indoor"):
                    continue
                pick = p
                break
            if pick is None:
                for p in ranked:
                    if p["name"] not in used:
                        pick = p
                        break
                if pick is not None and rainy and not pick.get("indoor"):
                    outdoor_fallback = True
            if pick is None:
                # 精选点位用完了：留一段机动时间，而不是硬凑一个假的景点
                if flex_added:
                    continue
                flex_added = True
                items.append({
                    "time": clock, "slot": slot,
                    "name": "自由活动 / 返程准备", "cat": "机动",
                    "cost": 0, "hours": 3,
                    "tip": "精选点位已排完，这段留给你临时发现的地方或休息",
                })
                continue
            used.add(pick["name"])
            items.append({
                "time": clock, "slot": slot, "name": pick["name"], "cat": pick["cat"],
                "cost": pick.get("cost", 0), "hours": pick.get("hours", 2), "tip": pick.get("tip", ""),
            })
        theme = "雨天室内为主" if rainy else ("兴趣优先" if cats else "经典路线")
        if rainy:
            note = ("当天有雨，室内点位已排完，剩余点位请按天气临时取舍" if outdoor_fallback
                    else "当天有雨，已把户外点位换成室内")
        else:
            note = "行程较松，可按体力加一两个点位"
        plan_days.append({
            "index": d + 1, "date": ds,
            "weekday": w.get("weekday") or ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][date.weekday()],
            "weather": w.get("weather") or "（暂无预报）",
            "tmax": w.get("tmax"), "tmin": w.get("tmin"), "rain_prob": w.get("rain_prob"),
            "theme": theme, "items": items, "note": note,
        })

    ticket = sum(it["cost"] for d in plan_days for it in d["items"] if it["cat"] != "美食")
    food = FOOD_COST_PER_DAY * days + sum(45 for d in plan_days for it in d["items"] if it["slot"] == "午餐")
    hotel = HOTEL_COST_PER_NIGHT * max(0, days - 1)
    total = ticket + food + hotel
    summary = {
        "ticket_cost": ticket, "food_cost": food, "hotel_cost": hotel, "total_cost": total,
        "currency": "CNY", "per_day": int(round(total / float(days))),
    }
    tips = ["门票与预约：博物馆类多为免费但需提前 1-3 天实名预约"]
    if generic:
        tips.insert(0, "「%s」暂未收录精选手工点位，当前为通用框架，建议补充当地攻略后微调" % city)
    if budget:
        try:
            b = float(budget)
            summary["budget"] = b
            summary["budget_ok"] = total <= b
        except (TypeError, ValueError):
            pass

    return {
        "destination": city, "matched": not generic, "tagline": info.get("tagline"),
        "start_date": start.isoformat(), "days_count": days,
        "interests": interests, "days": plan_days, "summary": summary, "tips": tips,
        "weather_source": "open-meteo.com" if weather else "未取到预报",
    }
