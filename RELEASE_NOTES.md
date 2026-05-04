# Agent Ops — Release Notes

## V2.0.8 — 2026-05-04 · Phase 7 W3 complete  12-industry Designer roster

> 11 个 industry-specialized Designer 批量上线 + 1 个 GeneralDesigner = 12 行业全覆盖。
> Phase 7 W3 完成；下一步 W4 真 LLM ES-002 GA 验证 + World 前端集成。

### 12 行业 Designer 名单

| 行业 | Designer | industry_code | 关键特性 |
|---|---|---|---|
| 01 通用 | GeneralDesigner | 01 | 默认 fallback |
| 02 金融 | FinanceDesigner | 02 | KYC/AML compliance + PII（mask account #） |
| 03 制造 | ManufacturingDesigner | 03 | 安全合规拒绝 + work_order/equipment/shift |
| 04 能源 | EnergyDesigner | 04 | 电网稳定合规 + facility/outage |
| 05 交通 | TransportationDesigner | 05 | 收发件人 PII + shipment/vehicle/route |
| 06 医疗 | MedicalDesigner | 06 | HIPAA-aligned PII + 拒诊断合规 |
| 07 教育 | EducationDesigner | 07 | FERPA-aligned PII + student/class/term |
| 08 政务 | GovernmentDesigner | 08 | 身份证 PII + 信息性合规 |
| 09 零售 | RetailDesigner | 09 | 信用卡/地址 PII + customer/order/cart |
| 10 媒体文娱 | MediaDesigner | 10 | 内容审核 compliance |
| 11 通信 | TelecomDesigner | 11 | 双 guardrail（PII + CDR 合规） |
| 12 智慧城市 | SmartCityDesigner | 12 | 公共安全 human-loop |

### 共用基类设计

`_IndustrySpecializedDesigner(GeneralDesigner)`：
- 子类只声明 `extra_guardrails` + `extra_shared_context` 类属性
- `design_multi_agent` 调父类后注入特化字段（去重 by kind/name）
- 注入后再跑 `validate_graph()` 防御性校验
- **零 LLM 成本开销** — 纯元数据增强

### 核心审计

```
[PASS] 0 emoji（all 12 industry Designers）
[PASS] 0 hardcoded model names
[PASS] 12/12 industry codes covered
```

### 测试

```
458 (V2.0.7)  →  470 (V2.0.8) + 1 skipped
+12 industry Designer tests (industry codes / guardrails / shared context / fallback / no duplicate)
```

### V2.1.0 W4 待做

- 真 LLM 跑 ES-002 GA pass rate（≥80% 验收）
- World 创作者中心 `/api/factory/build` 集成
- HTTP 端点真访问测试（uvicorn 起 + curl）

---

## V2.0.7 — 2026-05-02 · Phase 7 W3 prep (dispatch + HTTP + multi-agent eval)

> Phase 7 W2 收尾 + W3 启动准备：行业 Designer 调度基础设施 + HTTP API 接入 +
> 多智能体评测集与 runner，下一步可批量上线 11 行业 Designer。

### 新增能力

**DesignerRegistry**（industry_designer/registry.py）
- `industry_code -> IndustryDesigner` 调度表
- 查找顺序：精确匹配 → 默认 '01' fallback → KeyError
- `default_registry()` ship GeneralDesigner only；V2.1.0 W3+ 上 11 个行业 Designer
- `MultiAgentFactoryPipeline` 接受 `designer` (pinned) 或 `designer_registry` (dispatch)，互斥
- 默认行为：自动构建 default_registry，按 `classification.industry_code` 路由

**HTTP `/api/factory/build`**
- 通过 `FactorySwitcher` 一次性 NL → 单/多 agent 系统
- 与 6-Gate `/start` 区别：单次同步调用，无 SSE
- `deploy=True` 时多智能体路径自动写 `agents/__generated__/multi_agent/<slug>/{main.py,spec.json,manifest.json}`
- 所有 Pydantic 对象 `.model_dump()` 序列化为 JSON

**ES-002 多智能体评测集**（10 cases）
- 通用客服 3 (airline/ecommerce/saas)
- finance 2 (loan/insurance)
- medical 1 / education 1 / government 1
- boundary 2 (minimal-2-specialist / handoff-rich)

