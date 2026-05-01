# Resolver Component

Owner: TBD / 2026-05

## What
Binds atoms to a StructuredIntent's abstract steps. Submodules: recall (RAG) -> rank (LLM with NOT_applicable as hard filter) -> bind (params + type-checked edges).

## Submodules
- `recall.py` - candidate retrieval from registry (top-K)
- `rank.py` - LLM-based ranking (NOT_applicable hard filter, never soft-prompt)
- `bind.py` - parameter mapping + edge type-check + auto-insert type_adapter combos

## Dependencies
- `app.registry.search_engine.SearchEngine`
- `app.core.llm.client` (via Ruidong gateway, model selected by task_type+size)
- `app.core.pipelines.factory.ir`

## Phase
Phase 1 W2 day 3-5. Not implemented yet.

## Notes
- LLM nodes get `llm_task_type` + `llm_size` (NOT specific model name) per [[资产中心/横切-LLM模型路由表]]
- Edge `type_check` mismatch -> auto-insert `combo.type_adapter.<from>_to_<to>.v1`
