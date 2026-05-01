# Prompt Loader Component

Owner: TBD / 2026-05

## What
Loads prompt YAML files (`capabilities/prompts/<sub>/*.yaml`) into PromptDef Pydantic models. Renders templates via Jinja2 with strict undefined-var checking.

## Asset shelf #2 of 9 (Prompts) — activated.

## Validation
- `asset_id` regex: `prompt.<sub>.<...>.v<n>`
- `description` ≥ 15 chars
- `tags` ≥ 1
- `template` ≥ 20 chars (real prompt, not placeholder)
- `test_cases` ≥ 1

## Why prompts are first-class
Atoms reference `prompt_id` strings (e.g. `prompt.report.zh_writer.v1`). Without this loader, those strings are dangling refs. With it, prompts can:
  - be versioned independently
  - have their own test cases
  - be A/B-tuned without touching atoms or workflows
  - be harvested from GitHub (Phase 2 W5 harvest CLI uses this)

## Tests
`tests/test_prompt_loader.py` covers schema validation + render + happy/edge.
