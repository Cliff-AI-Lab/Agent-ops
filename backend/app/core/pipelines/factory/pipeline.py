"""FactoryPipeline - top-level orchestration entry.

Phase 1 (this file): synchronous "build once" mode.
Phase 2: 6 Gate state machine wraps this; resume/redo per Gate.

Wires the 4 stages of the compiler:
  NL -> IntentParser -> Intent
  Intent -> Resolver -> ResolvedDAG
  ResolvedDAG -> DSLCompiler -> DSL string

Returns BuildResult dict with all intermediate artifacts so callers (CLI / API)
can persist them for trace replay.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.llm.client import LLMClient
from app.core.llm.model_router import ModelRouter
from app.core.pipelines.factory.compiler import DifyCompilerImpl
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

        self._intent_parser = intent_parser
        self._resolver = resolver
        self._compiler = compiler

    async def build(self, nl: str) -> dict[str, Any]:
        """NL -> Intent -> ResolvedDAG -> DSL.

        Returns:
            {
                "intent": StructuredIntent,
                "dag": ResolvedDAG,
                "dsl": str (YAML),
                "target": str,
                "issues": list,
            }
        """
        emit("L3", "FactoryPipeline", "build_start", f"nl_len={len(nl)}")

        intent: StructuredIntent = await self._intent_parser.parse(nl)
        dag: ResolvedDAG = await self._resolver.resolve(intent)
        dsl: str = self._compiler.compile(dag)

        emit(
            "L3",
            "FactoryPipeline",
            "build_done",
            f"steps={len(intent.steps)} nodes={len(dag.nodes)} target={dag.target}",
        )

        return {
            "intent": intent,
            "dag": dag,
            "dsl": dsl,
            "target": dag.target,
            "issues": dag.issues,
        }
