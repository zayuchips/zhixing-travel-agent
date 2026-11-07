# -*- coding: utf-8 -*-
"""理解层（一）：规则版意图与槽位抽取。

为什么需要它：本项目的目标环境是「答辩机房 / 没网 / 没有 API Key」。
没有大模型时，系统仍然要能从「我想 11 月 12 号从广州去长沙玩 3 天，预算 1500，
想看看博物馆和吃辣」里抽出：目的地=长沙、出发地=广州、天数=3、日期、预算=1500、
兴趣=[历史, 美食]。

规则版的能力边界也写清楚（不吹）：它靠关键词和正则，遇到很绕的说法会漏抽；
接上大模型后由 LLM 出 JSON，再做同样的校验与兜底（见 llm/ 与 agent/orchestrator.py）。
"""
import datetime
import json
import os
import re

import config

CN_NUM = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

INTEREST_WORDS = {
    "美食": ["美食", "吃", "小吃", "火锅", "早茶", "夜宵", "探店", "好吃的"],
    "历史": ["历史", "古迹", "古镇", "故居", "文化", "人文", "遗址"],
    "博物馆": ["博物馆", "展览", "美术馆", "文物"],
    "自然": ["自然", "风景", "山", "海", "湖", "公园", "徒步", "爬山"],
    "夜景": ["夜景", "夜游", "晚上逛", "灯光"],
    "购物": ["购物", "买", "商场", "逛街", "伴手礼"],
    "亲子": ["亲子", "孩子", "小孩", "带娃", "遛娃"],
    "拍照": ["拍照", "打卡", "出片", "摄影"],
}
WEATHER_WORDS = ["天气", "气温", "下雨", "下雪", "冷不冷", "热不热", "带伞", "穿什么", "温度"]
ROUTE_WORDS = ["怎么去", "多远", "多远啊", "路线", "开车", "自驾", "高铁", "多久", "几个钟", "怎么走", "车程", "飞过去"]
PLAN_WORDS = ["行程", "攻略", "安排", "计划", "玩", "旅游", "旅行", "几天", "怎么玩", "路线规划", "帮我排", "规划"]
CITY_STOP = ["怎么走", "怎么去", "怎么玩", "多远", "多久", "路线", "开车", "自驾", "高铁", "飞机",
             "出发", "玩", "旅游", "旅行", "出差", "天气", "看看", "逛", "的", "之旅", "几天",
             "住", "吃", "怎么样", "好玩", "冷不冷", "热不热", "冷", "热", "去", "来", "回", "怎", "多"]

_KNOWN = None


def known_cities():
    """data/cities.json 里的城市 + 常见城市名，只用于「快速命中」，不是白名单。"""
    global _KNOWN
    if _KNOWN is None:
        names = set()
        try:
            path = os.path.join(config.DATA_DIR, "cities.json")
            with open(path, "r", encoding="utf-8") as f:
                for city, info in json.load(f).items():
                    names.add(city)
                    for a in info.get("aliases", []):
                        if a and not a.isascii():
                            names.add(a)
        except (OSError, ValueError):
            pass
        names.update(["桂林", "厦门", "南京", "武汉", "青岛", "三亚", "苏州", "天津", "昆明", "丽江",
                      "大理", "哈尔滨", "大连", "拉萨", "香港", "澳门", "台北", "洛阳", "开封", "敦煌",
                      "西双版纳", "张家界", "黄山", "婺源", "珠海", "佛山", "东莞", "汕头", "湛江", "惠州"])
        _KNOWN = sorted(names, key=len, reverse=True)
    return _KNOWN


def _strip_city(word):
    w = (word or "").strip()
    # 从「长沙怎么走」这类粘连串里切出城市名：找最先出现的停用词，切掉它后面的部分
    cut = len(w)
    for stop in CITY_STOP:
        idx = w.find(stop)
        if idx > 0:
            cut = min(cut, idx)
    w = w[:cut]
    return w.strip("的，,。 　")


def _find_known(text):
    hits = []
    for c in known_cities():
        if c in text:
            hits.append((text.index(c), -len(c), c))
    if not hits:
        return []
    hits.sort()
    out = []
    for _, _, c in hits:
        if c not in out:
            out.append(c)
    # 长名优先去重（「广州市」命中「广州」时只保留长的那个）
    final = []
    for c in out:
        if not any(c != o and c in o for o in out):
            final.append(c)
    return final


