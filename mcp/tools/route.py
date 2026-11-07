# -*- coding: utf-8 -*-
"""MCP 工具三：route.plan_route —— 两地之间真实里程与耗时（OSRM，免密钥）。"""
import math

from ..net import NetError, get_json
from ..registry import ToolError, register
from .geo import search_city

import config

MODES = {
    "driving": ("驾车", 1.0, 0),
    "walking": ("步行", 1.25, 4.8),   # 路网系数 1.25，速度 km/h
    "cycling": ("骑行", 1.20, 15.0),
}
_SCHEMA = {
    "type": "object",
    "properties": {
        "origin": {"type": "string", "description": "出发地城市或地名"},
        "destination": {"type": "string", "description": "目的地城市或地名"},
        "mode": {"type": "string", "description": "driving / walking / cycling，默认 driving"},
    },
    "required": ["origin", "destination"],
}


def _haversine(a, b):
    r = 6371.0
    la1, lo1 = math.radians(a[0]), math.radians(a[1])
    la2, lo2 = math.radians(b[0]), math.radians(b[1])
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(d))


@register("route.plan_route",
          "规划**城际**路线（城市到城市），返回真实路网里程与耗时。**只接受城市级地名**：市内景点之间的距离不要用它——底层地名库是城市级的，查「西街」「洛阳桥」这类小地名会解析到外省同名地点，给出上千公里的错误结果；市内通勤请让用户用地图 App。", _SCHEMA)
def plan_route(origin, destination, mode="driving"):
    mode = (mode or "driving").lower()
    if mode not in MODES:
        mode = "driving"
    label, net_factor, speed = MODES[mode]
    a = search_city(origin)
    b = search_city(destination)
    straight = _haversine((a["latitude"], a["longitude"]), (b["latitude"], b["longitude"]))

    result = {
        "origin": a["name"], "destination": b["name"], "mode": mode, "mode_label": label,
        "straight_km": round(straight, 1),
    }

    if mode == "driving":
        try:
            data = get_json("%s/%s,%s;%s,%s" % (
                config.OSRM_URL, a["longitude"], a["latitude"], b["longitude"], b["latitude"]),
                {"overview": "false", "alternatives": "false"})
            route = (data.get("routes") or [{}])[0]
            result.update({
                "distance_km": round((route.get("distance") or 0) / 1000.0, 1),
                "duration_min": int(round((route.get("duration") or 0) / 60.0)),
                "estimated": False,
                "source": "OSRM 实时路网（OpenStreetMap）",
            })
        except NetError as e:
            # 路线服务挂了也要给结论，但必须标明是估算
            km = straight * net_factor
            result.update({
                "distance_km": round(km, 1),
                "duration_min": int(round(km / 60.0 * 60)),
                "estimated": True,
                "source": "离线估算（%s）" % e,
            })
    else:
        km = straight * net_factor
        result.update({
            "distance_km": round(km, 1),
            "duration_min": int(round(km / speed * 60)),
            "estimated": True,
            "source": "按直线距离 × 路网系数 %s 估算" % net_factor,
        })

    km = result["distance_km"]
    if km < 30:
        # 底下的地名库是城市级的，查「西街」这类小地名会解析到外省同名地点，
        # 所以市内通勤一律提醒用地图 App，别让人拿这个数字去导航
        result["advice"] = "这段是市内通勤；本工具的路网按城际标定，市内导航请用地图 App"
    elif km < 200:
        result["advice"] = "中短途，高铁/城际大巴比自驾更省力"
    elif km < 1200:
        result["advice"] = "推荐高铁，注意预留进站时间"
    else:
        result["advice"] = "跨省长途，建议飞机并提前 2 小时到机场"
    result["departure_hint"] = "建议出发时间提前 %d 分钟" % (30 if km < 200 else 60)
    return result
