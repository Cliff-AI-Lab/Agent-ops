# Agent Ops — Release Notes

## v0.5.0 — 2026-04-26 (`ops` CLI)

### Batch H — `ops` CLI MVP (blueprint M9)

新增 headless 命令行入口 `ops`,通过 HTTP 与运行中的后端通信(默认 `http://127.0.0.1:8000`,可用 `OPS_BASE` env 或 `--base` 覆盖)。这是 blueprint §M9 的最小可用版本,主要用于自动化脚本 / 头无场景 / 其它平台对接。

**子命令(共 6 个)**:

```bash
ops version                              # 服务端版本(从 /openapi.json 读)
ops health                               # GET /health
ops triage "用户消息"                     # POST /api/triage,只决策不执行
ops triage "..." --session <id>          # 带 session 上下文
ops wiki search "周报"                    # /api/wiki/search,合并 tools+capabilities
ops asset list [--status draft|active|archived]
ops asset promote <asset_id> [--by alice]
```

**全局开关**:`--json` 强制原始 JSON 输出(给脚本管道用),`--base URL` 临时覆盖后端地址。

**退出码**(供 shell 链路判断):
- `0` 成功
- `1` 网络错误 / 5xx
- `2` 参数错误(argparse)
- `3` 4xx(认证 / 不存在)

**Console script**:`pyproject.toml` 注册 `ops = "app.cli.ops:main"`,`uv sync` 之后直接 `uv run ops version`。

### 真实联调
启动后端 + `uv run ops triage "用之前那个团队周报工具帮我生成本周周报"` → 真返回 `target=generated_agent · target_id=gen.team-weekly-reporter-1cc29872`。

### 数字
- pytest 224 → **239**(+15 CLI 测试,respx mock + 所有子命令 + 错误路径)
- 改 1 文件 + 加 2 新模块(`app/cli/__init__.py` + `app/cli/ops.py`)
- 无新依赖(只用 stdlib argparse + 已有的 httpx)

---

## v0.4.3 — 2026-04-26 (input-schema coercion patch)

### Batch I-2 — Generated agent invocation respects input_schema

之前 TurnOrchestrator 在 generated_agent 分支无脑包装 `{"message": forwarded_message}`,如果 agent 自己的 `AgentContract.input_schema` 声明了结构化字段,这些字段就丢了。Batch I-2 加了一个**四阶梯式 coercer**(`backend/app/core/dialog/agent_input_coercer.py`):

| 阶梯 | 何时触发 | 成本 |
|---|---|---|
| `empty_schema` | input_schema 为空或缺失 → `{"message": user_msg}` | 0 |
| `heuristic`    | schema 有可识别的字符串字段(`message/query/input/text/prompt/...`)或单一字符串字段 | 0 |
| `llm`          | schema 复杂(多字段/类型混合)→ 单次轻 LLM 调用产出 payload | 1 light call |
| `fallback`     | LLM 失败/不可解析/未提供 → `{"message": user_msg}` + required 字段填空串 | 0 |

每次调用的 coercion_method + missing_required 都写进 `audit_log[i].data`,GET /api/sessions/{id} 能看到完整路由+coercion 历史。

**没有引入新依赖**(没用 jsonschema),自己写了最小的 required-key 校验。

### 数字
- pytest 206 → **224**(+16 coercer + 2 orchestrator integration)
- 改 2 个文件 + 加 1 个新模块
- 无新依赖

---

## v0.4.2 — 2026-04-26 (audit-trail patch)

### Batch K — Per-session triage audit log

`Session` 模型新增 `audit_log: list[dict]` 字段,SQLite `sessions` 表新增 `audit_log_json` 列(老库通过幂等 `ALTER TABLE` 自动加列,零迁移成本)。每条用户消息进 `TurnOrchestrator` 时,以下三类事件会按顺序追加到 audit_log:

| kind | 何时写入 | data 字段 |
|---|---|---|
| `triage_decision` | 每次 turn 的第一步 | `target` `target_id` `reason` `forwarded_message` `fallback_used` `user_message` |
| `agent_invoked` | generated_agent 分支调用结束 | `agent_id` `kind` `used_mock` `error` `elapsed_ms` |
| `workflow_planned` | intent_router 分支 plan 结束 | `intent_summary` `step_count` `spec_valid` `fallback_used` `shortlist_size` `step_capabilities` |

每个 entry 都有 `ts` ISO 时间戳。`GET /api/sessions/{id}` 自动返回 `audit_log` —— 不再依赖 ephemeral 的 trace stream 才能复盘一段对话的路由历史。

### 数字
- pytest 200 → **206**(+6 audit_log 测试,含老库无列兼容)
- 改 4 个文件:`session.py` `turn_orchestrator.py` `db/schema.sql` 加测试
- 无新依赖

### Migration note
跑过 v0.4.0 / v0.4.1 的 `harness.db` 不用手动迁移 —— 第一次 SessionStore.get 调用会自动 `ALTER TABLE ADD COLUMN audit_log_json TEXT`,旧 session 的 audit_log 默认空数组。

---

## v0.4.1 — 2026-04-26 (UI patch)

### Batch J — Trace UI Triage badge

把 Batch Z++/I 在后端做的 Triage 路由决策**真正展现在用户眼前**。每条用户消息发出后,SSE 流入的 `triage_decision` 事件会立刻在对话流里渲染一个居中胶囊(chip),用颜色区分目的地:

