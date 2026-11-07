# -*- coding: utf-8 -*-
"""MCP 工具二：weather.get_forecast —— 真实天气预报（Open-Meteo，免密钥）。"""
from ..net import NetError, get_json
from ..registry import ToolError, register
from .geo import search_city

import config

# WMO 天气代码 -> 中文描述（Open-Meteo 用的是 WMO 4677 编码）
WMO = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴小冰雹", 99: "雷阵雨伴大冰雹",
}
RAINY = set([51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99])
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "城市名，例如「长沙」"},
        "days": {"type": "integer", "description": "预报天数 1-16，默认 3"},
    },
    "required": ["city"],
}


def _advice(code, tmax, tmin, rain):
    tips = []
    if code in RAINY or rain >= 60:
        tips.append("带伞")
    if tmax is not None and tmax >= 32:
        tips.append("防晒、避开正午户外")
    if tmin is not None and tmin <= 8:
        tips.append("注意保暖")
    if code in (0, 1) and rain < 20:
        tips.append("适合户外行程")
    return "；".join(tips) or "天气平稳"


@register("weather.get_forecast", "查询城市未来若干天的真实天气预报（气温、降水概率、天气现象），并给出出行建议。", _SCHEMA)
def get_forecast(city, days=3):
    try:
        days = max(1, min(int(days or 3), 16))
    except (TypeError, ValueError):
        days = 3
    place = search_city(city)
    try:
        data = get_json(config.WEATHER_URL, {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "Asia/Shanghai",
            "forecast_days": days,
        })
    except NetError as e:
        raise ToolError("天气服务不可用：%s" % e)

    daily = data.get("daily") or {}
    import datetime
    out = []
    for i, d in enumerate(daily.get("time") or []):
        code = (daily.get("weather_code") or [None] * 9)[i]
        tmax = (daily.get("temperature_2m_max") or [None] * 9)[i]
        tmin = (daily.get("temperature_2m_min") or [None] * 9)[i]
        rain = (daily.get("precipitation_probability_max") or [None] * 9)[i]
        try:
            weekday = WEEKDAYS[datetime.date.fromisoformat(d).weekday()]
        except ValueError:
            weekday = ""
        out.append({
            "date": d, "weekday": weekday,
            "code": code,
            "weather": WMO.get(code, "未知"),
            "tmax": tmax, "tmin": tmin,
            "rain_prob": rain,
            "rainy": bool(code in RAINY or (rain or 0) >= 60),
            "advice": _advice(code, tmax, tmin, rain or 0),
        })
    return {
        "city": place["name"],
        "region": "%s%s" % (place.get("admin1") or "", place.get("admin2") or ""),
        "days": out,
        "source": "open-meteo.com（实时预报，无需密钥）",
    }