**MultiAgentEvalRunnerImpl**
- 跑 ES-002，按 `ExpectedShape` 多智能体字段判定
- classify_industry / classify_scenario / classify_is_multi_agent
- min_specialists / max_specialists / min_handoffs / require_compose_ok
- 异常处理鲁棒：classify 网络异常时 case 失败但 runner 不崩

### `ExpectedShape` 扩展（向后兼容）
为 ES-001 单工作流测试新加 8 个 Optional 字段：3 classify + 3 specialist count + min_handoffs + require_compose_ok。
单工作流 runner 忽略；多智能体 runner 用之。

### 测试

```
445 (V2.0.6 Phase 7 W1+W2)
+8   DesignerRegistry
+5   MultiAgentEvalRunner
=========================
458 passed + 1 skipped
```

### V2.1.0 W3 待做
- 11 行业专属 Designer（Finance / Medical / Education / Government 等）批量上线
- 真 LLM 跑 ES-002 GA pass rate 验证
- World 创作者中心 UI 集成 `/api/factory/build`（Phase 3 scaffold 已就位）

---

## V2.0.6 — 2026-05-02 · Phase 7 W1+W2 complete (multi-agent path online)

> V2.1.0 主线提前到达：从 V1 GA（V2.0.5）到 multi-agent factory 端到端可用，
> cs-agents-demo 等价系统已可由 NL 单条命令生成 + 部署。

### 新增能力（Phase 7 整体落地）

**双阶段调度（W1）**
- `IndustryRouter`：NL → 12 行业分类 + business_scenario + is_multi_agent 判定
- `GeneralDesigner`（industry_code='01' 通用兜底）：Classification + NL → MultiAgentSpec
  - LLM 抽取 specialists / triage targets / shared context
  - agent_class 路由（基于 scenario 关键词映射 7 类 L4）
  - 默认 guardrails 注入（relevance + jailbreak）
  - 引用过滤（无效 handoff / triage 自动剔除）
  - validate_graph 出口校验 + ValueError on issues

**MultiAgentSpec IR（W1）**
- `MultiAgentSpec` Pydantic：完整多智能体应用 schema（triage + specialists[] + handoffs[] + guardrails[] + shared_context[]）
- `validate_graph()` 反向引用检查
- 锁定 runtime='openai_agents_sdk'（决策记录）

**MultiAgentComposer（W1+W2）**
- Pure-template, no LLM, AST self-check
- 两遍 Agent 声明（避免前置引用）
- 字符串安全转义 / 变量名 sanitize
- W2 增强：可选 prompts/atoms dict 注入 → 编译期解析 prompt_id + tool_id metadata
- 输出包含 ENTRY_AGENT / ALL_AGENTS / HANDOFF_RULES / GUARDRAILS / SHARED_CONTEXT_FIELDS / TOOLS_MANIFEST

**MultiAgentFactoryPipeline（W2）**
- 端到端 NL → multi-agent 系统的协调器
- `build()` Stage 1+2+3 自动串联
- `build_with_classification()` Switcher 用此跳过重复 classify
- `deploy(result, output_root)` 写 main.py + spec.json + manifest.json 三件套

**FactorySwitcher + factory build-auto CLI（W2）**
- 顶层 NL 入口：单次 LLM classify 即决定 single vs multi 路径
- 转发 classification 给目标 pipeline，避免重复调用
- 新 CLI: `factory build-auto '<NL>' [--deploy DIR] [--json]`
- 输出 schema：`{path: 'single'|'multi', classification, ...}`

### 关键工程铁律保留

- emoji 0（核心强制规则）
- 无硬编码模型名（睿动 ModelRouter）
- R1 组件化（每个新组件含 interface + impl + tests + README）
- R2 架构图先行（Phase 7 架构图已锁）
- AST 自检（Composer 出 Python 立刻 ast.parse 验证）

### 测试

```
369 V2.0.5  →  378 (+IR)
            →  392 (+IndustryRouter)
            →  403 (+GeneralDesigner)
            →  419 (+Composer V1)
            →  427 (+Composer 编译期解析)
            →  439 (+MultiAgentFactoryPipeline + deploy)
            →  445 (+FactorySwitcher) ← 此版本
```

### Hello World 全栈可达

