# -*- coding: utf-8 -*-
"""MCP 工具五：poi.* —— 高德 Web 服务 API（地点检索 / 详情 / 地理编码）。

没配 AMAP_KEY 时回退 data/cities.json，返回的 source 标为 local。
调用方据此能分辨「实时数据」和「示例数据」—— 不静默降级。
"""
import json
import os
import time

import config
from ..net import NetError, get_json
from ..registry import ToolError, register

_SCHEMA_SEARCH = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "城市名，如「泉州」"},
        "keywords": {"type": "string", "description": "搜索词，如 博物馆/美食/公园/夜景"},
        "limit": {"type": "integer", "description": "返回条数 1-25，默认 10"},
    },
    "required": ["city", "keywords"],
}
_SCHEMA_DETAIL = {
    "type": "object",
    "properties": {"poi_id": {"type": "string", "description": "高德 POI id"}},
    "required": ["poi_id"],
}
_SCHEMA_GEO = {
    "type": "object",
    "properties": {
        "address": {"type": "string", "description": "详细地址"},
        "city": {"type": "string", "description": "城市名，可省略"},
    },
    "required": ["address"],
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
    """城市名/别名 -> cities.json 的 key；匹配不到返回 None。"""
    if not name:
        return None
    name = name.strip()
    for city, info in _cities().items():
        if name == city or city in name or name in city:
            return city
        for alias in info.get("aliases") or []:
            if alias and alias.lower() == name.lower():
                return city
    return None


def _s(v, default=""):
    """高德字段无值时返回空数组 [] 而不是 null，这里统一归一化。"""
    if v in (None, "", [], {}):
        return default
    return v


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _split_loc(loc):
    parts = str(loc or "").split(",")
    if len(parts) != 2:
        return None, None
    return _num(parts[0]), _num(parts[1])


def _check(data):
    """高德的「业务错误」也用 HTTP 200 返回，靠 status 字段区分。"""
    if str(data.get("status")) != "1":
        raise ToolError("高德接口返回错误：%s（%s）" % (data.get("info"), data.get("infocode")))
    return data


def _normalize(p):
    lng, lat = _split_loc(p.get("location"))
    biz = p.get("biz_ext") if isinstance(p.get("biz_ext"), dict) else {}
    photos = [x.get("url") for x in (p.get("photos") or [])
              if isinstance(x, dict) and x.get("url")]
    return {
        "id": _s(p.get("id")),
        "name": _s(p.get("name")),
        "type": _s(p.get("type")),
        "address": _s(p.get("address")),
        "lng": lng, "lat": lat,
        "coord": "gcj02" if lng is not None else "none",
        "rating": _num(biz.get("rating")),
        "cost": _num(biz.get("cost")),
        "open_time": _s(biz.get("open_time")),
        "tel": _s(p.get("tel")),
        "photos": photos,
        "district": _s(p.get("adname")),
    }


def _local(city, keywords, limit):
    """本地降级：只有粗分类，没有坐标。上层靠名字去重。"""
    info = _cities().get(city) or {}
    items = [{
        "id": "", "name": p.get("name"), "type": p.get("cat") or "",
        "address": "", "lng": None, "lat": None, "coord": "none",
        "rating": None, "cost": p.get("cost"), "open_time": "",
        "tel": "", "photos": [], "district": "",
        "indoor": p.get("indoor"),  # 本地数据有准确标记，别丢
    } for p in (info.get("pois") or [])]
    return {
        "city": city, "keywords": keywords, "source": "local",
        "note": "未配置 AMAP_KEY，返回本地示例点位（无坐标）",
        "count": len(items[:limit]), "items": items[:limit],
    }


@register("poi.search_places",
          "按关键词搜索城市内的真实地点（景点/餐饮/公园/夜景等），返回名称、地址、经纬度、评分、人均与照片。"
          "未配置高德 Key 时回退本地示例数据。", _SCHEMA_SEARCH)
def search_places(city, keywords, limit=10):
    city = (city or "").strip()
    keywords = (keywords or "").strip()
    if not city or not keywords:
        raise ToolError("city 与 keywords 都不能为空")
    try:
        limit = max(1, min(int(limit or 10), 25))
    except (TypeError, ValueError):
        limit = 10

    if not config.AMAP_KEY:
        return _local(match_city(city) or city, keywords, limit)

    params = {
        "key": config.AMAP_KEY, "keywords": keywords, "city": city,
        "citylimit": "true", "offset": limit, "page": 1,
        "extensions": "all", "output": "json",
    }
    try:
        data = get_json(config.AMAP_PLACE_URL, params)
    except NetError as e:
        raise ToolError("高德 POI 服务不可用：%s" % e)

    # 10021 = CUQPS_HAS_EXCEEDED_THE_LIMIT，个人 Key 很容易触发，等一下重试一次比直接失败好
    if str(data.get("infocode")) == "10021":
        time.sleep(0.8)
        try:
            data = get_json(config.AMAP_PLACE_URL, params)
        except NetError as e:
            raise ToolError("高德 POI 服务不可用：%s" % e)

    items = [_normalize(p) for p in (_check(data).get("pois") or []) if _s(p.get("name"))]
    # 没坐标的点位对排程和地图都没用，直接剔掉
    items = [i for i in items if i["lng"] is not None]
    return {
        "city": city, "keywords": keywords, "source": "amap",
        "count": len(items[:limit]), "items": items[:limit],
    }


@register("poi.get_detail", "获取单个地点的详情：营业时间、评分、人均消费、照片。", _SCHEMA_DETAIL)
def get_detail(poi_id):
    poi_id = (poi_id or "").strip()
    if not poi_id:
        raise ToolError("poi_id 不能为空")
    if not config.AMAP_KEY:
        raise ToolError("未配置 AMAP_KEY，无法查询 POI 详情")

    try:
        data = get_json(config.AMAP_DETAIL_URL, {
            "key": config.AMAP_KEY, "id": poi_id,
            "extensions": "all", "output": "json",
        })
    except NetError as e:
        raise ToolError("高德详情服务不可用：%s" % e)

    pois = _check(data).get("pois") or []
    if not pois:
        raise ToolError("没有找到该地点：%s" % poi_id)
    return _normalize(pois[0])


@register("poi.geocode", "把详细地址解析成经纬度（GCJ-02 坐标系）。", _SCHEMA_GEO)
def geocode(address, city=None):
    address = (address or "").strip()
    if not address:
        raise ToolError("address 不能为空")
    if not config.AMAP_KEY:
        raise ToolError("未配置 AMAP_KEY，无法做地址解析")

    params = {"key": config.AMAP_KEY, "address": address, "output": "json"}
    if city:
        params["city"] = city
    try:
        data = get_json(config.AMAP_GEO_URL, params)
    except NetError as e:
        raise ToolError("高德地理编码服务不可用：%s" % e)

    geos = _check(data).get("geocodes") or []
    if not geos:
        raise ToolError("无法解析地址：%s" % address)
    g = geos[0]
    lng, lat = _split_loc(g.get("location"))
    if lng is None:
        raise ToolError("地址解析结果没有坐标：%s" % address)
    return {
        "address": address,
        "formatted": _s(g.get("formatted_address")),
        "lng": lng, "lat": lat, "coord": "gcj02",
        "district": _s(g.get("district")),
    }
