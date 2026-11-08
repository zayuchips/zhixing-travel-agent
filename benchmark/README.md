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

## 顺带修的一个真 bug

主项目的 `SYSTEM.md` **从来没有被 Pi 加载过**：Pi 自动发现的是 `AGENTS.md` / `CLAUDE.md`，
而 `pi_bridge._args()` 也没传 `--system-prompt`。所以之前反复调整的「不要叙述执行过程」
「不要逐条转述专家意见」等约束，实际只起了很弱的作用。

本分支在 `pi_bridge._args()` 里加了显式的 `--system-prompt <path>`，并在 `config.py` 增加
`PI_SYSTEM_PROMPT` 可覆盖。这个修复对主项目同样适用，已单独回合到 main。

## 已知限制

- 目前只验证了 1 条 easy query。没有做批量并发、断点续跑和 token 统计；
  完整跑 300 条要按每条 ~80 秒估，且模型费用不低。
- `normalize.py` 补的是**记账字段**，不是排程逻辑。复杂约束（多天、房型、菜系组合、
  预算卡边、返程车次衔接）仍要靠模型自己排对，目前没有确定性求解器兜底。
- 模型偶尔会在长 prompt 下只输出半截 JSON。`run.py` 会把原始输出留在
  `.runtime/logs/` 并标 FAIL，不会拿编造的 plan 去凑数。
- 这个实验的价值在于**验证架构可迁移 + 找到「模型决策 / 确定性校验」这条分工线**，
  不在于刷榜。要和 SOTA 比，还得把 benchmark 的符号求解器接进来做兜底。
