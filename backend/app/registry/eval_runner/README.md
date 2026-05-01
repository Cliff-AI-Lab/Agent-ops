# EvalRunner Component

Owner: TBD / 2026-05

## What
Runs an EvalSet through FactoryPipeline. Loose structural assertions per case (target / subcategories / node-count / validator-ok), aggregates pass-rate.

## Phase 1 W2 use
`factory eval ES-001` runs the MVP eval set and emits a pass-rate. Goal:
- MVP gate: ≥ 70%
- GA gate: ≥ 80%

## Why "loose checks" not exact YAML diffs
LLM output varies between runs. Phase 1 cares about STRUCTURE (right atoms picked, right target chosen, validator passes). Phase 4 will add snapshot tests for stable cases.

## File location
`capabilities/eval_set/*.yaml`

Each YAML defines `EvalSet` with cases that have `nl` + `expected` shape.
