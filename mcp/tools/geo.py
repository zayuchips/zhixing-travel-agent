# -*- coding: utf-8 -*-
"""MCP 工具：geocode.search_city —— 城市/地名解析（Open-Meteo Geocoding，免密钥）。

踩坑记录：直接查「长沙」会命中若干个**同名村庄**（重庆/贵州/广东各有一个叫长沙的
PPLA4），真正的地级市在 Open-Meteo 里登记成「长沙市」。所以这里做两件事：
    1) 原名和「原名+市」各查一次，合并候选；
    2) 按 feature_code（PPLC/PPLA 权重最高）与人口数打分，取分最高的一个。
否则用户问「长沙天气」会拿到重庆某个村的天气 —— 这是个真实复现过的 bug。
"""
from ..net import NetError, get_json
from ..registry import ToolError, register

import config

import time
from concurrent.futures import ThreadPoolExecutor

_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "城市或地名，中文或拼音，例如「长沙」「Guangzhou」"},
    },
    "required": ["name"],
}

_FEATURE_BONUS = {"PPLC": 5000000, "PPLA": 3000000, "PPLA2": 1000000, "PPLA3": 300000, "PPLA4": 0}

# 地名解析结果缓存（同一次对话里天气/路线/行程会反复查同一座城市，
# 没有缓存的话一次「排行程」要打 3 次地名接口，白等 6 秒）
_CACHE = {}
_CACHE_TTL = 3600.0


def _cached(name):
    item = _CACHE.get(name)
    if item and (time.time() - item[0]) < _CACHE_TTL:
        return item[1]
    return None


def _store(name, payload):
    _CACHE[name] = (time.time(), payload)
    return payload


def _query(name):
    try:
        data = get_json(config.GEOCODE_URL, {"name": name, "count": 10, "language": "zh", "format": "json"})
    except NetError as e:
        raise ToolError("地名解析服务不可用：%s" % e)
    return data.get("results") or []


def _score(r):
    return _FEATURE_BONUS.get(r.get("feature_code") or "", 0) + (r.get("population") or 0)


@register("geocode.search_city", "把城市/地名解析成经纬度与行政区信息，作为天气与路线工具的前置步骤。", _SCHEMA)
def search_city(name):
    name = (name or "").strip()
    if not name:
        raise ToolError("地名不能为空")
    hit = _cached(name)
    if hit:
        return hit
    # 原名与「原名+市」并发查，省掉一次串行等待
    queries = [name] if name.endswith("市") else [name, name + "市"]
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        results = list(pool.map(_query, queries))
    candidates = []
    for r in results:
        candidates += r
    if not candidates:
        raise ToolError("没有找到地名「%s」，换个写法试试（如「长沙市」）" % name)
    best = max(candidates, key=_score)
    return _store(name, {
        "query": name,
        "name": best.get("name"),
        "admin1": best.get("admin1"),
        "admin2": best.get("admin2"),
        "country": best.get("country"),
        "latitude": best.get("latitude"),
        "longitude": best.get("longitude"),
        "timezone": best.get("timezone"),
        "feature_code": best.get("feature_code"),
        "population": best.get("population"),
    })
