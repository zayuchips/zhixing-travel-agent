# -*- coding: utf-8 -*-
"""智行 · 出行规划智能体 —— 全局配置。

所有配置都可以用环境变量覆盖，默认值保证「零安装、零密钥」也能完整跑通。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")

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

# ---- 外部工具（无需密钥的公共服务）------------------------------------
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
# 注意：用 http。macOS 自带 Python 3.9 链的是 LibreSSL，访问 OSRM 官方演示服的
# https 会报 SSLV3_ALERT_HANDSHAKE_FAILURE；http 端点返回同样的数据。
OSRM_URL = os.environ.get("OSRM_URL", "http://router.project-osrm.org/route/v1/driving")
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "12"))

# 工具调用失败后是否允许用离线兜底数据（答辩机房断网也能演示）
ALLOW_OFFLINE_FALLBACK = os.environ.get("ALLOW_OFFLINE_FALLBACK", "1") == "1"
