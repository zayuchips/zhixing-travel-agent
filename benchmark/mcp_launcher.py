# -*- coding: utf-8 -*-
"""把 ChinaTravel 的 agent_env MCP server 拉起来，供 Pi 挂载。

为什么要这层壳：Pi 的 mcp.json 不支持 cwd，而 agent_env 必须在仓库根目录
才能 import；另外它的依赖装在 3.12 的 venv 里，不能拿主项目的 3.13 直接跑。
所以用一个入口脚本自己 chdir + 补 sys.path。

由 run.py 生成配置时把这个文件的绝对路径写进 args。
"""
import os
import runpy
import sys

ROOT = os.environ.get("CHINATRAVEL_ROOT")
if not ROOT or not os.path.isdir(ROOT):
    sys.stderr.write("CHINATRAVEL_ROOT 未设置或目录不存在\n")
    sys.exit(2)

os.environ.setdefault("CHINATRAVEL_LANG", "zh")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.chdir(ROOT)
sys.path.insert(0, ROOT)
runpy.run_module("agent_env.mcp_stdio", run_name="__main__")
