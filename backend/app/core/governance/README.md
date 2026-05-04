# Governance Component (Phase 6 / V2.2)

> Owner: TBD / 2026-05
> 上线: V2.2 W1 (2026-05-05)
> 设计依据: [[Phase-6-治理与可观测]] § cost gate

## 职责

V2.2 W1 ship 1: **预估单次工厂调用的 LLM 成本，超阈值拒绝**。
防止 NL → multi-agent 量产模式 / 大规模评测 跑出失控的账单。

## 接口

```python
class CostEstimator(Protocol):
    def estimate_single(self, nl: str) -> CostEstimate: ...
    def estimate_multi(self, nl: str, anticipated_specialists: int = 5) -> CostEstimate: ...

class CostBudget(Protocol):
    threshold_cny: float
    def check(self, estimate: CostEstimate) -> CostBudgetDecision: ...
```

`CostEstimate`: `estimated_total_cny`, `breakdown` (per-stage), `estimated_llm_calls`, `tokens_in/out`, `notes`, `confidence`

## V2.2 W1 实现

`HeuristicCostEstimator`:
- 字符长度 token 代理（1 中文字符 ~= 1 token）
- 默认价格：0.07 CNY/1k input + 0.21 CNY/1k output（中等模型）
- `estimate_single`: 3 LLM calls （intent + 2 rank）
- `estimate_multi`: 2 LLM calls （classify + designer extract），输出 scale by specialists 数量
- confidence: single 0.7 / multi 0.6

`ThresholdCostBudget`:
- 单点 hard cap：`estimate.total <= threshold_cny` 通过
- 边界：== threshold 通过（保守倾向）
- L5 trace 事件 `factory.budget_check` 含 approved + 数字
- `CostBudgetExceeded` 异常，pipeline 抛它后转用户错误

## 不做的（V2.2 W1）

- 跨调用滚动预算（日 / 月）
- 多租户配额隔离
- 真实 token 计数（用 tiktoken）
- 软警告（80% 提醒）

V2.3+ 起逐步上。

## 集成点（V2.2 W2 待做）

```python
estimator = HeuristicCostEstimator()
budget = ThresholdCostBudget(threshold_cny=2.00)

# pre-flight check
estimate = estimator.estimate_multi(nl)
decision = budget.check(estimate)
if not decision.approved:
    raise CostBudgetExceeded(decision)
# else proceed with build
```

未来在 `MultiAgentFactoryPipeline.build()` / `FactorySwitcher.build()`
入口接 budget 参数（默认 None = no gate）。

## 测试

`tests/test_cost_estimator.py` 9 个测试 / `tests/test_cost_budget.py` 8 个测试.
共 17 测试，覆盖：构造默认值、breakdown 完整、specialists 数量伸缩、自定义价格、阈值边界、拒绝/通过、异常携带、estimator+budget 端到端。

## 后续演进

- V2.2 W2: 集成到 Switcher.build() + 暴露到 HTTP `/api/factory/build` request body
- V2.3: tiktoken 真实 token 计数
- V2.3: 滚动预算（DB 计当日累计成本）
- V2.4: 跨租户配额（tenant_id 维度）
