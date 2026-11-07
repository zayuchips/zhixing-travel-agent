# -*- coding: utf-8 -*-
"""导入即注册。顺序有讲究：geo 被 weather/route 依赖，poi 被 hotel/itinerary/experts 依赖。"""
from . import geo, poi, weather, route, hotel, itinerary, experts  # noqa: F401


def register_all():
    """显式入口，避免调用方依赖导入副作用。"""
    return True
