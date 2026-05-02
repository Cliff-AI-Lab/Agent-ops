# Industry Designer

Phase 7 component · stage 2 of two-stage scheduler.

> Owner: TBD / 2026-05
> 上线日期: 2026-05-02
> 版本: V2.1.0-alpha (V2.0.5 V1 GA 后启动)
> 设计依据: [[Phase-7-多智能体与行业理解]] § 双阶段调度

## 职责

把 (NL + IndustryClassification) 转成 MultiAgentSpec，是 Phase 7 双阶段调度的 Stage 2。

只在 `classification.is_multi_agent=True` 时被调用；单工作流路径直接走 FactoryPipeline。

## V2.1.0 W1 实现

只发 1 个 Designer：`GeneralDesigner`（industry_code='01' 通用）。
12 行业专属 Designer 在 V2.1.0 W3+ 批量上线。

## 接口

```python
class IndustryDesigner(Protocol):
    industry_code: str  # '01'..'12'
    async def design_multi_agent(
        self, nl: str, classification: IndustryClassification
    ) -> MultiAgentSpec: ...
```

## GeneralDesigner 行为

1. LLM 抽取（"结构化抽取"任务）：从 NL 提取 specialists / triage targets / shared context
2. 1 次重试（带错误消息的修复 prompt）
3. agent_class 路由：基于 business_scenario 关键词映射到 7 类 L4
4. 默认 guardrails 注入：relevance + jailbreak（V2 审计补法）
5. handoff_targets 过滤：只保留指向有效 specialist id 的边
6. validate_graph() 校验：结构性问题立即抛 ValueError
7. runtime 锁定：'openai_agents_sdk'（Phase 7 决策已锁）
8. L3 trace：design_start / design_done

## 不做的

- 不调用 FactoryPipeline 把 specialists 实化为 L4 specimens（那是 MultiAgentComposer 的职责）
- 不发 OpenAI Agents SDK runtime 代码（那是 Composer 的职责）
- 不部署（那是 deploy stage 的职责）

## 测试

`tests/test_general.py` 11 个测试：
- _default_guardrails / _agent_class_for_scenario 单元
- 航司客服等价 fixture（cs-agents-demo）：5 specialists + 4 handoffs + 2 guardrails 完整产出
- 拒绝 single-agent classification
- 拒绝空 NL
- 1 次重试恢复
- triage_initial_targets 过滤无效 id
- handoff_targets 过滤无效 id
- 数据分析场景 agent_class 路由正确
- 单 specialist 拒绝（multi-agent 至少 2 个）

## 集成点

```
NL --(IndustryRouter)--> IndustryClassification
                              |
                              | is_multi_agent=True
                              ↓
                  GeneralDesigner (this) --> MultiAgentSpec
                                                 |
                                                 ↓
                                       MultiAgentComposer (V2.1.0 W2)
                                       --> N x FactoryPipeline.build(specialist.nl_brief)
                                       --> OpenAI Agents SDK runtime code
                                       --> deployable artifact
```

## 后续演进

- V2.1.0 W2: MultiAgentComposer 接入（实化 N specialists）
- V2.1.0 W3+: 11 行业 Designer 批量上线，DesignerRegistry 按 industry_code 路由
- V2.1.1: 历史 spec 反哺 prompt 库（每个行业有专属 in-context examples）
- V2.2: Designer 自我评估 + DAG 优化建议（"是否拆分 X specialist"）
