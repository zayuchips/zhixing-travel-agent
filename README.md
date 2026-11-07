# 智行 · 出行规划智能体

> **你说一句，我做一路。**
> 广东工业大学 计算机学院 计科 24 级 78 班 · 软件工程 · 团队项目第一次作业
> 在线演示：（部署后填写）　｜　需求文档：[docs/需求规格说明书.md](docs/需求规格说明书.md)

一个**能说也会做**的出行助手：用户用自然语言说出需求，系统自己判断该查天气还是算路线，
调用真实接口拿数据，再排成一张能照着走的逐日行程表。

关键点：**天气和路线是真的查来的**（open-meteo / OSRM，无需密钥），
而且每次调用了哪个工具、参数是什么、花了多少毫秒，界面上都能展开核对。

![界面截图](docs/screenshots/01-chat.png)

---

## 1 快速开始

```bash
cd zhixing-travel-agent
python3 server.py
```

打开 http://127.0.0.1:8000 ，用体验账号登录：

```
用户名：demo
密码：  demo1234
```

**没有任何第三方依赖**（只用 Python 标准库），不需要 pip install、不需要数据库服务、不需要 API Key。

常用环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | 8000 | 端口 |
| `BIND` | 127.0.0.1 | 想让人从别的设备访问就设 `0.0.0.0` |
| `DB_PATH` | data/zhixing.db | SQLite 文件位置 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 空 | 填了就走大模型理解（任意 OpenAI 兼容端点） |
| `LLM_PROVIDER` | auto | 设为 `demo` 可强制使用规则引擎 |

```bash
# 让同宿舍的同学/老师的手机能打开
BIND=0.0.0.0 python3 server.py

# 接 DeepSeek 做语义理解（不填也能用，只是理解层换成规则）
LLM_BASE_URL=https://api.deepseek.com/v1 \
LLM_API_KEY=sk-xxx LLM_MODEL=deepseek-chat \
python3 server.py
```

## 2 它能做什么

| 你说 | 它做 |
|---|---|
| 长沙未来三天天气 | 调 `weather.get_forecast`，返回真实预报 + 带伞建议 |
| 从广州到长沙怎么走，开车要多久 | 调 `route.plan_route`，返回 OSRM 真实路网里程与耗时，并提示「该改高铁」 |
| 11 月 12 号从广州去长沙玩 3 天，预算 1500，想逛博物馆和吃辣 | **先算路线、再排行程**（两次工具调用），输出逐日行程 + 花费估算 + 预算结论 |
| 帮我排个行程 | 不猜城市，直接追问目的地 |

排行程时会读未来天气：**那天下雨，就把户外点位换成室内**；
室内点位排完了会如实说「剩余点位请按天气临时取舍」，不会假装排得下。

## 3 架构

```
浏览器（原生 HTML/CSS/JS，无构建）
        │ fetch + Cookie
server.py（标准库 ThreadingHTTPServer：路由 / 鉴权 / 静态文件 / MCP 网关）
        │
   agent/  ──  nlu.py（意图与槽位）  orchestrator.py（工具编排 + 表达）
        │ registry.call_tool
   mcp/    ──  protocol.py（JSON-RPC 2.0）  registry.py（注册/校验/兜底/轨迹）
        │      tools/ geo · weather · route · itinerary
   外部服务 ──  open-meteo（天气/地名）· OSRM（路网）· 可选大模型
        │
   db.py   ──  SQLite：users / sessions / messages / tokens
```

设计取舍：

- **零依赖**：答辩机房 pip 装不上包是常态，`python3 server.py` 必须能跑。
- **演示模式是正式需求**：没有 Key 也要能完整演示，但界面必须如实显示当前用的是规则引擎。
- **工具层用 MCP**：同一份工具表既能被内部智能体调用，也能被外部 MCP 客户端
  （如 Claude Desktop）挂载，见 `mcp/server_stdio.py`。
- **诚实优先**：估算的里程会标「估算」，没拿到数据就说没拿到，绝不编。

## 4 MCP 工具

