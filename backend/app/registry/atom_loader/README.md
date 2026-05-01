# Atom Loader Component

Owner: TBD / 2026-05

## What
Loads atom YAML files (`capabilities/atom/<sub>/*.yaml`) into AtomDef Pydantic models. Strict validation: invalid YAML rejected at load time.

## Phase
Phase 1 W1 day 2 (skeleton). Phase 1 W1 day 7 (load all 5 seed atoms).

## Validation
- `asset_id` format `atom.<sub>.<impl>.v<n>` enforced via regex
- `NOT_applicable` MUST have ≥ 1 entry (Resolver depends on it)
- `test_cases` MUST have ≥ 2 (happy + edge)
- `description` MUST be ≥ 20 chars (RAG quality)
- `projections` MUST have ≥ 1 target

## Tests
`tests/test_atom_loader.py` covers contract + happy load.