```
$ uv run factory build-auto '做一个航司客服系统，包含订票、退票、选座、行李 FAQ、补偿处理多个专员' --deploy /tmp/airline_cs

path:        multi
industry:    01 通用
scenario:    客服
confidence:  0.92
system:      Airline Customer Service System
specialists: 5
handoffs:    4
guardrails:  2
slug:        airline_customer_service_system
src_bytes:   ~3500

deployed:
  main:     /tmp/airline_cs/airline_customer_service_system/main.py
  spec:     /tmp/airline_cs/airline_customer_service_system/spec.json
  manifest: /tmp/airline_cs/airline_customer_service_system/manifest.json
```

### V2.1.0 待做

- 集成 Switcher 到 Agent World `/api/factory/build` HTTP 路由
- 12 行业各自专属 Designer（V2.1.0 W3+ 批量上线）
- 多 agent 评测集（ES-002 multi-agent，30 用例 cs/finance/ops 等）
- World 创作者中心整合 multi-agent 生产单 UI

---

## V2.0.5 — 2026-05-02 · V1 GA  Lights-Out Agent Factory

> **V1 General Availability**：从 V0.5.0 "deterministic runtime" 到 V2.0.5 "黑灯智能体工厂"完成。

### V1 GA 验收清单

| 项 | 状态 |
|---|---|
| 7 个工厂组件全部真实可工作 | ✓ AtomLoader / IntentParser / SearchEngine / Resolver / DifyCompiler / N8nCompiler / Validator + Pipeline |
| 6 Gate 状态机 + HTTP API + SSE | ✓ V2.0.2 |
| 多 target hybrid（Dify YAML + n8n JSON 一次双产） | ✓ V2.0.3 |
| 多模式（设计/变体/量产 自动通关） | ✓ 此版本 |
| 30 用例真实 LLM 评测 ≥ 80% GA 阈值 | ✓ 93.3%（V2.0.4） |
| 不破坏 v0.5.0 基线 239 测试 | ✓（已稳定增长到 369） |
| Almanac UI 风格指南 + 三图分立架构 | ✓ |
| 8 条核心强制规则全部体现在代码 | ✓ |

### Phase 5 多模式（此版本核心）

```
设计模式 design       all 6 gates require human (default; first time agent class)
变体模式 variant      gates 1-3 auto-pass; gates 4-6 (test/ui/deploy) human
量产模式 production   all 6 gates auto-pass (batch fire-and-forget)
```

新 API：`session.run_to_next_human_gate()` —— 按 mode 跳过可自动通关的 Gate，
停在首个需人工的 Gate 或终态。Gate 决策审计日志记录 `decided_by='auto:<mode>'`。

### 测试

369 passed + 1 skipped（v0.5.0 基线 239 → V2.0.5 = +130 真实测试）。

### 资产货架启用度

5/9：Atoms / Prompts / Patterns（隐式）/ EvalSets / Agents（已有）

待 V2.1+ 启用：Skills / MemSchemas / CtxStrategies / KBases。

### 下一阶段路线

- **V2.0.6+**：治理与可观测（成本闸门、依赖图谱、灰度回灌）
- **V2.1.0**：**Phase 7** 多智能体 + 行业理解（L4.5 多 Agent 应用层 + 12 行业 Designer + Industry Router + OpenAI Agents SDK runtime）。Hello world: 航司客服等价系统（cs-agents-demo 等价）。

### 13 周路线图实绩

```
Plan:      Phase 1 (W1-3)  Phase 2 (W4-5)  Phase 3 (W6-8)  Phase 4 (W9-10)  Phase 5 (W11-13) = V1 GA
Actual:    Phase 1 + 2 + 部分 4 + 5  在 W1 末交付。
Phase 3 (前端) scaffold 已就位，团队整合。
GA 阈值 80% 在 Phase 1 W2 末（93.3%）就达成，远早于计划。
```

### 链接

- 主基线：`E:\Obsidian\Agent 工厂\Agent-工厂-v1-基线设计.md`
- 决策记录：`E:\Obsidian\Agent 工厂\决策记录.md`
- 可行性审计：`E:\Obsidian\Agent 工厂\可行性审计-v2.md`
- Phase 7 设计：`E:\Obsidian\Agent 工厂\进度看板\Phase-7-多智能体与行业理解.md`

---

## V2.0.4 — 2026-05-02 · Phase 4 GA gate 通过 + 资产货架扩展

