# -*- coding: utf-8 -*-
"""长期记忆：把用户说过的事实存下来，下次对话自动带上。

为什么不用 Pi 的现成能力：Pi 有会话级上下文（--session-id 落盘恢复），
但那是「这个会话里聊过什么」，关掉会话就没了；也没有跨会话的用户档案。
所以这里只做 Pi 不做的那一段：SQLite 存事实 + 每轮注入。

抽取走一次独立的轻量 API 调用，不碰 Pi 的会话，避免污染上下文；
而且是异步的，不拖慢回复。
"""
import json
import re
import threading

import config
import db

# 只有命中文这些词才值得花一次模型调用去抽取，否则白花钱
TRIGGERS = ("我住", "我常住", "我家", "我喜欢", "我偏好", "我不吃", "我不能吃",
            "我过敏", "我带", "我预算", "通常", "习惯", "每次", "固定", "老人", "小孩", "孕")

MAX_ITEMS = 20

_SYS = """从用户这句话里抽取值得长期记住的出行偏好或事实，输出 JSON 数组，最多 3 条。

每条形如 {"key": "常住城市", "value": "广州"}。
key 用简短中文名词，优先从这些里选：常住城市、预算习惯、饮食禁忌、同行人员、兴趣偏好、行动限制、住宿偏好。
只抽长期有效的信息。不要抽本次行程的具体安排（目的地、日期、天数、某次预算）。

没有可记的就输出 []。只输出 JSON 数组，不要解释。"""


def context_for(user_id):
    """拼成一段给模型看的背景。没记忆返回空串。"""
    if not user_id:
        return ""
    rows = db.memories_get(user_id)
    if not rows:
        return ""
    return "\n".join("- %s：%s" % (r["key"], r["value"]) for r in rows[:MAX_ITEMS])


def should_extract(text):
    return any(t in (text or "") for t in TRIGGERS)


def extract_async(user_id, text):
    """异步抽取，失败静默——记忆是加分项，不能因为它把对话搞坏。"""
    if not user_id or not should_extract(text):
        return
    threading.Thread(target=_extract, args=(user_id, text), daemon=True).start()


def _extract(user_id, text):
    try:
        items = _llm_json(text)
    except Exception:  # noqa: BLE001
        return
    for it in items[:3]:
        key = str(it.get("key") or "").strip()
        val = str(it.get("value") or "").strip()
        if key and val and len(key) <= 12 and len(val) <= 60:
            db.memory_set(user_id, key, val, "auto")


def _llm_json(text):
    if not (config.LLM_API_KEY and config.LLM_BASE_URL and config.LLM_MODEL):
        return []
    body = json.dumps({
        "model": config.LLM_MODEL,
        "messages": [{"role": "system", "content": _SYS},
                     {"role": "user", "content": text}],
        "temperature": 0,
        "max_tokens": 300,
    }).encode("utf-8")

    import urllib.request
    req = urllib.request.Request(
        config.LLM_BASE_URL.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + config.LLM_API_KEY,
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        return []
    parsed = json.loads(m.group())
    return parsed if isinstance(parsed, list) else []
