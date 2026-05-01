# IntentParser Component

Owner: TBD / 2026-05

## What
Parses natural-language requirement into a `StructuredIntent` (abstract verbs + data flow + constraints, no atom binding).

## Inputs / Outputs
- in: `nl: str` (the user's request, Chinese or English)
- out: `StructuredIntent` (Pydantic, schema_version="1.0")

## Dependencies
- LLM gateway: `iruidong.com/v1/chat/completions` (via `app.core.llm.client`)
- IR: `app.core.pipelines.factory.ir.intent.StructuredIntent`

## Tests
`tests/test_intent_parser.py` (skeleton, expand in Phase 1 W2)

## Phase
Phase 1 W2 (Day 8-9). Not implemented yet (stub raises `NotImplementedError`).
