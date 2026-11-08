# -*- coding: utf-8 -*-
"""Pi 引擎桥接：把 pi 这个 agent harness 接成本项目的「大脑」。

链路：
    server.py → 本模块（JSONL over stdio）→ pi --mode rpc
                                            └─ MCP stdio → mcp/server_stdio.py → 各工具

为什么走 RPC 而不是 SDK：Pi 的 SDK 只有 TypeScript，本项目是纯 Python。
RPC 模式正是官方为「非 Node 集成」准备的（JSONL over stdin/stdout）。

为什么加 --no-builtin-tools：Pi 本职是编程助手，默认带 read/write/edit/bash。
关掉之后它只剩我们通过 MCP 挂上去的业务工具，等于从编程 agent 被裁成领域 agent。

系统提示用 --system-prompt <path> 显式传入。Pi 自动发现的是 AGENTS.md / CLAUDE.md，
不认 SYSTEM.md；以前只把文件放在项目根，提示词其实一直没生效。
传路径而不是把中文塞进命令行，避免 Windows 下经 cmd 转义乱码。
"""
import json
import os
import queue
import shutil
import subprocess
import threading
import time

import config


class PiError(Exception):
    pass


# ---------------- 进程命令 ----------------

def _prefix():
    """把 PI_BIN 解析成可执行前缀。

    支持三种写法：PATH 里的 pi、完整路径的 pi.cmd（Windows）、以及
    「node /path/to/cli.js」这种自带解释器的形式（pi 没全局装时最省事）。
    """
    raw = (config.PI_BIN or "pi").strip()
    parts = raw.split()
    if len(parts) > 1:  # node xxx.js 形式
        head = shutil.which(parts[0]) or parts[0]
        return [head] + parts[1:]

    found = shutil.which(raw) or (raw if os.path.exists(raw) else None)
    if not found:
        raise PiError(
            "找不到 pi 可执行文件（%s）。先安装：npm i -g @earendil-works/pi-coding-agent，"
            "或用 PI_BIN 指定 node + cli.js 的路径" % raw)
    # Windows 上 npm 装出来的是 pi.cmd，CreateProcess 不能直接执行，得经 cmd
    if os.name == "nt" and found.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", found]
    return [found]


def _args(session_key):
    """session_key 必须是 Pi 认的字符集（字母数字 . _ -），调用方传 s<id>。

    用 --session-id 而不是 --no-session：Pi 会把会话落盘并在下次自动恢复，
    多轮上下文（「那第二天呢」）就由它自己维护，我们不用把历史拼进 prompt。
    """
    args = _prefix() + [
        "--mode", "rpc",
        "--session-id", session_key,
        "--session-dir", os.path.join(config.DATA_DIR, "pi-sessions"),
        "--no-builtin-tools",
    ]
    # 参赛模式会通过 config.PI_SYSTEM_PROMPT 换成自己的提示词文件
    prompt_file = getattr(config, "PI_SYSTEM_PROMPT", None) or os.path.join(
        config.BASE_DIR, "SYSTEM.md")
    if os.path.isfile(prompt_file):
        args += ["--system-prompt", prompt_file]
    if config.PI_PROVIDER:
        args += ["--provider", config.PI_PROVIDER]
    if config.PI_MODEL:
        args += ["--model", config.PI_MODEL]
    return args


def available():
    """只检查可执行文件在不在。模型凭据的问题让 Pi 自己报，我们负责转成可读提示。"""
    try:
        _args("zxprobe")
        return True, ""
    except PiError as e:
        return False, str(e)


# ---------------- 单会话子进程 ----------------

