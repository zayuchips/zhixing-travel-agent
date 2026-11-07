# -*- coding: utf-8 -*-
"""MCP 工具四：itinerary.build_plan —— 排逐日行程。

数据来自 poi.search_places（高德实时，未配 Key 时自动降级本地示例），不再是写死的点位表。
三条自有逻辑必须保留：雨天换室内、点位排完给自由活动、拿不到就不编。
"""
import datetime
import time

from ..registry import register
from .hotel import search as hotel_search
from .poi import match_city, search_places
from .weather import get_forecast

# 兴趣 -> 搜索词。拆成「搜什么」而不是「分类名」，因为高德按关键词检索。
INTEREST_KEYWORDS = {
    "美食": ["美食街", "小吃"],
    "历史": ["古迹", "历史建筑"],
    "古建筑": ["古迹", "古建筑", "寺庙"],
    "建筑": ["古迹", "历史建筑"],
    "古镇": ["古镇", "古迹"],
    "寺庙": ["寺庙", "宗教"],
    "宗教": ["宗教", "寺庙"],
    "博物馆": ["博物馆"],
    "文化": ["博物馆", "古迹"],
    "自然": ["公园", "风景名胜"],
    "爬山": ["风景名胜", "山"],
    "海滨": ["海滨", "海滩"],
    "海岛": ["海岛", "风景名胜"],
    "温泉": ["温泉"],
    "夜景": ["观景台", "夜景"],
    "夜游": ["夜景", "步行街"],
    "购物": ["购物中心", "商业街"],
    "亲子": ["动物园", "科技馆", "公园"],
    "遛娃": ["动物园", "科技馆"],
    "拍照": ["风景名胜", "古迹"],
    "打卡": ["风景名胜", "地标"],
}
# 兴趣 -> 高德类型关键词，用于给候选点位打分
INTEREST_TYPES = {
    "美食": ["餐饮", "美食"],
    "历史": ["风景名胜", "文物古迹", "宗教"],
    "古建筑": ["文物古迹", "寺庙道观", "风景名胜"],
    "建筑": ["文物古迹", "风景名胜"],
    "古镇": ["风景名胜", "文物古迹"],
    "寺庙": ["寺庙道观", "宗教"],
    "宗教": ["寺庙道观", "宗教"],
    "博物馆": ["博物馆", "科教文化"],
    "文化": ["博物馆", "文物古迹"],
    "自然": ["公园", "风景名胜"],
    "爬山": ["风景名胜"],
    "海滨": ["风景名胜"],
    "海岛": ["风景名胜"],
    "温泉": ["风景名胜", "休闲娱乐"],
    "夜景": ["风景名胜"],
    "夜游": ["风景名胜", "购物"],
    "购物": ["购物", "商场"],
    "亲子": ["科教文化", "公园", "动物园"],
    "遛娃": ["科教文化", "动物园"],
    "拍照": ["风景名胜"],
    "打卡": ["风景名胜"],
}

# 这些不是「去逛的景点」。高德会把夜市、学生街、商业街也归到风景名胜类，
# 搜「景点」时它们排在前面，结果整张行程表全是吃的地方，一个古迹都没有。
MEAL_SPOT_HINTS = ("美食街", "小吃街", "夜市", "学生街", "美食广场", "大排档")

INDOOR_HINT = ("博物馆", "科技馆", "美术馆", "展览", "购物", "商场", "影剧", "宗教")
MEAL_PRICE = {"breakfast": 25, "lunch": 45, "dinner": 60}
TRANSPORT_PER_DAY = {"步行": 0, "公交": 20, "自驾": 80}
MAX_DAYS = 30

_SCHEMA = {
    "type": "object",
    "properties": {
        "destination": {"type": "string", "description": "目的地城市"},
        "days": {"type": "integer", "description": "游玩天数 1-30，默认 2"},
        "start_date": {"type": "string", "description": "出发日期 YYYY-MM-DD，默认明天"},
        "end_date": {"type": "string", "description": "结束日期，给了就用区间推算天数"},
        "interests": {"type": "array", "items": {"type": "string"},
                      "description": "兴趣标签，如 美食/历史/博物馆/自然/夜景/亲子/拍照"},
        "budget": {"type": "number", "description": "总预算（元），可不填"},
        "hotel_level": {"type": "string", "description": "住宿档次：经济/舒适/豪华，默认舒适"},
        "transport": {"type": "string", "description": "市内交通：步行/公交/自驾，默认公交"},
    },
    "required": ["destination"],
}


