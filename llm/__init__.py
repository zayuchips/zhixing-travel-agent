# -*- coding: utf-8 -*-
"""智能体层：大模型接入（可选）。

设计原则（写进需求文档，答辩时会被问）：
1. **不是必需的**。没有 Key 时系统走规则理解 + 真实工具数据 + 模板化表达，
   整个业务流程（对话 → 工具调用 → 结构化结果 → 自然语言回复）完全跑通。
2. **可插拔**。任何 OpenAI 兼容端点（DeepSeek / Kimi / 通义 / 本地 vLLM / Ollama）
   只要改三个环境变量即可。
3. **有兜底**。模型超时、鉴权失败、返回不是合法 JSON，一律静默回退规则层，
   并把「本次未使用大模型」如实告诉用户，绝不假装是模型输出。
"""
import json
import re

import config
from mcp.net import NetError, get_json  # noqa: F401  （复用 UA/超时配置）

try:  # Python 3.9 标准库
    import urllib.error
    import urllib.request
except ImportError:  # pragma: no cover
    raise


class LLMError(Exception):
    pass


class Provider(object):
    name = "base"
    label = "基础"

    def available(self):
        return False

    def chat(self, system, messages, temperature=0.3):
        raise LLMError("未实现")

    def extract_json(self, system, user):
        text = self.chat(system, [{"role": "user", "content": user}], temperature=0.0)
        return _loads_json(text)


def _loads_json(text):
    """模型经常把 JSON 包在 ```json 里，做一次宽松解析。"""
    if not text:
        raise LLMError("模型返回为空")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMError("模型没有返回 JSON")
    try:
        return json.loads(m.group(0))
    except ValueError as e:
        raise LLMError("JSON 解析失败：%s" % e)


class OpenAICompatProvider(Provider):
    """OpenAI / DeepSeek / Kimi / 任意兼容端点。"""
    name = "openai_compat"

    def __init__(self):
        self.base_url = (config.LLM_BASE_URL or "").rstrip("/")
        self.api_key = config.LLM_API_KEY or ""
        self.model = config.LLM_MODEL or "gpt-4o-mini"
        self.label = "大模型（%s）" % self.model

    def available(self):
        return bool(self.base_url and self.api_key)

    def chat(self, system, messages, temperature=0.3):
        if not self.available():
            raise LLMError("未配置 LLM_API_KEY")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "temperature": temperature,
            "stream": False,
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
                "User-Agent": "zhixing-travel-agent/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise LLMError("接口返回 HTTP %s" % e.code)
        except Exception as e:  # noqa: BLE001
            raise LLMError("调用失败：%s" % e)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise LLMError("返回结构不符合 OpenAI 规范")


class DemoProvider(Provider):
    """无密钥时的「理解层」占位：不产生任何文本，由 agent/nlu.py 的规则接管。

    之所以保留这个类：系统需要显式区分「现在是演示模式」和「现在真接了模型」，
    界面上要把这件事告诉用户（诚实性要求）。
    """
    name = "demo"
    label = "规则引擎（无需密钥）"

    def available(self):
        return True


_CACHE = {}


def get_provider():
    if "p" not in _CACHE:
        p = OpenAICompatProvider()
        _CACHE["p"] = p if (config.LLM_PROVIDER != "demo" and p.available()) else DemoProvider()
    return _CACHE["p"]


def status():
    p = get_provider()
    return {
        "provider": p.name, "label": p.label,
        "llm_enabled": p.name != "demo",
        "model": p.model if hasattr(p, "model") else None,
        "base_url": p.base_url if hasattr(p, "base_url") else None,
    }
