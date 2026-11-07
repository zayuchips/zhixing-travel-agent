# -*- coding: utf-8 -*-
"""用模型给会话起标题。

原来直接截首句前 18 个字，问题是「帮我排一下长沙三天」这种开头截出来全是废话，
列表里十条会话长得都一样。改成让模型读完首轮问答再概括成一句话。

两个约束：
1. 后台线程跑，不拖慢本轮回复——标题晚几秒出来比回复晚几秒更好。
2. 失败就保持截断标题，绝不因为起标题把对话搞挂。
"""
import json
import threading
import urllib.request

import config

_PROMPT = """给下面这段对话起个标题，要求：
- 不超过 12 个字，不要引号、书名号、句号
- 写目的地和核心诉求，比如「泉州两日古建筑攻略」「广州到长沙自驾」「长沙三日亲子游」
- 只输出标题本身，不要任何解释

用户说：%s
助手答：%s"""


def summarize(user_text, reply):
    """返回标题字符串，失败返回 None。"""
    if not config.LLM_API_KEY or not config.LLM_BASE_URL:
        return None
    body = json.dumps({
        "model": config.LLM_MODEL,
        "messages": [{"role": "user", "content": _PROMPT % (
            (user_text or "")[:400], (reply or "")[:400])}],
        "temperature": 0.2,
        "max_tokens": 2000,
    }).encode("utf-8")
    req = urllib.request.Request(
        config.LLM_BASE_URL.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + config.LLM_API_KEY,
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = (data["choices"][0]["message"].get("content") or "").strip()
    except Exception:  # noqa: BLE001 起标题失败是小事，不该冒泡
        return None
    # 模型偶尔会带引号或前缀，清一遍
    text = text.splitlines()[0].strip().strip("「」『』\"'《》 ")
    for junk in ("标题：", "标题:", "会话标题：", "会话标题:"):
        if text.startswith(junk):
            text = text[len(junk):].strip()
    if not text:
        return None
    return text[:20]


def name_async(session_id, user_id, user_text, reply):
    """后台起标题，成功后写库。调用方不等待。"""
    def run():
        title = summarize(user_text, reply)
        if not title:
            return
        try:
            import db
            db.rename_session(session_id, user_id, title)
        except Exception:  # noqa: BLE001
            pass

    t = threading.Thread(target=run, name="titler-%s" % session_id, daemon=True)
    t.start()
    return t
