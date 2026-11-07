# -*- coding: utf-8 -*-
"""MCP 工具六：hotel.search —— 目的地酒店检索。

高德 POI 不返回房价，所以价格是按档位估算的，返回值里 price_estimated 恒为 True。
宁可标「估算」，也不编一个看起来精确的数字。
"""
import math

from ..registry import ToolError, register
from .poi import search_places

LEVEL_PRICE = {"经济": 250, "舒适": 450, "豪华": 900}

_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "城市名"},
        "level": {"type": "string", "description": "住宿档次：经济/舒适/豪华，默认舒适"},
        "near_lng": {"type": "number", "description": "参考点经度，用于按距离排序"},
        "near_lat": {"type": "number", "description": "参考点纬度"},
        "limit": {"type": "integer", "description": "返回条数 1-10，默认 5"},
    },
    "required": ["city"],
}


def _distance_km(lng1, lat1, lng2, lat2):
    """球面距离，够用来排序，不做精确导航。"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)


@register("hotel.search",
          "检索目的地酒店，返回名称、地址、坐标、评分与每晚价格（按档位估算，非实时房价）。", _SCHEMA)
def search(city, level="舒适", near_lng=None, near_lat=None, limit=5):
    city = (city or "").strip()
    if not city:
        raise ToolError("city 不能为空")
    level = (level or "舒适").strip()
    if level not in LEVEL_PRICE:
        level = "舒适"
    try:
        limit = max(1, min(int(limit or 5), 10))
    except (TypeError, ValueError):
        limit = 5

    raw = search_places(city, "酒店", limit=max(limit * 2, 8))
    items = []
    for p in raw["items"]:
        name, typ = p.get("name") or "", p.get("type") or ""
        if "住宿" not in typ and "酒店" not in name:
            continue
        dist = None
        if near_lng is not None and near_lat is not None and p.get("lng") is not None:
            try:
                dist = _distance_km(float(near_lng), float(near_lat), p["lng"], p["lat"])
            except (TypeError, ValueError):
                dist = None
        items.append({
            "name": name, "address": p.get("address") or "",
            "lng": p.get("lng"), "lat": p.get("lat"), "coord": p.get("coord"),
            "rating": p.get("rating"), "level": level,
            "price_per_night": LEVEL_PRICE[level],
            "price_estimated": True,
            "distance_km": dist,
        })

    items.sort(key=lambda x: (x["distance_km"] if x["distance_km"] is not None else 999))
    return {
        "city": city, "level": level, "source": raw["source"],
        "price_note": "房价为按档位估算，非实时房价",
        "count": len(items[:limit]), "items": items[:limit],
    }
