# DSL Compiler Component

Owner: TBD / 2026-05

## What
Pure template-based compilation: ResolvedDAG -> Dify YAML / n8n JSON.

## Phase
- Phase 1 W3 day 1-3: DifyCompilerImpl (Jinja2 templates from atom.projections.dify)
- Phase 5 W11: N8nCompilerImpl (atom.projections.n8n)

## Determinism
NO LLM in this stage. Same ResolvedDAG -> same DSL output. Test contract: round-trip same input twice -> identical output.

## Dependencies
- `app.registry.atom_loader` (for projection templates)
- Jinja2 (already in pyproject.toml)
