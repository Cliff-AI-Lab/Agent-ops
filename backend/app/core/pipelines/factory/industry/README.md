# Industry Router

Phase 7 component · stage 1 of two-stage scheduler.

> Owner: TBD / 2026-05
> 上线日期: 2026-05-02
> 版本: V2.1.0-alpha (V2.0.5 V1 GA 后启动)
> 设计依据: [[Phase-7-多智能体与行业理解]] § 双阶段调度 + [[工程规范/架构图/Phase-7-架构图]]

## 职责

把自然语言需求分类到 12 个行业 + 业务场景 + 是否需要多智能体。
是 Phase 7 双阶段调度的入口（**Stage 1**）；Stage 2 由 industry-specific Designer 接力。

## 接口

```python
class IndustryRouter(Protocol):
    async def classify(self, nl: str) -> IndustryClassification: ...
```

`IndustryClassification`:
- `industry_code`: 2 位代码 01..12（与 [[资产中心/横切-行业分类]] 对齐）
- `primary` / `sub` / `business_scenario`
- `is_multi_agent`: True 时下游走 MultiAgentComposer，False 走 FactoryPipeline 单工作流
- `confidence` ∈ [0, 1]
- `reasoning` ≥ 10 字 LLM 解释

## 实现

`IndustryRouterImpl` (impl.py)：
- LLM + 严格 JSON 输出（不依赖 OpenAI structured-output API）
- 1 次重试（带错误消息的修复 prompt）
- 启发式兜底（2 次失败后默认 01 通用）
- L3 trace 事件 `factory.industry.classify_*`
- 通过 `ModelRouter` 选模型（不硬编码型号；睿动网关）

## 不做的

- 不直接调用 Designer / FactoryPipeline（保持组件单一职责）
- 不维护行业 atom/prompt 库（那是 Designer 和 Registry 的职责）
- 不做行业内细分（细分由各 Designer 各自决定）

## 测试

`tests/test_router.py` 14 个测试：
- 模型构造与字段约束
- 启发式兜底
- LLM mock 单/多智能体路径
- 多个行业代码识别（金融/教育/医疗）
- markdown fence 剥除
- 重试恢复 + 兜底触发
- 空 NL 拒绝

## 集成点

```
NL --(IndustryRouter)--> IndustryClassification
                              |
                       is_multi_agent ?
                       /              \
                      yes              no
                       |                |
              IndustryDesigner    FactoryPipeline
              -> MultiAgentSpec   -> ResolvedDAG -> DSL
```

## 后续演进

- V2.1.0：12 行业 Designer 各 1 个种子 prompt
- V2.1.1+：基于历史 classify 结果训练分类 prompt 或加载 in-context examples
- V2.2+：Industry Router 的"行业内子分类"二次细化
