# -*- coding: utf-8 -*-
"""ChinaTravel 参赛入口：复用智行的 Pi 桥接层，把工具层换成 benchmark 沙盒。

和主项目的关系：
    - 复用 agent/pi_bridge.py（JSONL 收发、会话池、abort）
    - 复用 mcp 的「工具作为模型唯一数据来源」这一设计
    - 不复用 mcp/tools/*（那里查的是高德，名字和沙盒对不上）
    - 输出从「自然语言 + 攻略文件」改成「评测器能执行的 plan JSON」

运行：
    CHINATRAVEL_ROOT=/path/to/ChinaTravel python benchmark/run.py --split smoke --limit 1 --eval
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

DEFAULT_ROOT = os.path.normpath(os.path.join(BASE, "..", "_bench", "ChinaTravel"))
RUNTIME = os.path.join(BENCH_DIR, ".runtime")
METHOD = "zhixing"


def _read_env():
    """从主项目 .env 读 Key，不写进任何配置文件。"""
    out = {}
    path = os.path.join(BASE, ".env")
    if os.path.isfile(path):
        for line in io.open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def prepare(args):
    """生成一份只属于参赛模式的 Pi 配置，不碰 ~/.pi/agent。"""
    root = os.environ.get("CHINATRAVEL_ROOT") or DEFAULT_ROOT
    if not os.path.isdir(root):
        raise SystemExit("找不到 ChinaTravel 仓库：%s（用 CHINATRAVEL_ROOT 指定）" % root)

    venv_py = os.path.join(root, ".venv", "Scripts", "python.exe")
    if not os.path.isfile(venv_py):
        venv_py = os.path.join(root, ".venv", "bin", "python")
    if not os.path.isfile(venv_py):
        raise SystemExit("benchmark 的 .venv 不存在，先按 README 建好")

    cfg_dir = os.path.join(RUNTIME, "pi_config")
    data_dir = os.path.join(RUNTIME, "data")
    os.makedirs(cfg_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    env = _read_env()
    for k, v in env.items():
        os.environ.setdefault(k, v)

    mcp = {"mcpServers": {"chinatravel": {
        "command": venv_py,
        "args": [os.path.join(BENCH_DIR, "mcp_launcher.py")],
        "env": {
            "CHINATRAVEL_ROOT": root,
            "CHINATRAVEL_LANG": "zh",
            "HF_ENDPOINT": os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"),
            "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
        },
        "exposure": "direct",
        "description": "ChinaTravel 沙盒：景点/餐厅/酒店/列车/市内交通查询",
    }}}
    with io.open(os.path.join(cfg_dir, "mcp.json"), "w", encoding="utf-8") as f:
        json.dump(mcp, f, ensure_ascii=False, indent=2)

    models = {"providers": {"dashscope": {
        "baseUrl": os.environ.get("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "api": "openai-completions",
        "apiKey": "$DASHSCOPE_API_KEY",
        "models": [{"id": args.model, "name": "ChinaTravel 参赛模型"}],
    }}}
    with io.open(os.path.join(cfg_dir, "models.json"), "w", encoding="utf-8") as f:
        json.dump(models, f, ensure_ascii=False, indent=2)

    os.environ["PI_CODING_AGENT_DIR"] = cfg_dir
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    # 复用智行的桥接层，但把 cwd / 会话目录 / 系统提示换成参赛版
    import config
    config.BASE_DIR = BENCH_DIR
    config.DATA_DIR = data_dir
    config.PI_PROVIDER = "dashscope"
    config.PI_MODEL = args.model
    config.PI_TIMEOUT = args.timeout
    config.PI_SYSTEM_PROMPT = os.path.join(BENCH_DIR, "SYSTEM.md")
    return root, venv_py


def load_queries(root, split, limit):
    """读本地 query 目录。镜像上的 csv 是旧 schema，见 benchmark/README.md。"""
    qdir = os.path.join(root, "chinatravel", "data", split)
    split_file = os.path.join(root, "chinatravel", "evaluation", "default_splits", split + ".txt")
    uids = [x.strip() for x in io.open(split_file, encoding="utf-8") if x.strip()]
    items = []
    for uid in uids[:limit]:
        p = os.path.join(qdir, uid + ".json")
        if os.path.isfile(p):
            items.append((uid, json.load(io.open(p, encoding="utf-8"))))
    return items


def collect(prompt, timeout):
    """驱动一次 Pi 会话，拼回模型吐出的文本，并记录工具调用与 token 用量。"""
    from agent import pi_bridge

    chunks, tools, usage_events = [], [], []

    def on_event(rec):
        if rec.get("usage"):
            usage_events.append({
                "type": rec.get("type"),
                "usage": rec.get("usage"),
                "message": (rec.get("message") or {}).get("role")
                if isinstance(rec.get("message"), dict) else None,
            })
        kind = rec.get("type")
        if kind == "message_update":
            ev = rec.get("assistantMessageEvent") or {}
            if ev.get("type") == "text_delta":
                chunks.append(ev.get("delta") or "")
        elif kind == "tool_execution_start":
            tools.append(rec.get("toolName") or "")
        elif kind == "message_end":
            msg = rec.get("message") or {}
            if msg.get("stopReason") == "error":
                tools.append("ERROR:" + str(msg.get("errorMessage"))[:120])

    sess = pi_bridge.PiSession("ct-%d" % int(time.time()))
    try:
        ok, err = sess.ask(prompt, on_event, timeout=timeout)
    finally:
        sess.close()
    return "".join(chunks), tools, ok, err, usage_events


def extract_json(text):
    """模型偶尔会包一层代码围栏或写半句开场白，尽量抠出 JSON。"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = m.group(1) if m else None
    if raw is None:
        i, j = text.find("{"), text.rfind("}")
        raw = text[i:j + 1] if i >= 0 and j > i else ""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def validate(venv_py, root, plan_path):
    """用 benchmark 自带的 output_schema.json 做精确校验。"""
    script = (
        "import json,sys,jsonschema\n"
        "p=json.load(open(sys.argv[1],encoding='utf-8'))\n"
        "s=json.load(open(sys.argv[2],encoding='utf-8'))\n"
        "try:\n"
        " jsonschema.validate(p,s); print('SCHEMA OK')\n"
        "except Exception as e: print('SCHEMA FAIL:',str(e)[:200]); sys.exit(1)\n"
    )
    schema = os.path.join(root, "chinatravel", "evaluation", "output_schema.json")
    r = subprocess.run([venv_py, "-c", script, plan_path, schema],
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="smoke")
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--model", default=os.environ.get("PI_MODEL", "deepseek-v4.1-flash"))
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--eval", action="store_true", help="跑完调评测器出分")
    args = ap.parse_args()

    root, venv_py = prepare(args)
    queries = load_queries(root, args.split, args.limit)
    if not queries:
        raise SystemExit("没有可用 query，检查 split 名和本地 query 目录")

    out_dir = os.path.join(root, "results", METHOD)
    os.makedirs(out_dir, exist_ok=True)
    log_dir = os.path.join(RUNTIME, "logs")
    os.makedirs(log_dir, exist_ok=True)

    print("Pi 配置目录:", os.environ["PI_CODING_AGENT_DIR"])
    print("系统提示:", os.path.join(BENCH_DIR, "SYSTEM.md"))
    print("沙盒:", root)
    print("共 %d 条 query，模型 %s\n" % (len(queries), args.model))

    ok_n = 0
    metrics = []
    for idx, (uid, q) in enumerate(queries, 1):
        text = q.get("nature_language") or ""
        prompt = (
            "[参赛任务]\n%s\n\n"
            "出发城市：%s　目的城市：%s　天数：%s　人数：%s\n"
            "请先用 MCP 工具把候选查齐，然后按系统提示只输出一个 JSON 计划对象。"
            % (text, q.get("start_city"), q.get("target_city"),
               q.get("days"), q.get("people_number")))
        started = time.time()
        raw, tools, ok, err, usage_events = collect(prompt, args.timeout)
        elapsed = time.time() - started
        plan = extract_json(raw)
        with io.open(os.path.join(log_dir, uid + ".txt"), "w", encoding="utf-8") as f:
            f.write("RAW OUTPUT\n" + raw + "\n\nTOOLS\n" + "\n".join(tools) +
                    "\n\nERROR\n" + str(err))
        with io.open(os.path.join(log_dir, uid + ".usage.jsonl"), "w", encoding="utf-8") as f:
            for ev in usage_events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        metrics.append({"uid": uid, "elapsed_sec": round(elapsed, 1),
                        "tools": len(tools), "output_chars": len(raw),
                        "usage_events": usage_events})

        status = "FAIL"
        fixes = []
        if plan is not None:
            plan.setdefault("people_number", int(q.get("people_number") or 1))
            plan.setdefault("start_city", q.get("start_city"))
            plan.setdefault("target_city", q.get("target_city"))
            from normalize import normalize
            plan, fixes = normalize(plan, plan.get("people_number"))
            with io.open(os.path.join(out_dir, uid + ".json"), "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            good, msg = validate(venv_py, root, os.path.join(out_dir, uid + ".json"))
            status = "PASS" if good else "SCHEMA-FAIL"
            if good:
                ok_n += 1
            else:
                print("   ", msg[:150])
        print("[%d/%d] %s  %s  %.0fs  工具 %d 次  输出 %d 字  规范化 %d 项" %
              (idx, len(queries), uid, status, elapsed, len(tools), len(raw), len(fixes)))
        print("    工具:", ", ".join(tools[:8]) if tools else "（无）")

    with io.open(os.path.join(RUNTIME, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump({"model": args.model, "split": args.split, "runs": metrics},
                  f, ensure_ascii=False, indent=2)
    print("\n合法计划 %d/%d，结果目录 results/%s/" % (ok_n, len(queries), METHOD))
    print("token 明细: .runtime/logs/*.usage.jsonl　汇总: .runtime/metrics.json")

    if args.eval:
        cmd = [venv_py, "eval_exp.py", "--splits", args.split,
               "--method", METHOD, "--lang", "zh"]
        print("\n评测:", " ".join(cmd))
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=900)
        print(r.stdout[-2500:])
        if r.stderr:
            print("STDERR:", r.stderr[-600:])


if __name__ == "__main__":
    main()
