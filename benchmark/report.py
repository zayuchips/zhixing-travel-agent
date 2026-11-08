# -*- coding: utf-8 -*-
"""汇总一次批量跑分：按 easy / medium / human 分组给分数，并核算模型费用。

用法：
    python benchmark/report.py --split mixed18

读三处数据：
    _bench/ChinaTravel/eval_res/splits_<split>/zhixing/*.csv   评测器输出
    _bench/ChinaTravel/chinatravel/data/<split>/*.json         题目所属子集
    benchmark/.runtime/logs/*.usage.jsonl                      Pi 上报的 token 用量
"""
import argparse
import csv
import io
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEFAULT_ROOT = os.path.normpath(os.path.join(BASE, "..", "_bench", "ChinaTravel"))
RUNTIME = os.path.join(BASE, "benchmark", ".runtime")

# 百炼 deepseek-v4.1-flash（2026-11 官网价，元 / 百万 tokens）
PRICE = {
    "闲时": {"input": 1.0, "cache": 0.1, "output": 4.0},
    "忙时": {"input": 2.0, "cache": 0.2, "output": 8.0},
}


def _rows(path):
    with io.open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _last_usage(uid):
    p = os.path.join(RUNTIME, "logs", uid + ".usage.jsonl")
    if not os.path.isfile(p):
        return None
    last = None
    for line in io.open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        u = (json.loads(line).get("usage") or {})
        if (u.get("totalTokens") or 0) > 0:
            last = u
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="mixed18")
    ap.add_argument("--method", default="zhixing")
    args = ap.parse_args()

    root = os.environ.get("CHINATRAVEL_ROOT") or DEFAULT_ROOT
    er = os.path.join(root, "eval_res", "splits_" + args.split, args.method)
    qdir = os.path.join(root, "chinatravel", "data", args.split)

    uid2subset = {}
    if os.path.isdir(qdir):
        for fn in os.listdir(qdir):
            if fn.endswith(".json"):
                q = json.load(io.open(os.path.join(qdir, fn), encoding="utf-8"))
                uid2subset[fn[:-5]] = q.get("_subset") or "unknown"

    schema = {r["data_id"]: int(float(r["schema"])) for r in _rows(os.path.join(er, "schema.csv"))}
    commonsense = {}
    for r in _rows(os.path.join(er, "commonsense.csv")):
        vals = [int(float(v)) for k, v in r.items() if k != "data_id"]
        commonsense[r["data_id"]] = (1.0 - sum(vals) / len(vals)) if vals else 0.0
    logical = {}
    for r in _rows(os.path.join(er, "logical_py.csv")):
        vals = [int(float(v)) for k, v in r.items() if k != "data_id"]
        logical[r["data_id"]] = (sum(vals) / len(vals) if vals else 0.0,
                                 1.0 if vals and all(vals) else 0.0)

    def stat(uids):
        if not uids:
            return None
        n = len(uids)
        return {
            "n": n,
            "DR": sum(schema.get(u, 0) for u in uids) / n,
            "EPR_micro": sum(commonsense.get(u, 0) for u in uids) / n,
            "LPR_micro": sum(logical.get(u, (0, 0))[0] for u in uids) / n,
            "LPR_macro": sum(logical.get(u, (0, 0))[1] for u in uids) / n,
            "FPR": sum(1 for u in uids if schema.get(u, 0) == 1
                       and commonsense.get(u, 0) >= 0.999
                       and logical.get(u, (0, 0))[1] >= 1.0) / n,
        }

    print("=" * 68)
    print("ChinaTravel 跑分汇总  split=%s  method=%s" % (args.split, args.method))
    print("=" * 68)
    header = "%-10s %4s %8s %10s %10s %10s %8s" % (
        "子集", "N", "DR", "EPR.micro", "LPR.micro", "LPR.macro", "FPR")
    print(header)
    print("-" * 68)
    for subset in ("easy", "medium", "human", "unknown"):
        uids = [u for u in schema if uid2subset.get(u) == subset]
        s = stat(uids)
        if not s:
            continue
        print("%-10s %4d %7.1f%% %9.1f%% %9.1f%% %9.1f%% %7.1f%%" % (
            subset, s["n"], s["DR"] * 100, s["EPR_micro"] * 100,
            s["LPR_micro"] * 100, s["LPR_macro"] * 100, s["FPR"] * 100))
    all_s = stat(list(schema))
    if all_s:
        print("-" * 68)
        print("%-10s %4d %7.1f%% %9.1f%% %9.1f%% %9.1f%% %7.1f%%" % (
            "全部", all_s["n"], all_s["DR"] * 100, all_s["EPR_micro"] * 100,
            all_s["LPR_micro"] * 100, all_s["LPR_macro"] * 100, all_s["FPR"] * 100))

    # ---- 成本 ----
    usages = [(u, _last_usage(u)) for u in schema]
    usages = [(u, x) for u, x in usages if x]
    if not usages:
        print("\n没有 token 用量记录（.runtime/logs/*.usage.jsonl）")
        return
    tin = sum(x.get("input", 0) for _, x in usages)
    tcache = sum(x.get("cacheRead", 0) for _, x in usages)
    tout = sum(x.get("output", 0) + x.get("reasoning", 0) for _, x in usages)
    n = len(usages)
    print("\n" + "=" * 68)
    print("token 用量（%d 条，Pi 上报的全局累积值）" % n)
    print("=" * 68)
    print("  输入(非缓存) %9d  |  缓存命中 %9d  |  输出 %8d" % (tin, tcache, tout))
    print("  平均每条：输入 %.0f / 缓存 %.0f / 输出 %.0f / 合计 %.0f tokens" % (
        tin / n, tcache / n, tout / n, (tin + tcache + tout) / n))

    print("\n%-8s %12s %12s %12s %12s" % ("时段", "单条(元)", "easy 300", "human 154", "Phase2 2000"))
    print("-" * 68)
    for label, p in PRICE.items():
        per = (tin / n / 1e6 * p["input"] + tcache / n / 1e6 * p["cache"]
               + tout / n / 1e6 * p["output"])
        print("%-8s %12.4f %12.2f %12.2f %12.2f" % (
            label, per, per * 300, per * 154, per * 2000))

    print("\n口径：百炼 deepseek-v4.1-flash 官网价；"
          "缓存命中按 input(cache) 计；外推 = 单条均值 × 条数，未计失败重试。")
    print("DR=Delivery Rate / EPR=Environmental / LPR=Logical / FPR=Final Pass Rate")


if __name__ == "__main__":
    main()
