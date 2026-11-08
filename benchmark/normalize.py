# -*- coding: utf-8 -*-
"""plan 规范化：把模型漏掉的字段和算错的 cost 补齐。

为什么要这一层：ChinaTravel 的评测器检查的是**可执行约束**，不是语义。
模型能查对景点、排对时间，却经常漏 `tickets`、把 `cost` 写成单价——
这些是纯粹的记账规则，让模型每次都记住不划算，用确定性代码补更可靠。

对应评测器里的公式（chinatravel/symbol_verification/commonsense_constraint.py）：
    景点        cost = price * tickets
    餐饮        cost = price * people_number
    城际交通    cost = price * tickets
    metro       cost = price * tickets，且 transports[1].tickets 必须等于 1
    walk        cost = 0
    taxi        transports[0].cars 必须等于 1
"""
from __future__ import annotations


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize(plan, people_number=1):
    """就地修一份 plan，返回 (plan, 修改项列表)。"""
    changes = []
    people = int(people_number or 1)

    for day in plan.get("itinerary") or []:
        for act in day.get("activities") or []:
            kind = act.get("type")
            price = _num(act.get("price"))

            if kind in ("attraction", "train", "airplane"):
                if act.get("tickets") is None:
                    act["tickets"] = people
                    changes.append("+%s.tickets" % kind)
                want = price * _num(act.get("tickets"))
                if abs(_num(act.get("cost")) - want) > 0.1:
                    act["cost"] = round(want, 2)
                    changes.append("~%s.cost" % kind)
            elif kind in ("breakfast", "lunch", "dinner"):
                want = price * people
                if abs(_num(act.get("cost")) - want) > 0.1:
                    act["cost"] = round(want, 2)
                    changes.append("~%s.cost" % kind)

            segs = act.get("transports") or []
            for seg in segs:
                mode = seg.get("mode")
                if mode == "walk":
                    if seg.get("cost") != 0 or seg.get("price") != 0:
                        seg["cost"] = 0
                        seg["price"] = 0
                        changes.append("~walk.cost")
                elif mode in ("metro", "taxi"):
                    if seg.get("tickets") is None:
                        seg["tickets"] = people
                        changes.append("+%s.tickets" % mode)
                    if mode == "taxi" and seg.get("cars") is None:
                        seg["cars"] = 1
                        changes.append("+taxi.cars")
                    want = _num(seg.get("price")) * _num(seg.get("tickets"))
                    if abs(_num(seg.get("cost")) - want) > 0.1:
                        seg["cost"] = round(want, 2)
                        changes.append("~%s.cost" % mode)

            # 硬约束 metro_tickets(transports) 直接取 transports[1]["tickets"]，
            # taxi_cars 取 transports[0]["cars"]；段数不足时补齐避免越界/漏判
            if len(segs) >= 2 and segs[1].get("tickets") is None:
                segs[1]["tickets"] = people
                changes.append("+metro_tickets")
            if segs and any(s.get("mode") == "taxi" for s in segs) and segs[0].get("cars") is None:
                segs[0]["cars"] = 1
                changes.append("+taxi_cars")

    return plan, changes
