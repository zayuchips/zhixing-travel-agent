# 智行 × ChinaTravel 参赛模式

这个分支（`feature/chinatravel`）尝试把智行改造成能参加 [ChinaTravel](https://github.com/LAMDA-NeSy/ChinaTravel)（ICLR'26）评测的系统。

**它是实验分支，不影响 main 的产品链路。** 除了 `agent/pi_bridge.py` 和 `config.py` 里一处通用修复（见下），主项目代码没有改动。

## 为什么不能直接拿主项目去跑

ChinaTravel 是**封闭世界**评测：plan 里每个实体名必须和它的静态沙盒完全一致。

| | 主项目 | benchmark 要求 |
|---|---|---|
| 地点来源 | 高德实时 POI（「泉州大开元寺」） | 沙盒里的固定名称（可能叫「开元寺」） |
| 价格 | 门票按人均消费、房价按档位估算 | 沙盒里每条记录的精确 `price` |
| 交通 | 只算城际驾车里程 | 必须给 `TrainID` / `FlightID`、地铁换乘分段 |
| 输出 | 自然语言 + 独立攻略 HTML | 严格 JSON plan（时间不重叠、费用可核算） |

直接把智行的攻略文件喂给评测器，database grounding 这一关就会全挂。

## 改造思路：保留大脑，换掉手脚

```
复用   agent/pi_bridge.py      Pi RPC 子进程、JSONL 收发、会话池
复用   MCP「工具是唯一数据来源」的设计
换掉   mcp/tools/*             → ChinaTravel agent_env 的 21 个沙盒工具
换掉   SYSTEM.md               → benchmark/SYSTEM.md（要求只输出 plan JSON）
换掉   攻略文件产出             → results/zhixing/<uid>.json
```

Pi 仍然是那个自己决定调哪个工具、按什么顺序调的 Agent；变的只是它看到的世界和交付格式。

## 目录

| 文件 | 作用 |
|---|---|
| `SYSTEM.md` | 参赛系统提示：只能用沙盒数据、只输出 JSON、schema 与硬约束 |
| `mcp_launcher.py` | 拉起 benchmark 的 `agent_env.mcp_stdio`，处理 cwd 与 3.12 venv |
| `run.py` | 生成独立 Pi 配置 → 读 query → 驱动 Pi → 校验 plan → 写结果 → 调评测器 |
| `.runtime/` | 运行时生成的配置（含本机绝对路径，不入库） |

## 前置

1. 克隆 ChinaTravel 并建好 3.12 venv、下载沙盒（`HF_ENDPOINT=https://hf-mirror.com`），见主 README §9
2. 沙盒解压到 `chinatravel/environment/database/`
3. 本地 query split：镜像上的 `easy.csv` 是旧 schema（`hard_logic_py` 是字符串），需转成 list 后写成
   `chinatravel/data/<split>/<uid>.json` + `chinatravel/evaluation/default_splits/<split>.txt`
4. 主项目 `.env` 里有可用的模型 Key

## 运行

```bash
cd zhixing-travel-agent

# 跑 1 条并自动评测
python benchmark/run.py --split smoke --limit 1 --eval

# 跑 5 条、换模型、放宽超时
python benchmark/run.py --split smoke --limit 5 --model deepseek-v4.1-flash --timeout 900
```

`run.py` 会：

1. 在 `.runtime/pi_config/` 生成 `mcp.json`（指向沙盒 MCP）和 `models.json`（用 `$DASHSCOPE_API_KEY` 插值）
2. 设 `PI_CODING_AGENT_DIR` 指向它——**不污染 `~/.pi/agent`**
3. 把 `config.BASE_DIR` / `PI_SYSTEM_PROMPT` 换成参赛版，复用 `pi_bridge.PiSession`
4. 收集 `text_delta` 拼回 JSON，用 benchmark 自带的 `output_schema.json` 精确校验
5. 结果写到 `results/zhixing/<uid>.json`，原始输出留在 `.runtime/logs/` 便于排查
6. 带 `--eval` 时调 `eval_exp.py` 出分

## 实测结果（2026-11-08，smoke split 1 条 easy query）

| 指标 | 只靠模型 | 加 `normalize.py` 之后 |
|---|---|---|
| Schema Pass Rate | 100% | **100%** |
| Commonsense micro | 92.86% | **100%** |
| Logical micro / macro | 60% / 0% | **100% / 100%** |
| **All pass ratio** | **0%** | **100%** |

一次完整链路约 77 秒，Pi 自主调了 **34 次**沙盒工具（列车、景点字段、餐厅菜系、市内交通…），
吐出 5615 字的 plan JSON。

只补了 5 个字段就把 All pass 从 0 拉到 100：3 个 `attraction.tickets`、2 个 `taxi.cars`。
模型其实把 `cost = price × tickets` 算对了，纯粹是漏字段触发了 DSL 里的
`activity_tickets(activity) != 1`。这说明**确定性校验层比反复调提示词有效得多**——
记账规则不该交给模型记。

> 1 条样本不构成成绩，这里验证的是「智行的 Pi + MCP 架构能迁移到另一个封闭世界并跑出合法计划」。
> 要真正看分数，得跑完整的 easy 300 条 / Phase 2 100 条。

### 混合 18 条（2026-11-08，才是真实水平）

`easy 12（1/2/3 天各 4） + medium 3 + human 3`，固定随机种子 20261108，
用 `python benchmark/report.py --split mixed18` 复现：

| 子集 | N | DR | EPR.micro | LPR.micro | LPR.macro | **FPR** |
|---|---:|---:|---:|---:|---:|---:|
| easy | 12 | 66.7% | 95.5% | 98.8% | 91.7% | **25.0%** |
| medium | 3 | 0% | 75.0% | 88.0% | 0% | 0% |
| human | 3 | 33.3% | 88.1% | 94.4% | 66.7% | 0% |
| **全部** | **18** | **50.0%** | **90.9%** | **96.3%** | **72.2%** | **16.7%** |

和 1 条 smoke 的满分差距很大，原因很明确：**DR 只有 50%**——复杂题（多天、多约束）上
模型有一半吐不出合法 JSON。但只要 plan 交出来了，约束满足率相当高（EPR 90.9%、LPR.micro 96.3%）。
所以瓶颈不是"排得差"，是"交不稳"。这正好指明了下一步该做确定性排程器兜底，而不是继续调提示词。

**与论文 Table 3 同指标对照**（不同样本量、不同评测脚本，只能看数量级，不是官方排名）：

| 方法 | Easy FPR | Human-Val FPR |
|---|---:|---:|
| 官方纯 LLM（Act / ReAct，DeepSeek-V3 / GPT-4o） | 0 ~ 5.33 | 0 ~ 2.59 |
| **智行参赛（本分支，easy/human 子集）** | **25.0** | **0** |
| 官方 NeSy Planning + DeepSeek-V3 | 52.6 | 37.0 |
| 官方 NeSy Planning + GPT-4o | 60.6 | 27.9 |
| 官方 NeSy + Oracle Translation | 74.0 | 45.4 |

位置：easy 上明显超过纯神经基线，但只有符号方法的一半；human 上仍是纯 LLM 水平。
要拿官方榜单位次，得用官方 test split、官方评测器和同样的 oracle 设置提交，本地给不了。

**成本实测**（百炼 `deepseek-v4.1-flash`，Pi 上报的全局累积 token）：

| 项 | 数值 |
|---|---|
| 平均每条 | 输入 2,933 / 缓存命中 34,972 / 输出 6,368 ＝ 44,273 tokens |
| 单条费用 | **¥0.032（闲时）～ ¥0.064（忙时）** |
| easy 300 条 | ¥9.6 ～ ¥19.1 |
| human 154 条 | ¥4.9 ～ ¥9.8 |
| Phase 2 2000 条 | ¥63.8 ～ ¥127.6 |
| 实测耗时 | 18 条 35 分钟，约 **118 秒/条**；外推 easy 300 条约 10 小时 |

对照论文 Table 2：Act 是 88K input / 2K output / DeepSeek $0.007 每条；
我们 token 更少也更便宜，主要因为模型单价低且大量上下文命中了隐式缓存。
真正贵的是**时间**——多天题上模型要调 40~56 次工具。

## 顺带修的一个真 bug

主项目的 `SYSTEM.md` **从来没有被 Pi 加载过**：Pi 自动发现的是 `AGENTS.md` / `CLAUDE.md`，
而 `pi_bridge._args()` 也没传 `--system-prompt`。所以之前反复调整的「不要叙述执行过程」
「不要逐条转述专家意见」等约束，实际只起了很弱的作用。

本分支在 `pi_bridge._args()` 里加了显式的 `--system-prompt <path>`，并在 `config.py` 增加
`PI_SYSTEM_PROMPT` 可覆盖。这个修复对主项目同样适用，已单独回合到 main。

## 已知限制

- 已跑 18 条混合集（见上），但**不是官方 split 的完整成绩**。没有做批量并发、断点续跑；
  完整跑 300 条按实测 118 秒/条约需 10 小时。
- 最大短板是 **DR 50%**：复杂题上模型吐不出合法 JSON。目前没有确定性排程器兜底，
  交不出来就是 0 分；这是下一步最该补的地方。
- `normalize.py` 补的是**记账字段**，不是排程逻辑。复杂约束（多天、房型、菜系组合、
  预算卡边、返程车次衔接）仍要靠模型自己排对，目前没有确定性求解器兜底。
- 模型偶尔会在长 prompt 下只输出半截 JSON。`run.py` 会把原始输出留在
  `.runtime/logs/` 并标 FAIL，不会拿编造的 plan 去凑数。
- 这个实验的价值在于**验证架构可迁移 + 找到「模型决策 / 确定性校验」这条分工线**，
  不在于刷榜。要和 SOTA 比，还得把 benchmark 的符号求解器接进来做兜底。
