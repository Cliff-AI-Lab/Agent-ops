# Validator Component

Owner: TBD / 2026-05

## What
3-layer validation of compiled DSL:
1. Schema (JSON Schema against Dify/n8n)
2. Static (variable refs resolve, node types exist)
3. Dry-run (Phase 2)

## Repair loop
Only on validation failure: LLM is called with the failing node ONLY (not the entire DSL) for local repair.

## Phase
Phase 1 W3 day 3-4 (schema only). Phase 2 adds dry-run + LLM repair.
