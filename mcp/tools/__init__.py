# -*- coding: utf-8 -*-
"""导入即注册。注意顺序：geo 被 weather / route 依赖，先导入。"""
from . import geo, weather, route, itinerary  # noqa: F401


def register_all():
    """显式入口，避免调用方依赖导入副作用。"""
    return True