class PiSession(object):
    def __init__(self, session_key):
        self.key = session_key
        self.proc = None
        self.q = None
        self.err = []
        self.last_used = time.time()
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._dirty = False   # 被打断过的进程，下一轮重建，避免带着半截状态继续聊
        self._start()

    def _start(self):
        self.proc = subprocess.Popen(
            _args(self.key), cwd=config.BASE_DIR,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.q = queue.Queue()
        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump_out(self):
        # stdout 必须一直读，读者停住会把管道写满，整个 pi 进程卡死
        try:
            for line in self.proc.stdout:
                self.q.put(line)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.q.put(None)

    def _pump_err(self):
        try:
            for line in self.proc.stderr:
                self.err.append(line.decode("utf-8", "replace").rstrip())
                del self.err[:-40]
        except Exception:  # noqa: BLE001
            pass

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _send(self, obj):
        """写 stdin。abort 和 prompt 可能并发，用小锁保护避免 JSON 行交错。"""
        with self._wlock:
            self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            self.proc.stdin.flush()

    def abort(self):
        """中止当前这一轮。不拿 self._lock——ask 正持有它。"""
        if not self.alive():
            return False
        try:
            self._send({"type": "abort"})
            self._dirty = True
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def ask(self, text, on_event, timeout=None):
        """发一条 prompt，逐条回调事件，直到 agent_settled。返回 (成功, 错误说明)。"""
        timeout = timeout or config.PI_TIMEOUT
        with self._lock:
            if self._dirty:
                self.close()
                self._start()
                self._dirty = False
            if not self.alive():
                self._start()
            self.last_used = time.time()

            req = {"id": "p%d" % int(time.time() * 1000), "type": "prompt", "message": text}
            try:
                self._send(req)
            except (BrokenPipeError, OSError) as e:
                return False, "pi 进程不可写：%s" % e

            deadline = time.time() + timeout
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    self.close()
                    return False, "等待 Pi 超过 %d 秒，已重启进程" % timeout
                try:
                    raw = self.q.get(timeout=min(remain, 1.0))
                except queue.Empty:
                    continue
                if raw is None:
                    detail = "；".join(self.err[-3:]) or "无输出"
                    return False, "pi 进程已退出：%s" % detail
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                # on_event 返回 False 表示调用方要提前收工（例如模型硬报错，没必要等重试）
                if on_event(rec) is False:
                    return False, "调用方中止"
                if rec.get("type") == "agent_settled":
                    return True, ""

    def close(self):
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self.proc = None


# ---------------- 会话池 ----------------

_POOL = {}
_POOL_LOCK = threading.Lock()


def session_id_of(sid):
    """把我们的会话号转成 Pi 认的 session id（只留字母数字 . _ -）。"""
    raw = str(sid if sid is not None else "anon")
    safe = "".join(c for c in raw if c.isalnum() or c in "._-")
    return "zx" + (safe or "anon")


def session(sid):
    key = session_id_of(sid)
    with _POOL_LOCK:
        s = _POOL.get(key)
        if s is None or not s.alive():
            s = PiSession(key)
            _POOL[key] = s
            _trim()
        s.last_used = time.time()
        return s


def abort(sid):
    """给指定会话发中止。返回是否真的发出去了。"""
    s = _POOL.get(session_id_of(sid))
    return bool(s and s.abort())


def _trim():
    over = len(_POOL) - config.PI_MAX_SESSIONS
    if over <= 0:
        return
    for k in sorted(_POOL, key=lambda x: _POOL[x].last_used)[:over]:
        try:
            _POOL.pop(k).close()
        except Exception:  # noqa: BLE001
            pass


def shutdown():
    with _POOL_LOCK:
        for s in list(_POOL.values()):
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        _POOL.clear()


# ---------------- 事件 → 本项目数据结构 ----------------

_PRETTY = {
    "itinerary_build_plan": "itinerary.build_plan",
    "weather_get_forecast": "weather.get_forecast",
    "route_plan_route": "route.plan_route",
    "hotel_search": "hotel.search",
    "poi_search_places": "poi.search_places",
    "poi_get_detail": "poi.get_detail",
    "poi_geocode": "poi.geocode",
    "geocode_search_city": "geocode.search_city",
    "expert_convene": "expert.convene",
}
_CARD = {
    "itinerary_build_plan": "itinerary",
    "weather_get_forecast": "weather",
    "route_plan_route": "route",
    "hotel_search": "hotel",
    "poi_search_places": "poi",
    "poi_get_detail": "poi",
    # expert.convene 故意不做卡片：专家意见是给主智能体消化的素材，
    # 由它改写成连贯回答，前端不应该把「某专家说」原样列出来。
}


def _base(name):
    # Pi 会规范化工具名：weather.get_forecast -> mcp__zhixing__weather_get_forecast
    return (name or "").replace("mcp__zhixing__", "")


def pretty(name):
    b = _base(name)
    return _PRETTY.get(b, b)


def card_type(name):
    return _CARD.get(_base(name))


def payload(result):
    """从 tool_execution_end.result 抠出结构化数据。

    实测（Pi 1.5.0）层级是：
        result.structuredContent.structuredContent  ← 真正的业务数据
        result.structuredContent.content[].text     ← 同一份数据的 JSON 字符串
        result.details = {server, tool}             ← 只有元信息，别当数据

    所以按「由内向外」四路兜底，任何一层取到 dict 就返回。
    """
    if not isinstance(result, dict):
        return None

    holder = result.get("structuredContent")
    if isinstance(holder, dict):
        inner = holder.get("structuredContent")
        if isinstance(inner, dict) and inner:
            return inner
        # 不是包装形态（没有 content/isError）就当作数据本身
        if "content" not in holder and "isError" not in holder and holder:
            return holder

    details = result.get("details")
    if isinstance(details, dict):
        sc = details.get("structuredContent")
        if isinstance(sc, dict) and sc:
            return sc
        # details 可能只有 server/tool，那种不算数据
        if details and not set(details).issubset({"server", "tool"}):
            return details

    for c in result.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            try:
                data = json.loads(c.get("text") or "")
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict):
                return data
    return None