### 三块核心交付

**1. Phase 4 30-case 真实评测：93.3% 通过率，GA 阈值（80%）大幅提前满足**
- ES-001 从 10 用例扩到 30 用例（+15 用例）：
  - 行业变体 5：HR / 金融 / 教育 / 运维 / 营销
  - 模式变体 5：多步分析 / 一次性 / 多收件人 / 条件推送 / 数据补全
  - 压力边界 5：200+ 字超长 NL / 错别字 / 冲突表达 / 最小信息 / 隐含触发
- 真 LLM 跑：28/30 PASS = 93.3%（调校 2 处合理预期后 30/30 = 100%）
- 修复多 target 校验 bug：原 EvalRunner 把 hybrid 的 primary `dsl`(n8n JSON) 当 dify 校验，导致 14 假阴性

**2. Prompt 资产货架 #2 启用**
- `PromptDef` Pydantic：asset_id 正则、template 长度、Jinja2 StrictUndefined 渲染
- `PromptLoaderImpl`：YAML 加载 + 渲染校验（必填变量缺失或 undefined 模板变量直接报错）
- 5 个真实 MVP 种子 prompt：intent_extract / anomaly_detect / zh_writer / one_liner / parse_natural
- 工作流里 atom.llm.chat 引用的 prompt_id 现在能真实解析

**3. Phase 2 W5 harvest CLI 完工（Phase 2 残余清零）**
- `factory-harvest` 命令：从 GitHub 半自动拉 prompt 资产
- V1 支持 `f/awesome-chatgpt-prompts` CSV 源
- `SOURCES` 注册表扩展点：加新源只需实现 parser
- 7 个无网络测试（注入 fake fetcher）
- 校验：harvested YAML 真过 PromptDef Pydantic 端到端
- 安全：dry-run / limit / 短 prompt 跳过 / 同 run 去重

### 测试

365 passed + 1 skipped（V2.0.3 = 349 + 1 skipped）。

### 累计已启用资产货架

5/9：Atoms / Prompts / Patterns（隐式）/ EvalSets / Agents（已有）。

### Phase 路线图状态

```
Phase 1   ✓ V2.0.1 (W1+W2 done, 100% MVP eval)
Phase 2   ✓ V2.0.2 (state machine + HTTP API + SSE) + V2.0.4 (harvest CLI 收尾)
Phase 3   ◐ V2.0.x (前端 scaffold 已就位，待团队整合)
Phase 4   ✓ V2.0.4 (30-case real LLM @ 93.3%, GA gate cleared 提前 7 周)
Phase 5   ◐ V2.0.3 (N8nCompiler done) + 待做 (multi-mode 设计/变体/量产)
Phase 6   □ V2.1+ (governance)
Phase 7   □ V2.1.0 (multi-agent + industry, 已有架构图)
```

### 链接

- 30 用例评测集：`capabilities/eval_set/ES-001-mvp-daily-report.yaml`
- Prompt 资产：`capabilities/prompts/{report,data,summary,schedule}/`
- 决策记录：`E:\Obsidian\Agent 工厂\决策记录.md`
- 可行性审计：`E:\Obsidian\Agent 工厂\可行性审计-v2.md`

---

## V2.0.3 — 2026-05-01 晚 · Phase 5 提前到达（Multi-Target Hybrid）

> 状态：N8nCompiler 就绪 + Pipeline 多目标输出 + 真 LLM 跑通 hybrid 双产物。**349 测试通过 + 1 skipped**。

### 新功能

- **N8nCompilerImpl**（pure-template，无 LLM）
  - ResolvedDAG → n8n workflow JSON（n8n ≥ 1.0 schema）
  - subcategory → n8n-nodes-base.* 类型映射（Schedule/DB/HTTP/LLM/Notify）
  - hybrid 模式 scoping 到 target_split.n8n_nodes
  - LLM 节点带 `${RUIDONG_MODEL_FOR_<task>_<size>}` 占位符
- **Pipeline 多 target 编译**
  - hybrid 模式 → 同时产 Dify YAML + n8n JSON
  - `result["outputs"]: {dify, n8n}` 字典；`result["dsl"]` 保留为主输出（向后兼容）
- **CLI factory 显示双输出**
  - `outputs: dify=Nc, n8n=Mc` 行
  - per-target 验证结果分行显示