def parse_days(text):
    m = re.search(r"(\d+)\s*(?:天|日)(?:游|行程)?", text)
    if m:
        return max(1, min(int(m.group(1)), 7))
    m = re.search(r"([一二两三四五六七八九十])\s*(?:天|日)(?:游|行程)?", text)
    if m:
        return max(1, min(CN_NUM.get(m.group(1), 2), 7))
    if "一日游" in text or "当天来回" in text or "周末" in text:
        return 1
    return None


def parse_budget(text):
    m = re.search(r"预算\s*(?:是|大概|约|在)?\s*(\d+(?:\.\d+)?)", text)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:块|元|rmb|RMB)", text)
    if not m:
        m = re.search(r"(\d+)\s*(?:千|k|K)", text)
        if m:
            return float(m.group(1)) * 1000
        return None
    return float(m.group(1))


def parse_date(text, today=None):
    today = today or datetime.date.today()
    m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
    if m:
        try:
            d = datetime.date(today.year, int(m.group(1)), int(m.group(2)))
            if d < today:
                d = datetime.date(today.year + 1, int(m.group(1)), int(m.group(2)))
            return d.isoformat()
        except ValueError:
            pass
    if "后天" in text:
        return (today + datetime.timedelta(days=2)).isoformat()
    if "明天" in text or "明儿" in text:
        return (today + datetime.timedelta(days=1)).isoformat()
    if "今天" in text:
        return today.isoformat()
    m = re.search(r"(下)?(?:周|星期|礼拜)([一二三四五六日天])", text)
    if m:
        target = "一二三四五六日".find(m.group(2).replace("天", "日"))
        if target >= 0:
            delta = (target - today.weekday()) % 7
            if delta == 0:
                delta = 7
            if m.group(1):
                delta += 7
            return (today + datetime.timedelta(days=delta)).isoformat()
    return None


def parse(text, today=None):
    """返回 {intent, slots, missing, source}。"""
    raw = (text or "").strip()
    if not raw:
        return {"intent": "empty", "slots": {}, "source": "rules"}

    t = raw.replace(" ", "")
    cities = _find_known(t)

    origin = None
    m = re.search(r"从([\u4e00-\u9fa5]{2,6}?)(?:出发|启程|到|去|飞|坐|乘)", t)
    if m:
        origin = _strip_city(m.group(1))

    dest = None
    m = re.search(r"(?:到|去|去往|前往|飞往|飞)([\u4e00-\u9fa5]{2,6}?)(?=玩|旅游|旅行|出差|天气|看看|逛|的|吃|，|,|。|$|\d)", t)
    if m:
        dest = _strip_city(m.group(1))
    if not dest:
        # 「帮我排漠河2天行程」这类：目的地不在常见城市表里，靠上下文切词
        m = re.search(r"(?:排|去|到|在|游|逛)([\u4e00-\u9fa5]{2,4}?)"
                      r"(?=\d|[一二两三四五六七八九十]\s*(?:天|日)|行程|攻略|旅游|游玩|玩)", t)
        if m:
            dest = _strip_city(m.group(1))
    if not dest and cities:
        remain = [c for c in cities if c != origin]
        dest = (remain or cities)[0]
    if origin and dest and origin == dest:
        origin = None

    interests = []
    for tag, words in INTEREST_WORDS.items():
        if any(w in t for w in words):
            interests.append(tag)

    days = parse_days(t)
    budget = parse_budget(t)
    start_date = parse_date(t, today)

    mode = "driving"
    if any(w in t for w in ["走路", "步行", "走着去"]):
        mode = "walking"
    elif any(w in t for w in ["骑行", "骑车"]):
        mode = "cycling"

    wants_weather = any(w in t for w in WEATHER_WORDS)
    wants_route = any(w in t for w in ROUTE_WORDS)
    plan_strong = any(w in t for w in ["行程", "攻略", "安排", "计划", "怎么玩", "旅游", "旅行",
                                       "规划", "帮我排", "几天怎么", "玩几天"])
    wants_plan = plan_strong or days is not None or bool(interests)

    if wants_weather and not plan_strong:
        intent = "weather"
    elif wants_route and not plan_strong:
        intent = "route"
    elif wants_plan:
        intent = "plan"
    elif dest or origin:
        intent = "weather"
    else:
        intent = "chitchat"

    slots = {
        "origin": origin, "destination": dest, "days": days, "budget": budget,
        "interests": interests, "start_date": start_date, "mode": mode,
    }
    slots = {k: v for k, v in slots.items() if v not in (None, [], "")}
    return {"intent": intent, "slots": slots, "source": "rules"}


def missing_slots(intent, slots):
    need = {"weather": ["destination"], "route": ["origin", "destination"], "plan": ["destination"]}.get(intent, [])
    return [k for k in need if not slots.get(k)]
