# Agent Ops

**Deterministic, auditable runtime that coordinates heterogeneous agents, tools, humans, and business workflows through typed capability contracts.**

Alignment target: [`../agent ops/README.md`](../agent%20ops/README.md) — Agent Ops Graph / Agent Ontology System Blueprint.

## What Agent Ops does

- Registers heterogeneous agent capabilities as versioned, typed contracts
- Executes multi-step workflows with typed inputs/outputs and policy boundaries
- Validates every agent output against Pydantic Ontology schemas (L4 Repair + Fallback)
- Emits full six-layer trace (L1 model · L2 dialog · L3 orchestration · L4 stability · L5 generation · L6 delivery)
- Generates deployable Vite + React + shadcn/ui projects from a one-sentence product brief (MVP vertical)

## Quick Start

```bash
uv sync
cp .env.example .env.dev  # fill RUIDONG_API_KEY + HARNESS_DEFAULT_MODEL
uv run harness-initdb
uv run uvicorn app.main:app --port 8000
# open http://127.0.0.1:8000
```

For local `dev` and `sandbox`, the gateway is `https://iruidong.com/v1`. The runtime reads it only from environment variables.

## Layout

- `backend/app/api/`        · FastAPI routers (sessions, plan, generate, deliver, delivery, config, trace)
- `backend/app/core/llm/`   · L1 Ruidong-compatible LLM client + model profile probes
- `backend/app/core/dialog/`       · L2 Receptionist + state machine + session store
- `backend/app/core/orchestrator/` · L3 Meta-Agent + BlueprintPlanner + DeliverPipeline
- `backend/app/core/stability/`    · L4 Validator + Repair + Fallback + tri-level contracts
- `backend/app/core/pipelines/ui/` · L5 Phase2 HTML prototypes + Phase3 production code
- `backend/app/delivery/`          · L6 Packager (zip output)
- `backend/app/core/trace/`        · cross-layer trace bus + SSE endpoint
- `backend/app/ontology/`          · Canonical Objects (Batch B, WIP — aligning with blueprint §8)
- `backend/app/static/`            · single-file workspace UI (vanilla JS + Tailwind CDN)
- `tests/`                         · 41 tests covering contracts, retry, repair, parity, ui pipeline, etc.
- `docs/agent-ops-alignment.md`    · blueprint alignment roadmap

## Alignment roadmap

See [`docs/agent-ops-alignment.md`](docs/agent-ops-alignment.md). Current progress ≈ 30% of blueprint v1.0. Incremental batches A→H without breaking tests.

## Sources

- Blueprint: local [`../agent ops/README.md`](../agent%20ops/README.md)
- Ruidong platform: `https://iruidong.com/v1`
- Relevant agent interop protocols: A2A, MCP, ACP (planned adapters in Batch F)
