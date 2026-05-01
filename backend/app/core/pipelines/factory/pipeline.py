"""FactoryPipeline - top-level orchestration entry.

Phase 1 (this file): synchronous "build once" mode.
Phase 2: 6 Gate state machine wraps this; resume/redo per Gate.

Wires the 4 stages of the compiler:
  NL -> IntentParser -> Intent
  Intent -> Resolver -> ResolvedDAG
  ResolvedDAG -> DSLCompiler -> DSL string(s)

Multi-target: when ResolvedDAG.target == 'hybrid', both DifyCompiler and
N8nCompiler run on their respective scoped slices (per ResolvedDAG.target_split).
The build() result then carries `outputs: dict[str, str]` keyed by target name,
plus `dsl: str` as the legacy primary output for backward-compat callers.

Returns BuildResult dict with all intermediate artifacts so callers (CLI / API)
can persist them for trace replay.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.llm.client import LLMClient
from app.core.llm.model_router import ModelRouter
from app.core.pipelines.factory.compiler import (
    DifyCompilerImpl,
    N8nCompilerImpl,
)
from app.core.pipelines.factory.intent_parser import IntentParserImpl
from app.core.pipelines.factory.ir import ResolvedDAG, StructuredIntent
from app.core.pipelines.factory.resolver import ResolverImpl
from app.core.trace.bus import emit
from app.registry.atom_loader import AtomLoaderImpl
from app.registry.search_engine import SearchEngineImpl


class FactoryPipeline:
    """Top-level factory entry. Phase 1 = single-shot build."""

    def __init__(
        self,
        intent_parser: IntentParserImpl | None = None,
        resolver: ResolverImpl | None = None,
        compiler: DifyCompilerImpl | None = None,
        n8n_compiler: N8nCompilerImpl | None = None,
        atoms_dir: Path | None = None,
        llm_client: LLMClient | None = None,
        model_router: ModelRouter | None = None,
    ) -> None:
        self._llm = llm_client
        self._router = model_router or ModelRouter()

        if atoms_dir is not None:
            atoms = AtomLoaderImpl().load_all(atoms_dir)
        else:
            atoms = {}

        if intent_parser is None:
            intent_parser = IntentParserImpl(
                llm_client=self._llm, model_router=self._router
            )
        if resolver is None:
            search = SearchEngineImpl()
            search.index(atoms.values())
            resolver = ResolverImpl(
                search_engine=search,
                llm_client=self._llm,
                model_router=self._router,
            )
        if compiler is None:
            compiler = DifyCompilerImpl()
            compiler.index(atoms)
        if n8n_compiler is None:
            n8n_compiler = N8nCompilerImpl()
            n8n_compiler.index(atoms)

        self._intent_parser = intent_parser
        self._resolver = resolver
        self._compiler = compiler  # DifyCompilerImpl
        self._n8n_compiler = n8n_compiler

    async def build(self, nl: str) -> dict[str, Any]:
        """NL -> Intent -> ResolvedDAG -> DSL(s).

        Returns:
            {
                "intent": StructuredIntent,
                "dag": ResolvedDAG,
                "dsl": str            # primary output (n8n if hybrid, else target's DSL)
                "outputs": {          # per-target outputs (always present)
                    "dify": "...",    # only if target in (dify, hybrid)
                    "n8n": "...",     # only if target in (n8n, hybrid)
                },
                "target": str,
                "issues": list,
            }
        """
        emit("L3", "FactoryPipeline", "build_start", f"nl_len={len(nl)}")

        intent: StructuredIntent = await self._intent_parser.parse(nl)
        dag: ResolvedDAG = await self._resolver.resolve(intent)

        outputs: dict[str, str] = {}
        if dag.target in ("dify", "hybrid"):
            outputs["dify"] = self._compiler.compile(dag)
        if dag.target in ("n8n", "hybrid"):
            outputs["n8n"] = self._n8n_compiler.compile(dag)

        # Primary `dsl` for backward-compat: prefer n8n in hybrid (the orchestrator),
        # else whichever target was chosen.
        if dag.target == "hybrid":
            primary = outputs.get("n8n", outputs.get("dify", ""))
        else:
            primary = outputs.get(dag.target, "")

        emit(
            "L3",
            "FactoryPipeline",
            "build_done",
            f"steps={len(intent.steps)} nodes={len(dag.nodes)} target={dag.target} outputs={list(outputs.keys())}",
        )

        return {
            "intent": intent,
            "dag": dag,
            "dsl": primary,
            "outputs": outputs,
            "target": dag.target,
            "issues": dag.issues,
        }
