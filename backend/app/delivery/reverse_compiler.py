"""ReverseCompiler - Phase 10 W1 Day 3/4 Dify YAML -> diff against original DAG.

Closes the human-in-the-loop side of Path B:

    factory pipeline --canvas dify
        ... build -> deploy -> HOLD ...        (Day 2)
        user edits in Dify Studio
        ... HOLD released ...
        ReverseCompiler.diff(original_dag, edited_yaml)
                                      |
                                      v
                            ReverseDiff(added/removed/reordered)
                                      |
                                      v
                  二次 deploy if non-empty (same app_id, human_tuned=true)

MVP scope (per Phase 10 design doc decision):
  - Compare atom_id sequences only (not full field reconstruction)
  - Source of truth for atom_id in Dify: node.data._factory.asset_id
    (added by DifyCompilerImpl V2 / Phase 8 Day 1)
  - Detect: added / removed / reordered / human_tuned (any change at all)
  - Full ResolvedDAG roundtrip (with inputs/params reconstruction) is W2

R3 reuse audit:
  - Reuses ResolvedDAG model unchanged (read-only)
  - Reuses DifyPublisher.drift_check shape for fetching the edited YAML
    (caller must do the fetch; ReverseCompiler is pure parse + compare)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from app.core.pipelines.factory.ir import ResolvedDAG


@dataclass
class ReverseDiff:
    """Outcome of comparing the original ResolvedDAG against the
    user-edited Dify YAML.
    """

    specimen_id: str
    original_atoms: list[str]
    edited_atoms: list[str]
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    reordered: bool = False
    human_tuned: bool = False
    parse_error: str | None = None

    def short_summary(self) -> str:
        if self.parse_error:
            return f"parse error: {self.parse_error}"
        if not self.human_tuned:
            return "no change (workflow identical to factory output)"
        parts: list[str] = []
        if self.added:
            parts.append(f"+{len(self.added)} added ({', '.join(self.added)})")
        if self.removed:
            parts.append(f"-{len(self.removed)} removed ({', '.join(self.removed)})")
        if self.reordered:
            parts.append("reordered")
        return "; ".join(parts) or "field-level edits (atom set unchanged)"


class ReverseCompiler:
    """Stateless. Construct once per pipeline run."""

    SYNTHETIC_NODES = {"node_start", "node_end"}

    def diff(
        self,
        specimen_id: str,
        original_dag: ResolvedDAG,
        edited_yaml: str,
    ) -> ReverseDiff:
        """Compare the atom sequence of the original DAG vs the edited YAML.

        Args:
            specimen_id: for trace / log linkage
            original_dag: what the factory pushed to Dify (the canonical version)
            edited_yaml: what the user wrote back in Dify Studio (export DSL)

        Returns:
            ReverseDiff. Inspect ``human_tuned`` for "did anything change?"
            and ``added/removed`` for atom-level deltas.
        """
        original = self._extract_dag_atoms(original_dag)
        edited, parse_err = self._extract_yaml_atoms(edited_yaml)
        if parse_err:
            return ReverseDiff(
                specimen_id=specimen_id,
                original_atoms=original,
                edited_atoms=[],
                parse_error=parse_err,
                human_tuned=False,
            )

        original_set = set(original)
        edited_set = set(edited)
        added = sorted(edited_set - original_set)
        removed = sorted(original_set - edited_set)
        intersection = original_set & edited_set
        # reordering only meaningful when the set is identical
        reordered = (
            not added
            and not removed
            and original != edited
        )
        human_tuned = bool(added or removed or reordered)
        # If sets match and order matches but YAML differs (e.g. params/prompt
        # text edited), MVP marks human_tuned=False (we report at atom-level).
        # W2 will detect field-level changes too.
        return ReverseDiff(
            specimen_id=specimen_id,
            original_atoms=original,
            edited_atoms=edited,
            added=added,
            removed=removed,
            reordered=reordered,
            human_tuned=human_tuned,
        )

    # ----- internals -----------------------------------------------------

    @staticmethod
    def _extract_dag_atoms(dag: ResolvedDAG) -> list[str]:
        """Atom asset_id sequence in DAG node-order.

        We rely on the (insertion-ordered) ``dag.nodes`` list rather than
        the topological sort, because Dify Studio renders by graph layout
        and the user's "reorder" intent is positional, not topological.
        """
        return [n.asset_id for n in dag.nodes]

    @classmethod
    def _extract_yaml_atoms(cls, edited_yaml: str) -> tuple[list[str], str | None]:
        """Pull atom asset_ids from a Dify YAML in the order they appear.

        Looks at workflow.graph.nodes, skipping synthetic Start/End nodes,
        and reads node.data._factory.asset_id. Falls back to (asset_id-less)
        skip when the field is absent (older or hand-written workflows).
        """
        try:
            data = yaml.safe_load(edited_yaml)
        except yaml.YAMLError as exc:
            return [], f"YAMLError: {exc}"
        if not isinstance(data, dict):
            return [], f"top-level is not a mapping (got {type(data).__name__})"

        try:
            nodes = data["workflow"]["graph"]["nodes"]
        except (KeyError, TypeError) as exc:
            return [], f"missing workflow.graph.nodes ({exc})"
        if not isinstance(nodes, list):
            return [], f"workflow.graph.nodes is not a list (got {type(nodes).__name__})"

        atoms: list[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            nid = node.get("id")
            if nid in cls.SYNTHETIC_NODES:
                continue
            data_block = node.get("data") or {}
            factory = data_block.get("_factory") or {}
            asset_id = factory.get("asset_id")
            if asset_id:
                atoms.append(asset_id)
            # else: human-added node without _factory metadata - W2 will
            # try to infer asset_id from data.type; MVP just skips it
        return atoms, None
