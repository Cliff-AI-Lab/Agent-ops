# Agent Ops 对齐路线图

**决策时间:** 2026-04-24
**决策:** 项目更名为 **Agent Ops**,完全对标 `docs/../../agent ops/README.md`(Agent Ops Graph / Agent Ontology System Blueprint)。

## 为什么对齐

Blueprint 是一份比现有 agent-harness 更成熟的**系统级设计**,包含:
- 7 个 Canonical Objects(CapabilityContract / AgentCard / WorkflowSpec / TaskRun / StepRun / Artifact / RunEvent)
- 10 个 Milestone 的完整路径
- 垂直起步 + 确定性外壳 + 契约先行 的 7 条工程哲学
- Registry / Planner / Verifier / Recovery / Adapter / Policy / Replay / Eval 的分层架构

当前实现(Sprint 1 + S2-A/B/C/D)约完成 blueprint 的 30%,方向一致但**缺很多关键能力**。对齐不是推翻,是**把已有的成果挂进 blueprint 框架,然后按 Milestone 顺序补齐**。

## 改名影响范围

| 位置 | 旧 | 新 |
|---|---|---|
| 产品展示名 | Agent Harness | **Agent Ops** |
| README 标题 | Agent Harness | Agent Ops |
| FastAPI app title | Agent Harness | Agent Ops |
| 前端 title / tagline | Agent Harness — 对话式 Meta-Agent 平台 | Agent Ops — Heterogeneous Agent Operations Runtime |
| Memory 关键词 | agent-harness | agent-ops |
| Workspace 目录 | `agent-harness/` | 暂时保留(改动成本高,下一批再考虑) |
| Python package 名 | `app` | 保留(内部标识,不影响对外) |
| pyproject.toml `[project] name` | `agent-harness` | 保留(package 元数据,不影响功能) |

**原则:** 所有**面向用户的文案**改成 Agent Ops,**内部 import 路径和目录结构**暂保留(降低风险,不破坏 41 tests)。

## 变更分批

### Batch A — 品牌 & 文档更名(**本次完成**)
- pyproject.toml description / FastAPI title / index.html title / README 头部 / memory 索引
- 新建本文档(对齐路线图)
- 新建 `backend/app/ontology/` 目录骨架(仅目录 + `__init__.py`)
- 零架构变更,pytest 全绿

### Batch B — Canonical Objects(**下一批**)
按 blueprint §8 建 7 个 Pydantic Object,放 `backend/app/ontology/objects/`:
- `capability_contract.py` — CapabilityContract(capability_id + version + input/output_schema + allowed_tools + risk_level + cost_budget + fallback_capabilities + eval_suite_ids)
- `agent_card.py` — AgentCard(agent_id + transport_type + endpoint + auth_mode + cost_profile + health_status)
- `workflow_spec.py` — WorkflowSpec(workflow_id + steps + edges + approval_nodes + failure_policies + success_criteria)
- `task_run.py` — TaskRun / StepRun
- `artifact.py` — Artifact(把现有 CodeArtifact / FileEntry 作为子类型)
- `run_event.py` — RunEvent(跟现有 TraceEvent 合并或兼容)
- `policy_decision.py` — PolicyDecision(提前准备给 M6)
- `eval.py` — EvalCase / EvalResult

把 `core/stability/contracts.py` 的 RequirementSpec / UIBlueprint 作为 domain-specific Object 放 `ontology/objects/ui.py`,**保留原有导入路径兼容**(`from app.core.stability.contracts import ...` 继续工作)。

### Batch C — Capability Registry(M2)
- 新建 `capabilities/` 目录(YAML 声明式 capability)
- `capabilities/ui.plan_blueprint.yaml` / `capabilities/ui.generate_prototypes.yaml` / ...
- 新建 `core/registry/` 模块读 YAML 加载 CapabilityContract
- CLI:`ops registry capability add/list/diff`

### Batch D — Workflow Engine(M3)
- 把 `UIPipeline` / `DeliverPipeline` 转成 `workflows/*.yaml` 驱动
- `core/workflow_engine/` 通用 DAG executor
- 现有硬编码 pipeline 保留作为 v0 fallback,直到 Workflow Engine 稳定

### Batch E — Planner / Verifier / Recovery(M4)
- 现有的 BlueprintPlanner 抽象为 Planner 接口的一个实现
- 新增 Verifier 抽象(现在的 Validator 升级版,跨 Object 通用)
- Recovery 接口(现在的 Fallback 扩展到任何 failure 路径)

### Batch F — Adapters(M5)
- `adapters/local/` / `adapters/http/` / `adapters/mcp/` / `adapters/a2a/` / `adapters/human/`
- A2A / MCP 接入后,Agent Ops 立即能和全球 A2A 兼容 agent 互通

### Batch G — Policy / Replay / Eval(M6-M8)
- Cedar-like policy engine
- 失败 bundle + replay 命令
- Golden fixtures + eval CI 门槛

### Batch H — CLI(M9,blueprint 明确 CLI-first)
- Typer + Rich 实现 `ops` 命令族
- Web UI 降级为 admin surface(保留当前作为 demo)

## Milestone 对标进度

| Blueprint Milestone | 当前状态 | 目标 Batch |
|---|---|---|
| M0 Repo guardrails | ✅ | — |
| M1 Canonical contracts | 🟡 部分 | **Batch B** 补齐 |
| M2 Registry | ❌ | Batch C |
| M3 Deterministic runner | 🟡 硬编码 pipeline | Batch D |
| M4 Planner/Verifier/Recovery | 🟡 未分离 | Batch E |
| M5 Adapters | ❌ | Batch F |
| M6 Policy boundary | ❌ | Batch G |
| M7 Observability + replay | 🟡 有 trace 无 replay | Batch G |
| M8 Eval loop | ❌ | Batch G |
| M9 Admin surface | 🟡 做了 Web,未做 CLI | **Batch H** 补 CLI |
| M10 AI-assisted control plane | ❌ | 后续 |

## 不对齐 blueprint 的决策(反向留存)

**Web UI 保留(不按 blueprint §12.1 "CLI-first" 先做 CLI)**
- 已投入大量视觉工作(Nordic + 双栏 + Trace 流 + 米字光标),不废弃
- 定位调整:Web UI 是 **demo / 客户向导** 面向终端用户,**CLI 是运营/CI 正式接口**(Batch H 补)
- 两套并存,共享同一后端

## 成功判定

对齐完成判定(最终 v1.0):
- Blueprint §21 的 9 条 v0 exit criteria 全部绿
- CLI 和 Web UI 都能跑完整 workflow
- A2A / MCP 各接入一个真实 agent / tool
- 至少一个非 UI 生成的 vertical(比如 blueprint 推荐的 RFP response)也能跑
