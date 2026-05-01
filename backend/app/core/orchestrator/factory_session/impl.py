"""FactorySessionImpl - orchestrates FactoryPipeline through state machine.

This is the main entry point for Phase 2's "interactive build" mode.
Wraps FactoryPipeline so each stage emits canvas events, persists artifacts,
and pauses at each Gate.

Key design (per [[Phase-2-架构图]]):
  - Stage execution is OWN async function per stage (start_designing, etc.)
  - Each stage emits stage_entered + canvas_* + stage_completed events
  - State machine transitions: working -> auto stage_done -> gate
  - Gate stays open until apply_gate_decision() called externally
  - Persistence updates on every state transition
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.orchestrator.factory_session import events as ev
from app.core.orchestrator.factory_session.machine import FactorySessionMachine
from app.core.orchestrator.factory_session.persistence import (
    SessionPersistence,
    SessionRecord,
)
from app.core.orchestrator.factory_session.state import (
    FactorySessionState,
    GateDecision,
    Stage,
    is_gate,
    is_terminal,
    stage_for_state,
)
from app.core.pipelines.factory.ir import ResolvedDAG, StructuredIntent
from app.core.pipelines.factory.pipeline import FactoryPipeline


class FactorySessionImpl:
    """Interactive factory session with 6 Gates.

    Construction:
        session = await FactorySessionImpl.create(pipeline, persistence, nl)

    Drive forward:
        await session.run_next_stage()           # advances current working stage
        await session.apply_gate_decision(...)   # progress past a Gate

    States exposed via:
        session.state  # FactorySessionState
        session.session_id
    """

    def __init__(
        self,
        pipeline: FactoryPipeline,
        persistence: SessionPersistence,
        record: SessionRecord,
    ) -> None:
        self._pipeline = pipeline
        self._persistence = persistence
        self._record = record
        self._machine = FactorySessionMachine(state=record.state)
        # In-memory cache of stage artifacts (also persisted in DB)
        self._intent: StructuredIntent | None = None
        self._dag: ResolvedDAG | None = None
        self._dsl: str | None = None
        self._validation: dict | None = None

    # ---- factory ----------------------------------------------------------

    @classmethod
    async def create(
        cls,
        pipeline: FactoryPipeline,
        persistence: SessionPersistence,
        nl: str,
        industry_code: str | None = None,
        scenario: str | None = None,
    ) -> "FactorySessionImpl":
        record = await persistence.create(nl, industry_code, scenario)
        ev.session_created(record.session_id, nl)
        return cls(pipeline, persistence, record)

    @classmethod
    async def load(
        cls,
        pipeline: FactoryPipeline,
        persistence: SessionPersistence,
        session_id: str,
    ) -> "FactorySessionImpl | None":
        record = await persistence.get(session_id)
        if record is None:
            return None
        return cls(pipeline, persistence, record)

    # ---- read-only --------------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._record.session_id

    @property
    def state(self) -> FactorySessionState:
        return self._machine.state

    @property
    def nl(self) -> str:
        return self._record.nl

    # ---- stage execution --------------------------------------------------

    async def run_next_stage(self) -> FactorySessionState:
        """Advance from current state.

        - CREATED -> DESIGNING (auto-runs Intent + Resolve)
        - WRAPPING/ASSEMBLING/etc.: run that stage
        - At a Gate: raise; caller must apply_gate_decision first
        - Terminal: raise

        Returns the new state (typically a Gate).
        """
        cur = self._machine.state

        if is_terminal(cur):
            raise RuntimeError(f"session is terminal ({cur.value}); cannot advance")

        if is_gate(cur):
            raise RuntimeError(
                f"session is at {cur.value}; call apply_gate_decision() first"
            )

        # CREATED -> start
        if cur == FactorySessionState.CREATED:
            self._machine.fire("start")
            await self._persist_state()
            cur = self._machine.state

        # cur is now a working state; run it
        stage = stage_for_state(cur)
        if stage is None:
            raise RuntimeError(f"no stage for state {cur.value}")

        ev.stage_entered(self.session_id, stage.value)
        try:
            await self._dispatch_stage(stage)
        except Exception as exc:
            self._machine.fire("fail")
            await self._persist_state()
            ev.session_failed(self.session_id, str(exc))
            raise

        # auto stage_done -> gate
        run_id = uuid.uuid4().hex[:12]
        self._machine.fire("stage_done")
        await self._persist_state()
        ev.stage_completed(self.session_id, stage.value, run_id)

        gate_state = self._machine.state
        ai_report = self._build_ai_report(stage)
        checklist = self._build_checklist(stage)
        ev.gate_open(self.session_id, stage.value, ai_report, checklist)
        return gate_state

    async def apply_gate_decision(
        self,
        decision: str,
        payload: dict | None = None,
        decided_by: str | None = None,
    ) -> FactorySessionState:
        """Apply user's decision at a Gate. Records to DB + transitions state."""
        if not is_gate(self._machine.state):
            raise RuntimeError(
                f"session not at a Gate (state={self._machine.state.value})"
            )

        stage = stage_for_state(self._machine.state)
        assert stage is not None
        decision = decision.lower()
        if decision not in ("pass", "edit", "redo"):
            raise ValueError(f"unknown decision {decision!r}; allowed: pass/edit/redo")

        await self._persistence.record_gate_decision(
            session_id=self.session_id,
            gate_id=stage.value,
            decision=decision,
            payload=payload,
            decided_by=decided_by,
        )
        ev.gate_decision(self.session_id, stage.value, decision, payload)

        self._machine.fire(decision, payload=payload)
        await self._persist_state()
        ev.gate_closed(self.session_id, stage.value)

        if self._machine.state == FactorySessionState.RELEASED:
            ev.session_released(self.session_id, self._record.final_artifact_path)

        return self._machine.state

    async def cancel(self, reason: str = "user") -> None:
        if is_terminal(self._machine.state):
            return
        self._machine.fire("cancel")
        await self._persist_state()
        ev.session_cancelled(self.session_id, reason)

    # ---- internals --------------------------------------------------------

    async def _persist_state(self) -> None:
        await self._persistence.update_state(
            session_id=self.session_id,
            state=self._machine.state,
        )

    async def _dispatch_stage(self, stage: Stage) -> None:
        """Run the work for a stage. Persists output artifact."""
        if stage == Stage.DESIGN:
            await self._do_design()
        elif stage == Stage.WRAP:
            await self._do_wrap()
        elif stage == Stage.ASSEMBLE:
            await self._do_assemble()
        elif stage == Stage.TEST:
            await self._do_test()
        elif stage == Stage.UI:
            await self._do_ui()
        elif stage == Stage.DEPLOY:
            await self._do_deploy()
        else:
            raise RuntimeError(f"unknown stage {stage}")

    async def _do_design(self) -> None:
        """Stage 1: Intent + Resolver -> ResolvedDAG.

        Emits canvas events for each node + edge.
        """
        self._intent = await self._pipeline._intent_parser.parse(self.nl)
        self._dag = await self._pipeline._resolver.resolve(self._intent)

        atoms_by_id = self._pipeline._resolver._search._atoms
        for node in self._dag.nodes:
            atom = atoms_by_id.get(node.asset_id)
            sub = atom.subcategory if atom else "Unknown"
            ev.canvas_node_added(self.session_id, node.id, node.asset_id, sub)
        for edge in self._dag.edges:
            ev.canvas_edge_added(
                self.session_id,
                edge.from_node,
                edge.to_node,
                {"from": edge.type_check.from_type, "to": edge.type_check.to_type},
                edge.needs_adapter,
            )

        await self._persistence.save_artifact(
            self.session_id,
            "design",
            uuid.uuid4().hex[:12],
            {
                "intent": self._intent.model_dump(),
                "dag": self._dag.model_dump(),
            },
        )

    async def _do_wrap(self) -> None:
        """Stage 2: Wrap atoms (Phase 1 already does this inside Resolver).

        For Phase 2 V1, this stage is a pass-through; Phase 1 W3 will refine
        the Wrapper to run independent post-processing here.
        """
        await self._persistence.save_artifact(
            self.session_id,
            "wrap",
            uuid.uuid4().hex[:12],
            {"note": "wrap stage is pass-through in V2.0.2; Phase 1 W3 refines"},
        )

    async def _do_assemble(self) -> None:
        """Stage 3: DSL Compiler -> Dify YAML."""
        if self._dag is None:
            raise RuntimeError("assemble called before design completed")
        self._dsl = self._pipeline._compiler.compile(self._dag)
        await self._persistence.save_artifact(
            self.session_id,
            "assemble",
            uuid.uuid4().hex[:12],
            {"dsl": self._dsl, "target": self._dag.target},
        )

    async def _do_test(self) -> None:
        """Stage 4: Validator (+ EvalRunner in future)."""
        from app.core.pipelines.factory.validator import DSLValidatorImpl

        if self._dsl is None or self._dag is None:
            raise RuntimeError("test called before assemble completed")
        validator = DSLValidatorImpl()
        report = await validator.validate(self._dsl, self._dag.target)
        self._validation = report.model_dump()
        await self._persistence.save_artifact(
            self.session_id,
            "test",
            uuid.uuid4().hex[:12],
            self._validation,
        )

    async def _do_ui(self) -> None:
        """Stage 5: UI generation (V1: pass-through, Phase 3 builds Almanac UI shell)."""
        await self._persistence.save_artifact(
            self.session_id,
            "ui",
            uuid.uuid4().hex[:12],
            {"note": "UI stage is pass-through in V2.0.2; Phase 3 builds shell"},
        )

    async def _do_deploy(self) -> None:
        """Stage 6: Package the artifact (V1: write DSL to a path)."""
        if self._dsl is None:
            raise RuntimeError("deploy called before assemble completed")
        # V1: Persist DSL string only; Phase 5 adds real Dify deploy.
        artifact_dir = Path("agents") / "__generated__" / self.session_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "workflow.yaml"
        artifact_path.write_text(self._dsl, encoding="utf-8")

        await self._persistence.save_artifact(
            self.session_id,
            "deploy",
            uuid.uuid4().hex[:12],
            {"artifact_path": str(artifact_path)},
        )
        await self._persistence.update_state(
            self.session_id,
            self._machine.state,
            final_artifact_path=str(artifact_path),
        )
        self._record.final_artifact_path = str(artifact_path)

    def _build_ai_report(self, stage: Stage) -> dict:
        """Per-Gate AI self-report (consumed by Phase 3 Gate panel)."""
        if stage == Stage.DESIGN and self._dag is not None:
            return {
                "node_count": len(self._dag.nodes),
                "edge_count": len(self._dag.edges),
                "target": self._dag.target,
                "issues": self._dag.issues,
                "low_confidence_nodes": [
                    n.id for n in self._dag.nodes if n.confidence < 0.6
                ],
            }
        if stage == Stage.ASSEMBLE and self._dsl is not None:
            return {"dsl_chars": len(self._dsl)}
        if stage == Stage.TEST and self._validation is not None:
            return self._validation
        return {}

    def _build_checklist(self, stage: Stage) -> list[str]:
        """Per-Gate review checklist (UI shows as ☐ items)."""
        if stage == Stage.DESIGN:
            return [
                "节点选型符合业务意图",
                "数据流类型对齐（type_check）",
                "无悬挂边或循环",
            ]
        if stage == Stage.ASSEMBLE:
            return ["DSL 结构完整", "节点参数齐全"]
        if stage == Stage.TEST:
            return ["validator 无 error", "evaluator 通过率达标"]
        if stage == Stage.UI:
            return ["UI 风格符合 Almanac"]
        if stage == Stage.DEPLOY:
            return ["资源清单完整", "凭据列表已填"]
        return []
