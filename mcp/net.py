# -*- coding: utf-8 -*-
"""极简 HTTP 客户端（标准库实现，不依赖 requests）。"""
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

import config

UA = "zhixing-travel-agent/1.0 (GDUT software engineering course project)"


class NetError(Exception):
    pass


def get_json(url, params=None, timeout=None, retries=2):
    """GET 一个 JSON 接口，失败重试，最终抛 NetError。"""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    timeout = timeout or config.HTTP_TIMEOUT
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, socket.timeout, ValueError, OSError) as e:
            last = e
    raise NetError("%s 请求失败：%s" % (url.split("?")[0], last))
