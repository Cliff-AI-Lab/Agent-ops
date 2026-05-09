# Agent Ops · Lights-Out Agent Factory

**A deterministic, auditable factory pipeline that takes a sentence of natural language and produces a deployable Dify workflow — with a self-reinforcing reuse loop on its parts library.**

> Status: **V2.7.0** (Phase 9 W1 GA) on branch `feat/v2.0.0-factory`
> Tracker: [`Agent ops V2.0.0` Obsidian workboard](#) · GitHub: [Cliff-AI-Lab/Agent-ops](https://github.com/Cliff-AI-Lab/Agent-ops)

---

## What "lights-out factory" means here

A black-out (lights-out) factory runs without humans on the floor. Three loops close the production line:

1. **Generation loop** — natural language → `StructuredIntent` → `ResolvedDAG` → Dify YAML → `harness factory deploy` → real Dify import.
2. **Reuse loop** — every part (atom) you ship records its usage in `wiki_articles` + `qa_runs`. The score service feeds usage data back into the resolver: parts that work get picked more, parts that fail sink.
3. **Drift loop** — humans editing the published Dify workflow trip a `harness factory drift-check`; the factory remains the single source of truth.

```
NL  ──► IntentParser ──► Resolver ──► DifyCompiler ──► YAML ──► Dify (real)
                            ▲                                        │
                            │                              auto_register
                            │                                        ▼
                       AtomScoreService ◄─── AtomUsageView ◄── wiki_articles
                                                                + qa_runs
```

---

## Quick start (60 seconds)

```bash
uv sync
cp .env.example .env.dev          # fill RUIDONG_API_KEY (no hardcoded model)
uv run harness-initdb

# generate a workflow from natural language
uv run harness factory build "每周一上午9点查 sales_db 出周报发钉钉"

# one-button publish to a local Dify (docker compose up in another window first)
uv run harness factory deploy \
    --nl "每周一上午9点查 sales_db 出周报发钉钉" \
    --specimen-id sp-weekly-report-001 \
    --target dify

# see your atoms ranked by real usage
uv run harness factory atom-rank --layer atom.

# inspect a single atom's score and history
uv run harness factory atom-score atom.llm.chat.v1
```

Open the Agent Ops workspace UI at `http://127.0.0.1:8000` (after `uv run uvicorn app.main:app`).

---

## CLI surface (`harness factory ...`)

| Command | Phase | Purpose |
|---|---|---|
| `build "<NL>"` | Phase 1+ | Compile natural language to Dify YAML / n8n JSON |
| `build-auto "<NL>"` | Phase 7 | Auto-route to single-agent or multi-agent (industry router) |
| `eval` / `eval-multi` | Phase 4 / 7 | Run ES-001 / ES-002 evaluation sets |
| `deps <asset_id>` | Phase 6 W4 | Asset dependency / impact graph |
| **`deploy`** | **Phase 8 Day 3** | **NL or YAML → real Dify import (one-button publish)** |
| **`drift-check`** | **Phase 8 Day 3** | **Detect human edits in Dify (one-way push boundary)** |
| **`atom-score <id>`** | **Phase 9 W1 Day 1** | **Per-atom usage score (the reuse-loop feedback)** |
| **`atom-rank --layer atom.`** | **Phase 9 W1 Day 1** | **Rank atoms by real-world utility** |
| **`wiki-sync --to obsidian --vault-path PATH`** | **Phase 9 W1 Day 2** | **Render reuse data into Obsidian asset center** |

Bold rows are the lights-out additions (Phase 8 / 9).

### Reuse score signal (opt-in)

The resolver picks atoms with strong real-world usage history first when the
`FACTORY_SCORE_SIGNAL=1` environment variable is set. Off by default so
ES-001 / ES-002 baselines stay reproducible on a fresh deploy. Once the
factory has accumulated wiki + QA history, turn it on:

```bash
export FACTORY_SCORE_SIGNAL=1
uv run harness factory build "..."
# trace stream now carries score_signal_applied(asset_id, base, score, final)
```

The lift is `final = base * (1 + 0.2 * score)`, clamped to `[0,1]`.
Cold-start atoms (zero history) are unaffected.

---

## Phase progress

| Phase | Track | Status | Released |
|---|---|---|---|
| 1 | Atom registry + DSL compiler skeleton | done | V2.0.1 |
| 2 | Factory session state machine + trace SSE | done | V2.0.2 / V2.0.4 |
| 3 | World factory frontend scaffold | blocked (frontend integration) | scaffold only |
| 4 | Real-LLM scenario evaluation (ES-001) | done @ 93.3% | V2.0.4 |
| 5 | n8n compiler + multi-mode (design / variant / production) | done | V2.0.5 = V1 GA |
| 6 | Governance: cost ledger + budgets + tenants + tiktoken | done | V2.2 → V2.3 |
| 7 | Multi-agent + 12 industry designers (OpenAI Agents SDK) | done | V2.1.0 |
| 8 | Dify lights-out publish (compiler V2 + atom Jinja2 + `factory deploy`) | done | **V2.6.0-day3** |
| 8b | Three-pane Coding IDE view (factory canvas) | paused | — |
| **9** | **Asset wiki + reuse-feedback loop (`AtomScoreService` + WikiSync + Resolver signal)** | **W1 GA** | **V2.7.0** |

See [`Agent 工厂/进度看板/`](https://github.com/Cliff-AI-Lab/Agent-ops/tree/feat/v2.0.0-factory) for the full Obsidian-style workboard (decision log, per-phase plans, daily notes).

---

## Layout

```
backend/app/
├── api/                       FastAPI routers (sessions, plan, generate, deliver, wiki, factory)
├── cli/                       harness factory subcommands (build, deploy, atom-rank, ...)
├── core/
│   ├── llm/                   L1 Ruidong-gateway client + model profiler (NO hardcoded model names)
│   ├── dialog/                L2 receptionist + dialog state machine
│   ├── orchestrator/          L3 meta-agent + blueprint planner + deliver pipeline + factory_session
│   ├── stability/             L4 validator + repair + fallback (tri-level Pydantic contracts)
│   ├── pipelines/
│   │   ├── factory/           single-agent factory (intent → resolver → compiler → validator)
│   │   ├── multi_agent/       multi-agent composer (Phase 7)
│   │   └── ui/                Phase 2 HTML prototypes + Phase 3 production scaffolds
│   ├── governance/            cost ledger + rolling budgets + tiktoken estimator
│   └── trace/                 cross-layer trace bus + SSE
├── delivery/                  L6 packager + DifyPublisher (Phase 8) + AtomScoreService (Phase 9)
├── marketplace/               asset_store + auto_register + wiki_articles writer
├── ontology/                  canonical objects (Batch B)
├── registry/                  atom_loader + prompt_loader + dependency_graph
└── static/                    single-file workspace UI

capabilities/
├── atom/                      L2 atomic capabilities (yaml + Jinja2 dify projection template)
└── prompt/                    versioned prompt registry

scripts/
├── dify_import_check.py       thin Dify import probe (delegates to DifyPublisher)
├── package_release.py         build versioned zip releases
└── verify_*.py                end-to-end smoke harness

backend/.factory_deploy_log/   per-specimen deploy_log JSON (operational, not committed)
```

---

## Hard rules (project-wide)

1. **No hardcoded LLM model names.** All inference goes through `iruidong.com/v1`; routing is by `task_type + size`, not by model id.
2. **No emoji** in design docs / code / UI / CLI / tables. Vector icons only (`lucide-react`).
3. **Almanac aesthetic** for any UI surface: serif + ink + paper + focal-orange; no gradients / glass / generic AI-SaaS.
4. **R1 componentization.** Every feature lives under its own dir with `interface.py` + `impl.py` + `tests/`.
5. **R2 architecture-first.** Every phase ships C1+C2+C3 Mermaid diagrams *before* code.
6. **R3 dependency Nexus.** Every new component must be checked against the existing schema and service tree first; never reinvent V2.x infrastructure.
7. **Three graphs, not one.** Asset graph (Obsidian) / Workflow canvas (frontend) / Code Nexus (GitNexus) — they don't substitute for each other.
8. **Sandbox-startable.** `v2.0.0-factory` branch must boot inside the Ruidong sandbox via 4 health checks.

---

## How the reuse loop actually works (Phase 9)

Every successful factory deploy writes a row to `wiki_articles` (`atom_ids_json` populated by `auto_register.py` since V2.5 W6). Every QA run writes to `qa_runs` (V2.5 W8). `AtomScoreService` reads both via a SQL `WITH` aggregate (no new tables) and produces a per-atom score:

```
score = 0.4 · log10(1 + invocations)        ← popularity
      + 0.4 · success_rate                   ← reliability
      + 0.2 · exp(−days_since_last_use/30)   ← recency
```

Cold-start (zero history) atoms fall back to their static `metrics.pass_rate` from the atom yaml.

The resolver consumes this score as an additive signal next to LLM confidence — popular, reliable, recent atoms win ties. Failed atoms sink. The library curates itself.

---

## Recent commits on `feat/v2.0.0-factory`

- **V2.7.0 / Phase 9 W1 Day 3** — Resolver score signal (env-gated) + pipeline wire-up
- Phase 9 W1 Day 2 — `WikiSync` to Obsidian + `factory wiki-sync` CLI
- Phase 9 W1 Day 1 — `AtomScoreService` + CLI `atom-score / atom-rank`
- Phase 8 Day 3 — `harness factory deploy` + `DifyPublisher` + drift-check
- Phase 8 Day 2 — atom-level Jinja2 Dify projections (5 atoms)
- Phase 8 Day 1 — Dify v0.4.0 DSL alignment + import-check tool
- V2.4.0 — asset dependency graph
- V2.3.0 — Phase 6 W4 (tiktoken + soft-warn budget)

---

## Sources

- Ruidong platform: `https://iruidong.com/v1`
- Dify community edition (referenced for DSL schema): [`langgenius/dify`](https://github.com/langgenius/dify)
- Workboard / decision log / phase plans: [`Agent 工厂/`](https://github.com/Cliff-AI-Lab/Agent-ops) — Obsidian vault folders (decisions / progress / asset center / daily logs)
- Multi-agent runtime reference: OpenAI Agents SDK (Python)
