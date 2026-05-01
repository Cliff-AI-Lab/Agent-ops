"""DSLValidatorImpl V1 - schema + structural checks on compiled DSL.

V1 (Phase 1 W3):
  - Parse YAML/JSON; on parse error -> error issue
  - Check minimum structure: app + workflow + workflow.graph.nodes
  - Check edge endpoints reference existing nodes
  - Warn on unresolved Dify model placeholders if no env mapping

Phase 2:
  - Full Dify/n8n JSON Schema (per compatible_*_version)
  - Dry-run via target API
  - LLM repair loop (single failing node only)
"""

from __future__ import annotations

import re

import yaml

from app.core.pipelines.factory.validator.interface import (
    ValidationIssue,
    ValidationReport,
)
from app.core.trace.bus import emit


_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class DSLValidatorImpl:
    """V1 structural validator. Phase 2 swaps in dry-run + LLM repair."""

    async def validate(
        self,
        dsl: str,
        target: str,
    ) -> ValidationReport:
        if target not in ("dify", "n8n", "hybrid"):
            return ValidationReport(
                ok=False,
                target="dify",
                issues=[
                    ValidationIssue(
                        severity="error",
                        message=f"unsupported target: {target!r}",
                    )
                ],
            )

        emit("L4", "DSLValidator", "validate_start", f"target={target} chars={len(dsl)}")

        issues: list[ValidationIssue] = []
        try:
            parsed = yaml.safe_load(dsl)
        except yaml.YAMLError as exc:
            return ValidationReport(
                ok=False,
                target=target,
                issues=[
                    ValidationIssue(
                        severity="error",
                        message=f"YAML parse error: {exc}",
                        fix_hint="check DSLCompiler output for malformed structure",
                    )
                ],
            )
        if not isinstance(parsed, dict):
            return ValidationReport(
                ok=False,
                target=target,
                issues=[
                    ValidationIssue(
                        severity="error",
                        message=f"top-level must be object, got {type(parsed).__name__}",
                    )
                ],
            )

        if target in ("dify", "hybrid"):
            issues.extend(self._check_dify_structure(parsed))
        if target == "n8n":
            issues.extend(self._check_n8n_structure(parsed))

        issues.extend(self._check_placeholders(dsl))

        ok = not any(i.severity == "error" for i in issues)
        emit(
            "L4",
            "DSLValidator",
            "validate_done",
            f"ok={ok} issues={len(issues)} errors={sum(1 for i in issues if i.severity == 'error')}",
        )
        return ValidationReport(ok=ok, target=target, issues=issues)

    @staticmethod
    def _check_dify_structure(parsed: dict) -> list[ValidationIssue]:
        out: list[ValidationIssue] = []
        if "app" not in parsed:
            out.append(
                ValidationIssue(
                    severity="error",
                    message="missing top-level 'app' key (Dify shape)",
                )
            )
        if "workflow" not in parsed:
            out.append(
                ValidationIssue(
                    severity="error",
                    message="missing top-level 'workflow' key (Dify shape)",
                )
            )
            return out
        graph = parsed.get("workflow", {}).get("graph", {})
        if not isinstance(graph, dict):
            out.append(
                ValidationIssue(severity="error", message="workflow.graph must be object")
            )
            return out

        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        if not isinstance(nodes, list):
            out.append(ValidationIssue(severity="error", message="workflow.graph.nodes must be list"))
            return out

        node_ids = {n.get("id") for n in nodes if isinstance(n, dict)}
        for n in nodes:
            if not isinstance(n, dict):
                continue
            if not n.get("id"):
                out.append(ValidationIssue(severity="error", message="node missing id"))
            if not n.get("type"):
                out.append(
                    ValidationIssue(
                        severity="error",
                        node_id=n.get("id"),
                        message="node missing type",
                    )
                )
        if isinstance(edges, list):
            for e in edges:
                if not isinstance(e, dict):
                    continue
                src = e.get("source")
                tgt = e.get("target")
                if src not in node_ids:
                    out.append(
                        ValidationIssue(
                            severity="error",
                            message=f"edge source {src!r} not in nodes",
                            fix_hint="check ResolvedDAG edges build correctly",
                        )
                    )
                if tgt not in node_ids:
                    out.append(
                        ValidationIssue(
                            severity="error",
                            message=f"edge target {tgt!r} not in nodes",
                        )
                    )
        return out

    @staticmethod
    def _check_n8n_structure(parsed: dict) -> list[ValidationIssue]:
        return [
            ValidationIssue(
                severity="info",
                message="n8n structural validation deferred to Phase 5",
            )
        ]

    @staticmethod
    def _check_placeholders(dsl: str) -> list[ValidationIssue]:
        out: list[ValidationIssue] = []
        for ph in set(_PLACEHOLDER_RE.findall(dsl)):
            if ph.startswith("RUIDONG_MODEL_FOR_"):
                out.append(
                    ValidationIssue(
                        severity="info",
                        message=f"placeholder ${{{ph}}} requires deploy-time env mapping",
                        fix_hint=(
                            "deploy_agent should resolve via /v1/models + task_type+size routing"
                        ),
                    )
                )
        return out