| target | 颜色 | 图标 |
|---|---|---|
| `receptionist` | 翠绿 (Nordic emerald) | 💬 |
| `intent_router` | 靛蓝 (indigo) | 📋 |
| `generated_agent` | 琥珀 (amber) | ⭐ |
| `help` | 中性灰 | ❓ |

**交互**:点击胶囊展开 reason / target_id 详情;`fallback_used` 时自动加虚线边表示降级。
**衍生展示**:`agent_invoked` 事件加耗时和 mock 标记;`workflow_planned` 事件显示 step_count + spec_valid。
**i18n**:中英双语标签,跟 `STATE.lang` 切换。
**深色模式**:专门校准的 dark-palette,不刺眼。

### 数字
- pytest 仍 200/200 全绿(无后端代码改动)
- 仅改 `backend/app/static/index.html`(+CSS chip styles · +1 SSE handler · +renderSystemMeta 函数)
- 无新依赖

---

## v0.4.0 — 2026-04-26

### Self-reinforcing loop · 第一跳动态路由 · 跨边界 agent 调用

**Headline**: 用户用一句相似的 SOP 召唤之前生成过的 agent,在同一个对话窗口里就能直接执行 — "今天的产品 → 明天的原子能力" 全链路打通。**200 / 200 tests 全绿**。

#### Batch G — 自我强化循环闭环
- 新增 `app/marketplace/keyword_extractor.py` —— `auto_register` 时调一次 light LLM 抽 5-10 个**业务概念检索关键词**(中英混合,含同义/上位词),取代之前直拷 `special_requirements` 的实现细节
- 修 `auto_register.reload_generated_agents` —— 用 `model_copy(update={"activation_status": "active"})` 覆盖 YAML 旧状态,以 `AssetStore` 为激活态唯一源
- IntentRouter `_gather_candidates` 加跨层 boost(任何 keyword 重叠都纳入 shortlist)+ `min_shortlist=5` 多样性补齐
- 真实 LLM 验证:`gen.team-weekly-reporter-*` 在相关 SOP 的 shortlist 里被选为 step-1

#### Batch Z++ — TriageAgent(第一跳动态路由)
- `app/core/orchestrator/triage.py` —— 4 个 target 枚举:`receptionist / intent_router / generated_agent / help`
- 严格不变式:LLM 编造的 `target_id` 自动降级到 receptionist,失败 fallback 到模板回复
- POST `/api/triage` —— 只返回决策不执行(audit boundary)
- 真实 LLM 3/3 case 全过

#### Batch F — TransportAdapter(跨边界 agent 调用)
- `app/core/transport/` —— ABC + 错误层级(Auth/Timeout/Remote)
- `LocalAdapter` —— in-process binding(sync/async + timeout)
- `HttpAdapter` —— JSON POST,bearer token 来自 `runtime_config` 字面值或 env(永不日志泄露)
- `TransportRegistry` 单例,默认装 local + http;MCP / A2A 留 stub(下批 F-2)
- `CapabilityInvoker` 集成:agent-tier 且非 local transport → 走 adapter,否则原 mock 路径(零回归)

#### Batch I — TurnOrchestrator
- `app/core/dialog/turn_orchestrator.py` —— 把 Triage 接进 `/api/sessions/{id}/turn` 的实际对话流
- 4 个分支:receptionist 走原 flow / generated_agent 走 CapabilityInvoker / intent_router 走 IntentRouter / help 走模板
- SSE 新增事件类型:`triage_decision` `agent_invoked` `workflow_planned`
- API 加 `?triage=false` 旁路开关用于回归测试

### 数字
| 维度 | v0.2.0 → v0.4.0 |
|---|---|
| pytest | 154 → 200 (+46) |
| 文件改动 | +12 新文件 / 改 6 |
| API endpoint | +2 (`/api/triage`, `/api/sessions/.../turn?triage=...`) |
| Batch | G + Z++ + F + I |

### Breaking changes
无。`/api/sessions/{id}/turn` 默认开 triage(可用 `?triage=false` 关掉);未传时行为与 v0.3 完全一致(因为 receptionist 分支与原路径一致)。

### Known follow-ups
- Batch F-2 真实 MCP / A2A adapter
- Batch H CLI(`ops` 命令套件,blueprint M9)
- 把 triage 决策落地到 trace stream UI 的可视化(目前只在 SSE 里)

---

## v0.3.0 — 2026-04-25
- Three-tier ontology pyramid(atom / composite / agent)
- RegistryHub 统一货架 · IntentRouter · WorkflowSpec · WorkflowEngine
- Marketplace · Asset Hub · auto_register · 自我强化资产循环(retrieval gap 仍在)
- distinctive-frontend 风格 UI(Space Grotesk + Inter + Nordic palette + mesh gradient)

## v0.2.0 — 2026-04-24
- Sprint 1 完成 11/11(L1 LLM client + L2 Receptionist + L3 BlueprintPlanner + L4 Validator+Repair+Fallback + L5 UIPipeline + L6 Packager)
- 真实睿动 Claude Sonnet 4.6 端到端跑通
- Vanilla JS 单文件 workbench UI · 6-layer trace stream
- 项目对齐到 blueprint,更名 Agent Ops

## v0.1.0 — 2026-04-23
- Project bootstrap · uv · FastAPI · Pydantic v2 · SQLite
- L1 LLMClient bound to iruidong.com/v1
- Three-tier contracts(RequirementSpec / UIBlueprint / CodeArtifact)