def _keywords_for(interests):
    kws = []
    for i in interests or []:
        for k in INTEREST_KEYWORDS.get(i, []):
            if k not in kws:
                kws.append(k)
    return kws[:3] or ["景点"]


def _types_for(interests):
    types = []
    for i in interests or []:
        types.extend(INTEREST_TYPES.get(i, []))
    return types


def _is_indoor(p):
    # 本地示例数据带准确标记，优先用它；高德不返回该字段，退回类型推断
    if p.get("indoor") is not None:
        return bool(p["indoor"])
    text = (p.get("type") or "") + (p.get("name") or "")
    return any(h in text for h in INDOOR_HINT)


def _score(p, want_types):
    """多因子打分：类型命中 3 分，口碑 2/1 分，有图 1 分。

    旧版只有 0/2 两档，等于「把匹配的提到前面」，撑不起「按兴趣优先排」这句话。
    """
    s = 0
    typ = p.get("type") or ""
    if any(t in typ for t in want_types):
        s += 3
    r = p.get("rating") or 0
    if r >= 4.5:
        s += 2
    elif r >= 4.0:
        s += 1
    if p.get("photos"):
        s += 1
    return s


def _ticket_price(p):
    """高德的 cost 是「人均消费」，餐饮类不是门票；景点类多数也为空，实际以景区公示为准。"""
    typ = p.get("type") or ""
    if "餐饮" in typ or "美食" in typ:
        return 0
    return int(p.get("cost") or 0)


def _gather(city, keywords, need, drop_meal_spots=False):
    """按关键词搜真实点位并去重，返回 (候选列表, 数据来源)。

    单个关键词失败不影响其他关键词；来源取最后一次成功的结果。
    关键词之间留一点间隔：高德个人 Key 的 QPS 很低，连发会触发 CUQPS_HAS_EXCEEDED。

    drop_meal_spots：用户兴趣不是吃的时候，把夜市、小吃街、学生街剔掉。
    高德把这些也归进风景名胜类，不过滤的话排出来整趟都在逛小吃街。
    """
    pool, seen, src = [], set(), "amap"
    for idx, kw in enumerate(keywords):
        if idx:
            time.sleep(0.15)
        try:
            r = search_places(city, kw, limit=need)
        except Exception:  # noqa: BLE001 检索失败不该让整张行程表崩掉
            continue
        src = r.get("source") or src
        for it in r.get("items") or []:
            key = it.get("id") or it.get("name")
            if not key or key in seen or not it.get("name"):
                continue
            if drop_meal_spots and any(h in (it.get("name") or "") for h in MEAL_SPOT_HINTS):
                continue
            seen.add(key)
            pool.append(it)
    return pool, src


def _resolve_days(days, start, end_date):
    if end_date:
        try:
            days = (datetime.date.fromisoformat(end_date.strip()) - start).days + 1
        except ValueError:
            pass
    try:
        days = int(days or 2)
    except (TypeError, ValueError):
        days = 2
    return max(1, min(days, MAX_DAYS))


@register("itinerary.build_plan",
          "根据目的地、天数、兴趣与预算生成逐日行程表（含天气提示、三餐、酒店、门票餐饮交通住宿四项预算），"
          "返回结构化行程卡片。", _SCHEMA)