| 工具 | 作用 | 数据源 |
|---|---|---|
| `geocode.search_city` | 地名 → 经纬度/行政区 | open-meteo geocoding |
| `weather.get_forecast` | 未来 1-16 天天气 + 出行建议 | open-meteo forecast |
| `route.plan_route` | 两地里程与耗时（驾车真实路网） | OSRM |
| `itinerary.build_plan` | 按天数/兴趣/预算/天气排行程 | 本地 POI 库 + 天气 |

用 curl 直接按 MCP 协议调用（不需要登录）：

```bash
curl -s localhost:8000/api/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
curl -s localhost:8000/api/mcp -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
  "params":{"name":"weather.get_forecast","arguments":{"city":"长沙","days":3}}}'
```

挂到 Claude Desktop：把 `mcp/server_stdio.py` 按文件头的说明写进
`claude_desktop_config.json` 即可。

## 5 验收

```bash
python3 server.py                  # 终端 A
python3 tests/acceptance.py        # 终端 B
```

脚本对**真实运行的服务**发 43 项断言，覆盖登录鉴权、会话隔离、三个核心能力、
多工具编排、MCP 协议、目录穿越与 XSS、统计接口：

```
通过 43 项，失败 0 项
全部通过 ✅
```

最近一次运行（2026-11-07，本机 macOS / Python 3.9.6）的真实耗时：
天气单轮 1.4 秒、路线单轮 2.1 秒、「路线 + 行程」双工具 3.4 秒。

## 6 目录结构

```
zhixing-travel-agent/
├─ server.py                 唯一入口：HTTP 服务 + 路由 + MCP 网关
├─ config.py                 配置（全部可用环境变量覆盖）
├─ db.py                     SQLite 数据层（账号/会话/消息/token）
├─ agent/
│  ├─ nlu.py                 理解层：意图分类 + 槽位抽取（规则）
│  └─ orchestrator.py        编排层：调工具、写回复、记轨迹
├─ llm/
│  └─ __init__.py            大模型接入（OpenAI 兼容）+ 演示模式 + 兜底
├─ mcp/
│  ├─ protocol.py            JSON-RPC 2.0 / MCP 消息
│  ├─ registry.py            工具注册表（校验、异常兜底、调用轨迹）
│  ├─ server_stdio.py        独立 MCP Server（stdio，可挂 Claude Desktop）
│  ├─ net.py                 标准库 HTTP 客户端（超时 + 重试）
│  └─ tools/
│     ├─ geo.py              地名解析（含同名村庄纠错）
│     ├─ weather.py          天气（WMO 编码 → 中文）
│     ├─ route.py            路线（OSRM + 步行/骑行估算）
│     └─ itinerary.py        行程排程（兴趣打分 / 雨天改室内 / 预算）
├─ data/
│  ├─ cities.json            9 座城市 × 8 个精选点位
│  └─ zhixing.db             运行时生成的数据库
├─ static/                   前端（index.html / style.css / app.js）
├─ docs/
│  ├─ 需求规格说明书.md       按国标 SRS 结构写的需求文档
│  ├─ 团队展示与选题报告.md
│  ├─ 博客随笔.md            提交到班级博客的正文
│  └─ screenshots/
└─ tests/
   └─ acceptance.py          43 项真实 HTTP 验收
```

## 7 已知局限

1. POI 库只覆盖 9 座城市（广州、长沙、北京、上海、成都、西安、杭州、重庆、深圳），
   其余城市返回「通用框架」并明确提示；
2. 规则理解层靠关键词，绕的说法会漏；接大模型后才算真正的语义理解；
3. 步行/骑行耗时是按直线距离估算的，只有驾车走真实路网；
4. 天气最多 16 天，更远的日期显示「暂无预报」，不拿气候平均值猜测；
5. 单实例 SQLite、无速率限制、无找回密码——定位是课程原型，不是生产系统。

## 8 声明

本项目代码由本组独立实现，未复制同学仓库；使用的公开接口均为免密钥的公共服务
（open-meteo、OSRM），POI 数据为小组自行整理的公开景点信息。