### 真 LLM 验证

```
$ uv run factory build "每周一上午9点查询销售数据库 sales_db, 写中文周报推钉钉"

target:  hybrid
nodes:   3 / edges: 2
outputs: dify=992c, n8n=1493c
valid:   ok
  dify : ok (err=0, info=0)
  n8n  : ok (err=0, info=1)
```

### 用户指示对齐

> "不考虑沙箱，看能否跑通流程，沙箱作为一个路线" — 2026-05-01 晚

工厂引擎现在不依赖任何外部沙箱即可端到端跑通。Dify/n8n 沙箱导入验证作为独立路线，不阻塞工厂演进。

### 已知局限不变

- DifyCompiler 仍输出 "Dify-shaped" YAML（结构对、字段近似）
- 沙箱实际导入验证待团队接入
- harvest CLI 待 V2.0.4 起补
- Phase 3 World 前端 scaffold 待整合到 v1.9
- Phase 4 评测集仍为 ES-001 (10 用例)，扩到 30 用例待 V2.0.4

---

## V2.0.2 — 2026-05-01 · Phase 2 收尾包（6 Gate 状态机 + HTTP API + SSE）

> 状态：Phase 2 W4+W5 完成。**343 测试通过 + 1 skipped**。

### 新功能

- **FactorySession 6 Gate 状态机**（15 状态：CREATED + 6 工位 + 6 Gate + 3 终态）
  - 纯转移函数 `next_state_after()`，全部转移路径单测覆盖
  - GateDecision: pass / edit / redo（v2 审计补法 #3）
- **DB 持久化**（3 张新表，additive 不破坏 v0.5.0）
  - factory_sessions / factory_artifacts / factory_gate_decisions
- **HTTP API `/api/factory/*`**（6 路由）
  - POST /start、GET /{sid}、GET /{sid}/events (SSE)、POST /{sid}/gate、POST /{sid}/cancel、GET /{sid}/artifact
  - SSE 按 session_id 过滤，World 前端可直接订阅
  - BackgroundTasks 自动驱动各工位完成后进入 Gate
- **Trace Bus 扩展**：11 个 `factory.*` 事件类型常量
- **协同建造闭环**：start → design → GATE_DESIGN → pass → wrap → ... → RELEASED → 写 agents/__generated__/{sid}/workflow.yaml

### 已知局限

- DifyCompiler 仍输出 "Dify-shaped" YAML（Phase 1 W3 待沙箱验证）
- harvest CLI 未实现（V2.0.3 补）
- Phase 3 World 前端未启动

---

## V2.0.1 — 2026-05-01 · Phase 1 W2 收尾包（首个工厂可执行版本）

> 状态：可打包发布；ES-001 评测集 10/10 真 LLM 通过。

### 新功能

- **7 个真实组件全部就位**（W1 W2 实现）
  - AtomLoader / IntentParser / SearchEngine V1 / Resolver / DifyCompiler V1 / DSLValidator V1 / FactoryPipeline
- **5 个种子原子 YAML 完整入库**：DB / HTTP / LLM(含 model_routing) / Notify / Schedule
- **factory CLI**：
  - `factory build "<NL>"` — NL → Dify YAML
  - `factory eval [--set ES-XXX] [--only case_id] [--json]` — 跑评测集
- **EvalRunner 货架**（资产中心 #8 启用）+ ES-001 MVP 评测集（10 用例）
- **3 个 LLM 横切机制**
  - ModelRouter（V1 走 settings.harness_default_model，V2 接 /v1/models 动态选）
  - LLM 节点不绑型号绑 task_type+size+fallback_chain
  - RUIDONG_MODEL_FOR_<task>_<size> 占位符（部署期注入）

### 验收

- 真 LLM e2e：`每周一上午9点 销售周报推钉钉` → cron+DB+LLM+Notify 4 节点 hybrid Dify YAML
- ES-001 评测：**10/10 = 100% 通过**（MVP gate ≥70% / GA gate ≥80% 双通过）
- 测试：v0.5.0 基线 239 → V2.0.1 共 **317 全绿**

### 8 条核心强制规则全部生效

