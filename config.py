# -*- coding: utf-8 -*-
"""智行 · 出行规划智能体 —— 全局配置。

所有配置都可以用环境变量覆盖，默认值保证「零安装、零密钥」也能完整跑通。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")


def _load_dotenv(path):
    """读项目根的 .env。已存在的环境变量优先（命令行 > .env），不覆盖。

    这样本地凭据放 .env 就能跑，不用每次 export；.env 已在 .gitignore 里。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


_load_dotenv(os.path.join(BASE_DIR, ".env"))

PORT = int(os.environ.get("PORT", "8000"))
BIND = os.environ.get("BIND", "127.0.0.1")
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "zhixing.db"))

APP_NAME = "智行 · 出行规划智能体"
APP_VERSION = "1.0.0"

# ---- 大模型接入（可选）------------------------------------------------
# provider: auto / demo / openai_compat
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "auto")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "25"))

# ---- 高德 Web 服务 API（可选）----------------------------------------
# 申请时必须选「Web服务」平台，不是「Web端(JS API)」，两者 Key 不通用。
# 不填也能跑：poi / hotel 工具会回退本地示例数据并标注 source=local。
AMAP_KEY = os.environ.get("AMAP_KEY", "")
AMAP_PLACE_URL = "https://restapi.amap.com/v3/place/text"
AMAP_DETAIL_URL = "https://restapi.amap.com/v3/place/detail"
AMAP_GEO_URL = "https://restapi.amap.com/v3/geocode/geo"
AMAP_WALKING_URL = "https://restapi.amap.com/v3/direction/walking"
AMAP_DRIVING_URL = "https://restapi.amap.com/v3/direction/driving"

# ---- Pi agent harness（本项目的主引擎）--------------------------------
# 需要 Node 22+ 与 npm i -g @earendil-works/pi-coding-agent
PI_BIN = os.environ.get("PI_BIN", "pi")
PI_MODEL = os.environ.get("PI_MODEL", "")
PI_PROVIDER = os.environ.get("PI_PROVIDER", "")
# 系统提示词文件；留空用项目根的 SYSTEM.md。参赛模式会指向 benchmark/SYSTEM.md
PI_SYSTEM_PROMPT = os.environ.get("PI_SYSTEM_PROMPT", "")
PI_TIMEOUT = float(os.environ.get("PI_TIMEOUT", "120"))
# 每个登录会话一个长驻子进程；超过这个数量就关掉最久没用的那个
PI_MAX_SESSIONS = int(os.environ.get("PI_MAX_SESSIONS", "6"))

# ---- 外部工具（无需密钥的公共服务）------------------------------------
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
# 注意：用 http。macOS 自带 Python 3.9 链的是 LibreSSL，访问 OSRM 官方演示服的
# https 会报 SSLV3_ALERT_HANDSHAKE_FAILURE；http 端点返回同样的数据。
OSRM_URL = os.environ.get("OSRM_URL", "http://router.project-osrm.org/route/v1/driving")
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "12"))

# 工具调用失败后是否允许用离线兜底数据（答辩机房断网也能演示）
ALLOW_OFFLINE_FALLBACK = os.environ.get("ALLOW_OFFLINE_FALLBACK", "1") == "1"

# ---------------- 设置页：读写 .env ----------------

EDITABLE = ("DASHSCOPE_API_KEY", "LLM_API_KEY", "AMAP_KEY", "LLM_MODEL", "PI_MODEL")


def env_path():
    return os.path.join(BASE_DIR, ".env")


def update_env(updates):
    """把键值写回 .env，保留注释与其他行，然后刷新进程内配置立即生效。"""
    valid = {k: v for k, v in updates.items() if k in EDITABLE}
    if not valid:
        return {}

    lines = []
    try:
        with open(env_path(), "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        pass

    out, seen = [], set()
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in valid:
                out.append("%s=%s" % (key, valid[key]))
                seen.add(key)
                continue
        out.append(line)
    for key, val in valid.items():
        if key not in seen:
            out.append("%s=%s" % (key, val))

    with open(env_path(), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")

    for key, val in valid.items():
        os.environ[key] = val
    refresh()
    return valid


def refresh():
    """把环境变量重新读进模块级常量。改完 .env 后调用。"""
    global AMAP_KEY, LLM_API_KEY, LLM_MODEL, LLM_BASE_URL, PI_MODEL, PI_PROVIDER
    AMAP_KEY = os.environ.get("AMAP_KEY", "")
    LLM_API_KEY = os.environ.get("LLM_API_KEY", "") or os.environ.get("DASHSCOPE_API_KEY", "")
    LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
    PI_MODEL = os.environ.get("PI_MODEL", "")
    PI_PROVIDER = os.environ.get("PI_PROVIDER", "")