def build_plan(destination, days=2, start_date=None, end_date=None, interests=None,
               budget=None, hotel_level="舒适", transport="公交"):
    interests = [str(i) for i in (interests or [])]
    want_types = _types_for(interests)

    try:
        start = datetime.date.fromisoformat((start_date or "").strip())
    except ValueError:
        start = datetime.date.today() + datetime.timedelta(days=1)
    days = _resolve_days(days, start, end_date)

    city = match_city(destination) or ((destination or "").strip() or "目的地")

    # ---- 候选点位：先去搜真实地点。搜不到才退回通用框架 ----
    # 注意：不能因为「城市不在 cities.json 里」就直接给模板——配了高德 Key 之后，
    # 全国任意城市都应该能搜到真实 POI。收不收录只影响本地降级数据集。
    need = min(days * 3 + 3, 25)
    # 用户想逛吃购物时保留夜市小吃街；想看书古建时就别让它们占坑位
    want_eat = any(k in ("美食", "小吃", "夜游", "夜市", "购物") for k in (interests or []))
    pois, source = _gather(city, _keywords_for(interests), need, drop_meal_spots=not want_eat)

    if pois:
        tagline = "按兴趣与口碑排序"
        generic = False
    else:
        pois = [
            {"name": "%s 老城区步行街" % city, "type": "购物", "address": "",
             "lng": None, "lat": None, "coord": "none", "rating": None,
             "cost": 0, "photos": [], "open_time": "", "id": ""},
            {"name": "%s 市博物馆" % city, "type": "科教文化;博物馆", "address": "",
             "lng": None, "lat": None, "coord": "none", "rating": None,
             "cost": 0, "photos": [], "open_time": "", "id": ""},
            {"name": "%s 城市公园" % city, "type": "公园", "address": "",
             "lng": None, "lat": None, "coord": "none", "rating": None,
             "cost": 0, "photos": [], "open_time": "", "id": ""},
        ]
        source, tagline, generic = "template", "通用行程框架", True

    ranked = sorted(pois, key=lambda p: -_score(p, want_types))

    # ---- 餐饮：优先用真实餐厅；本地降级数据里没有餐厅，就用模板，别把景点当饭 ----
    meal_pool = []
    if not generic and source == "amap":
        meal_pool, _ = _gather(city, ["美食", "小吃"], min(days * 2 + 2, 20))

    # ---- 天气（拿不到不影响排程）----
    weather = {}
    if not generic:
        span = max(7, min((start - datetime.date.today()).days + days, 16))
        try:
            for w in get_forecast(city, span).get("days") or []:
                weather[w["date"]] = w
        except Exception:  # noqa: BLE001
            weather = {}

    # ---- 排程 ----
    used, used_meal = set(), set()
    plan_days = []
    slots = [("09:00", "上午"), ("14:30", "下午"), ("19:30", "晚上")]

    for d in range(days):
        date = start + datetime.timedelta(days=d)
        ds = date.isoformat()
        w = weather.get(ds) or {}
        rainy = bool(w.get("rainy"))
        items, day_attr, day_meals = [], [], []
        outdoor_fallback = False

        def take(only_indoor):
            for p in ranked:
                key = p.get("name")
                if key in used:
                    continue
                if rainy and only_indoor and not _is_indoor(p):
                    continue
                used.add(key)
                return p
            return None

        for clock, slot in slots:
            pick = take(True)
            if pick is None and rainy:
                pick = take(False)
                if pick is not None:
                    outdoor_fallback = True
            if pick is None:
                continue
            day_attr.append({
                "time": clock, "slot": slot, "name": pick["name"],
                "category": pick.get("type") or "",
                "address": pick.get("address") or "",
                "lng": pick.get("lng"), "lat": pick.get("lat"), "coord": pick.get("coord"),
                "rating": pick.get("rating"), "photos": pick.get("photos") or [],
                "open_time": pick.get("open_time") or "",
                "visit_duration": 120, "ticket_price": _ticket_price(pick),
                "indoor": _is_indoor(pick),
            })
            items.append({
                "time": clock, "slot": slot, "name": pick["name"],
                "cat": "景点" if pick.get("type") else "点位",
                "cost": pick.get("cost") or 0, "hours": 2,
                "tip": pick.get("address") or "",
                "lng": pick.get("lng"), "lat": pick.get("lat"),
            })

        if not day_attr:
            items.append({
                "time": "09:00", "slot": "上午", "name": "自由活动 / 返程准备",
                "cat": "机动", "cost": 0, "hours": 3,
                "tip": "没有可排的点位，这段留给你临时发现的地方或休息",
            })

        # 三餐
        for kind, clock, label in (("breakfast", "08:00", "早餐"),
                                   ("lunch", "12:00", "午餐"),
                                   ("dinner", "18:30", "晚餐")):
            rest = None
            for p in meal_pool:
                key = p.get("name")
                if key in used_meal:
                    continue
                used_meal.add(key)
                rest = p
                break
            if rest:
                meal = {"type": kind, "time": clock, "slot": label, "name": rest["name"],
                        "cost": int(rest.get("cost") or MEAL_PRICE[kind]),
                        "address": rest.get("address") or "",
                        "lng": rest.get("lng"), "lat": rest.get("lat")}
            else:
                meal = {"type": kind, "time": clock, "slot": label,
                        "name": {"breakfast": "酒店/街边早餐",
                                 "lunch": "尝本地菜", "dinner": "本地菜馆"}[kind],
                        "cost": MEAL_PRICE[kind], "address": "", "lng": None, "lat": None}
            day_meals.append(meal)
            items.append({"time": clock, "slot": label, "name": meal["name"], "cat": "美食",
                          "cost": meal["cost"], "hours": 1,
                          "tip": "避开景区门口，往居民区走 300 米",
                          "lng": meal.get("lng"), "lat": meal.get("lat")})

        items.sort(key=lambda x: x["time"])
        theme = "雨天室内为主" if rainy else ("兴趣优先" if interests else "经典路线")
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
            "theme": theme, "note": note,
            "attractions": day_attr, "meals": day_meals,
            "transportation": transport,
            "transport_cost": TRANSPORT_PER_DAY.get(transport, 20),
            "items": items,
        })

    # ---- 酒店：按第一天首个景点就近选，最后一晚算返程 ----
    anchor = None
    for day in plan_days:
        for a in day["attractions"]:
            if a.get("lng") is not None:
                anchor = a
                break
        if anchor:
            break

    hotel = None
    if not generic:
        try:
            args = {"city": city, "level": hotel_level, "limit": 3}
            if anchor:
                args["near_lng"], args["near_lat"] = anchor["lng"], anchor["lat"]
            found = hotel_search(**args)
            if found.get("items"):
                hotel = found["items"][0]
        except Exception:  # noqa: BLE001 酒店失败不影响行程主体
            hotel = None

    nights = max(0, days - 1)
    for i, day in enumerate(plan_days):
        if i < nights and hotel:
            day["hotel"] = hotel
            day["accommodation"] = "%s（%s档，约 %d 元/晚，估算）" % (
                hotel["name"], hotel["level"], hotel["price_per_night"])
        else:
            if i == 0 and nights > 0:
                day["accommodation"] = ("未配置 AMAP_KEY，暂无酒店推荐" if source == "local"
                                        else "未取到酒店信息")
            elif i < nights:
                day["accommodation"] = ""  # 同上一晚，不重复提示
            else:
                day["accommodation"] = "返程，无需住宿"
        day["description"] = "、".join(a["name"] for a in day["attractions"]) or "自由活动"

    # ---- 预算四拆 ----
    ticket = sum(a.get("ticket_price") or 0 for d in plan_days for a in d["attractions"])
    food = sum(m["cost"] for d in plan_days for m in d["meals"])
    hotel_cost = hotel["price_per_night"] * nights if hotel else 0
    transport_cost = sum(d["transport_cost"] for d in plan_days)
    total = ticket + food + hotel_cost + transport_cost
    summary = {
        "ticket_cost": ticket, "food_cost": food, "hotel_cost": hotel_cost,
        "transport_cost": transport_cost, "total_cost": total,
        "currency": "CNY", "per_day": int(round(total / float(days))),
        "notes": ["门票按高德人均消费字段累计，多数景点该字段为空，实际以景区公示为准",
                  "酒店价格为按档次估算，非实时房价",
                  "市内交通按「%s」估算，不含城际大交通" % transport],
    }
    if budget:
        try:
            b = float(budget)
            summary["budget"] = b
            summary["budget_ok"] = total <= b
        except (TypeError, ValueError):
            pass

    tips = ["门票与预约：博物馆类多为免费但需提前 1-3 天实名预约"]
    if source == "local":
        tips.insert(0, "未配置 AMAP_KEY，点位来自本地示例数据，仅供演示")
    elif source == "empty":
        tips.insert(0, "「%s」没有检索到可用点位，行程可能为空；可换个说法或补充 AMAP_KEY" % city)
    if generic:
        tips.insert(0, "「%s」暂未收录，当前为通用框架，建议补充当地攻略后微调" % city)

    return {
        "destination": city, "matched": not generic, "tagline": tagline,
        "start_date": start.isoformat(), "days_count": days,
        "interests": interests, "days": plan_days, "summary": summary, "tips": tips,
        "poi_source": source, "hotel": hotel,
        "weather_source": "open-meteo.com" if weather else "未取到预报",
    }