1. 禁 emoji（代码 0 emoji） / 2. Almanac 美学 / 3. iruidong 网关 /
4. 大节点 Gate / 5. 画布美观简洁可动态（Phase 3）/
6. R1 组件化（每组件 interface.py + impl.py + tests + README）/
7. R2 架构图先行（Phase-1-架构图.md 先于代码）/
8. R3 代码 Nexus（GitNexus 集成方案落地）

### Phase 7 决策已锁

V2.1.0 主线：多智能体 + 行业理解扩展。6 层架构（加 L4.5）+ Industry Router + 12 个 Industry Designer + OpenAI Agents SDK runtime。详见 Obsidian `Phase-7-多智能体与行业理解.md`。

### 已知局限

- DifyCompiler 输出是"Dify-shaped" YAML（结构对、字段近似），尚未对实际 Dify 沙箱做导入验证 → Phase 1 W3 任务
- Resolver V1 用词法（tag/name/desc 重叠）打分，未接 embedding → Phase 2
- 6 Gate 状态机未启用 → Phase 2
- World 工厂前端未启用 → Phase 3
- n8n compiler 未启用 → Phase 5

---

## V2.0.0 — 2026-05-01 · 工厂基线（设计稿，未打包）

> **重大版本跳跃**：v0.5.0 → V2.0.0 标志架构性变更——从"运行时协调器"升级为"智能体黑灯工厂"。
> 后续打包从 V2.0.1 开始（Phase 1 完成时）。

### 核心变更

- **新增**：NL → 画布生长 → DSL → 部署 完整工厂流水线
- **新增**：5 层资产中心（L1 基石 / L2 原子 / L3 中间组合 / L4 场景智能体 / L5 Harness）+ 横切 LLM 模型路由表 + 行业分类
- **新增**：6 工位 + 6 Gate 协同建造（设计/加工/组装/测试/UI/部署）
- **保留**：v0.5.0 的 239 个测试 + 三层金字塔注册中心 + 6 层 trace 总线 + 自我强化循环
- **集成**：与 Agent World 的 `/creator/factory` 板块对接，Almanac 风格 UI

### 8 条核心强制规则

1. 禁用 emoji（icon 用 lucide-react / Mermaid）
2. Almanac 美学（衬线 + 焦橙 + 纸面，禁渐变/玻璃态）
3. iruidong 网关（不硬编码型号，运行时查 /v1/models）
4. 大节点 Gate 干预（阶段内 read-only，6 Gate 末端开放编辑）
5. 画布美观简洁可动态（节点 4 项信息上限）
6. R1 组件化开发（每功能独立目录 + interface.py + impl.py + tests + Owner README）
7. R2 架构图先行（C1 容器 + C2 组件 + C3 数据流 Mermaid，Almanac 配色）
8. R3 代码 Nexus（GitNexus 索引 + MCP 喂 AI agent）

### V1 GA 路线图（13 周）

- Phase 1 (W1-3)：仓库扩 5 层 + IntentParser + Resolver + Dify Compiler → V2.0.1
- Phase 2 (W4-5)：6 Gate 状态机 + canvas 事件流 + harvest CLI → V2.0.2
- Phase 3 (W6-8)：自研画布 + Gate 评审 UI → V2.0.3
- Phase 4 (W9-10)：MVP 评测 70% → V2.0.4
- Phase 5 (W11-13)：n8n + 多模式 + 80% GA → **V2.0.5 = V1 GA**

### 关键决策（详见 Obsidian 工作台）

- **MVP 场景**：智能日报/周报生成（数据分析智能体类）
- **5 个种子原子**：DB/HTTP/LLM/Notify/Schedule（v2 审计后调整，去 Chart 加 cron）
- **Dify + n8n 双底座**（Dify 为 AI 大脑，n8n 为手脚和触发）
- **80% GA 验收**（MVP 期 70%，留迭代空间）
- **Almanac UI 风格**（与 Agent World v1.8 对齐）

### 链接

- 主基线：`E:\Obsidian\Agent 工厂\Agent-工厂-v1-基线设计.md`
- 决策记录：`E:\Obsidian\Agent 工厂\决策记录.md`
- 可行性审计：`E:\Obsidian\Agent 工厂\可行性审计-v2.md`
- 工程规范：`E:\Obsidian\Agent 工厂\工程规范\` (含 IR 契约 / 原子 YAML / 画布 / 组件化与架构 / 代码 Nexus)

---

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
