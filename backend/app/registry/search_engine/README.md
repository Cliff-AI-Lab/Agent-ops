# Search Engine Component

Owner: TBD / 2026-05

## What
Embedding-based RAG over atom registry. Returns ranked candidates for Resolver.

## Hard rules (v2 audit gap)
- `health != green` -> filtered BEFORE ranking
- `subcategory` filter applied as hard rule when provided
- `NOT_applicable` enters as Resolver-side hard prompt constraint, NOT scoring

## Phase
Phase 1 W1 day 5: implement embedding + cosine similarity + filter chain.

## Dependencies
- `AtomLoader` for the corpus
- LLM gateway for embedding generation
