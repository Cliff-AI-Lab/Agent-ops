"""AtomHealthVerifier - Phase 9 W2 atom-level static health probe.

Runs cheap, deterministic checks against an atom yaml WITHOUT touching
external resources (no real LLM call, no real DB connection, no real HTTP).
Each check contributes a weight to the atom's pass_rate; the result is
persisted as an operational health log file under
``<harness_root>/.factory_health_log/<asset_id>.json``.

Why static checks first?
  - reproducible (no flaky network)
  - fast enough to run on every atom yaml change
  - already covers the most common failure modes:
      * yaml field drift (loader rejects the atom)
      * Dify projection template stale or unparseable
      * test_cases or NOT_applicable accidentally emptied during edit
  - real-resource probes (live LLM / DB / HTTP smoke) belong in W3.

Why a JSON log file instead of a new SQLite table?
  - operational state, not authoritative data; not committed
  - mirrors Phase 8's ``.factory_deploy_log`` design (consistent UX)
  - zero schema migration risk on V2.5.x baselines (R3 reuse audit)
  - AtomScoreService can read this in W2 follow-ups to enrich cold-start
    fallbacks without coupling to atom yaml writes
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jinja2
import yaml

from app.registry.atom_loader import AtomDef


@dataclass
class HealthCheck:
    """One named check + its result + weight."""

    name: str
    weight: float
    passed: bool
    detail: str = ""


@dataclass
class HealthReport:
    asset_id: str
    pass_rate: float
    verified_at: str
    checks: list[HealthCheck] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.checks)

    def to_json(self) -> str:
        return json.dumps(
            {
                "asset_id": self.asset_id,
                "pass_rate": self.pass_rate,
                "verified_at": self.verified_at,
                "passed": self.passed_count,
                "total": self.total_count,
                "checks": [asdict(c) for c in self.checks],
            },
            ensure_ascii=False,
            indent=2,
        )


class AtomHealthVerifier:
    """Runs the 5 static health checks per atom.

    The 5 checks and their weights:
      1. yaml_loadable      0.30   AtomLoader accepted the file
      2. test_cases_ok      0.20   >= 2 test_cases declared
      3. dify_template_ok   0.30   projections.dify.template renders to a
                                   YAML mapping (or atom is marked
                                   not_supported, which is a valid skip)
      4. not_applicable_ok  0.10   NOT_applicable list non-empty
      5. metadata_ok        0.10   description >= 20 chars and tags non-empty

    Sum of weights = 1.0 by construction.
    """

    CHECK_WEIGHTS = {
        "yaml_loadable": 0.30,
        "test_cases_ok": 0.20,
        "dify_template_ok": 0.30,
        "not_applicable_ok": 0.10,
        "metadata_ok": 0.10,
    }

    def __init__(self, log_dir: Path | str | None = None):
        self.log_dir = Path(log_dir) if log_dir else self._default_log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def verify(self, atom: AtomDef) -> HealthReport:
        """Run all checks for one already-loaded atom.

        ``yaml_loadable`` is treated as PASS here because the atom was
        successfully loaded by the caller (otherwise we wouldn't have an
        AtomDef instance). For loader-failure paths, use ``verify_path``.
        """
        checks: list[HealthCheck] = []

        checks.append(HealthCheck(
            name="yaml_loadable",
            weight=self.CHECK_WEIGHTS["yaml_loadable"],
            passed=True,
            detail="loaded as AtomDef",
        ))

        checks.append(self._check_test_cases(atom))
        checks.append(self._check_dify_template(atom))
        checks.append(self._check_not_applicable(atom))
        checks.append(self._check_metadata(atom))

        pass_rate = sum(c.weight for c in checks if c.passed)
        report = HealthReport(
            asset_id=atom.asset_id,
            pass_rate=round(pass_rate, 4),
            verified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            checks=checks,
        )
        self.write_log(report)
        return report

    def verify_path(self, yaml_path: Path) -> HealthReport:
        """Verify by file path; useful when the loader itself rejects the yaml.

        Falls back to a yaml-loadable check that records the loader error.
        """
        from app.registry.atom_loader import AtomLoaderImpl

        try:
            loader = AtomLoaderImpl()
            atom = loader.load_one(yaml_path)
            return self.verify(atom)
        except Exception as exc:  # noqa: BLE001
            asset_id = self._guess_asset_id(yaml_path) or yaml_path.stem
            checks = [
                HealthCheck(
                    name="yaml_loadable",
                    weight=self.CHECK_WEIGHTS["yaml_loadable"],
                    passed=False,
                    detail=f"loader error: {exc}",
                ),
                HealthCheck(name="test_cases_ok", weight=self.CHECK_WEIGHTS["test_cases_ok"],
                            passed=False, detail="not evaluated (yaml unloadable)"),
                HealthCheck(name="dify_template_ok", weight=self.CHECK_WEIGHTS["dify_template_ok"],
                            passed=False, detail="not evaluated (yaml unloadable)"),
                HealthCheck(name="not_applicable_ok", weight=self.CHECK_WEIGHTS["not_applicable_ok"],
                            passed=False, detail="not evaluated (yaml unloadable)"),
                HealthCheck(name="metadata_ok", weight=self.CHECK_WEIGHTS["metadata_ok"],
                            passed=False, detail="not evaluated (yaml unloadable)"),
            ]
            report = HealthReport(
                asset_id=asset_id,
                pass_rate=0.0,
                verified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                checks=checks,
            )
            self.write_log(report)
            return report

    def verify_all(self, atoms: dict[str, AtomDef]) -> list[HealthReport]:
        return [self.verify(a) for a in atoms.values()]

    # ----- per-check implementations ------------------------------------

    def _check_test_cases(self, atom: AtomDef) -> HealthCheck:
        n = len(atom.test_cases or [])
        return HealthCheck(
            name="test_cases_ok",
            weight=self.CHECK_WEIGHTS["test_cases_ok"],
            passed=n >= 2,
            detail=f"declared={n} (required>=2)",
        )

    def _check_dify_template(self, atom: AtomDef) -> HealthCheck:
        proj = atom.projections.get("dify")
        if proj is None:
            return HealthCheck(
                name="dify_template_ok",
                weight=self.CHECK_WEIGHTS["dify_template_ok"],
                passed=False,
                detail="no dify projection declared",
            )
        if proj.not_supported:
            return HealthCheck(
                name="dify_template_ok",
                weight=self.CHECK_WEIGHTS["dify_template_ok"],
                passed=True,
                detail="atom marked dify not_supported (valid; e.g. cron trigger)",
            )
        if not proj.template:
            return HealthCheck(
                name="dify_template_ok",
                weight=self.CHECK_WEIGHTS["dify_template_ok"],
                passed=False,
                detail="dify.template empty",
            )
        tmpl_src = proj.template.strip()
        if tmpl_src.startswith("# Phase"):
            # legacy placeholder template - explicit fail
            return HealthCheck(
                name="dify_template_ok",
                weight=self.CHECK_WEIGHTS["dify_template_ok"],
                passed=False,
                detail="legacy placeholder template (not yet ported)",
            )
        try:
            env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
            tmpl = env.from_string(proj.template)
            rendered = tmpl.render(
                atom=atom,
                node=_DummyNode(),
                helpers=_DummyHelpers(),
            )
            data = yaml.safe_load(rendered)
        except Exception as exc:  # noqa: BLE001
            return HealthCheck(
                name="dify_template_ok",
                weight=self.CHECK_WEIGHTS["dify_template_ok"],
                passed=False,
                detail=f"render failed: {type(exc).__name__}: {exc}",
            )
        if not isinstance(data, dict):
            return HealthCheck(
                name="dify_template_ok",
                weight=self.CHECK_WEIGHTS["dify_template_ok"],
                passed=False,
                detail=f"rendered yaml is not a mapping (got {type(data).__name__})",
            )
        if "type" not in data or "title" not in data:
            return HealthCheck(
                name="dify_template_ok",
                weight=self.CHECK_WEIGHTS["dify_template_ok"],
                passed=False,
                detail=f"rendered missing required keys; got {sorted(data.keys())}",
            )
        return HealthCheck(
            name="dify_template_ok",
            weight=self.CHECK_WEIGHTS["dify_template_ok"],
            passed=True,
            detail=f"rendered {len(data)} keys ok",
        )

    def _check_not_applicable(self, atom: AtomDef) -> HealthCheck:
        n = len(atom.NOT_applicable or [])
        return HealthCheck(
            name="not_applicable_ok",
            weight=self.CHECK_WEIGHTS["not_applicable_ok"],
            passed=n >= 1,
            detail=f"items={n} (required>=1)",
        )

    def _check_metadata(self, atom: AtomDef) -> HealthCheck:
        desc_ok = len((atom.description or "").strip()) >= 20
        tags_ok = bool(atom.tags)
        return HealthCheck(
            name="metadata_ok",
            weight=self.CHECK_WEIGHTS["metadata_ok"],
            passed=desc_ok and tags_ok,
            detail=f"desc>=20={desc_ok} tags_nonempty={tags_ok}",
        )

    # ----- log persistence ----------------------------------------------

    def log_path(self, asset_id: str) -> Path:
        safe = asset_id.replace("/", "_").replace("\\", "_")
        return self.log_dir / f"{safe}.json"

    def write_log(self, report: HealthReport) -> Path:
        p = self.log_path(report.asset_id)
        p.write_text(report.to_json(), encoding="utf-8")
        return p

    def read_log(self, asset_id: str) -> dict[str, Any] | None:
        p = self.log_path(asset_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    # ----- internals ----------------------------------------------------

    @staticmethod
    def _default_log_dir() -> Path:
        # backend/app/delivery/atom_health_verifier.py -> harness root = parents[3]
        return Path(__file__).resolve().parents[3] / ".factory_health_log"

    @staticmethod
    def _guess_asset_id(yaml_path: Path) -> str | None:
        try:
            with yaml_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                return data.get("asset_id")
        except Exception:  # noqa: BLE001
            return None
        return None


# Dummy context objects used to render the Jinja2 template in dry-run mode.
# They expose the same attribute surface that real ResolvedNode/_TemplateHelpers
# use so a well-formed atom template renders without exceptions even though
# we never actually invoke the LLM / DB / HTTP.

class _DummyNode:
    id = "node_probe"
    asset_id = "atom.probe.v0"
    asset_version = "0.0.0"
    confidence = 0.0
    selection_reason = "verifier dry-run"
    inputs: dict = {"input_a": "ref"}
    params: dict = {}
    llm_task_type = "通用对话"
    llm_size = "中"
    llm_fallback_chain: list = ["中"]
    prompt_id = "prompt.dummy.v1"


class _DummyHelpers:
    @staticmethod
    def ruidong_model_placeholder(task_type: str | None, size: str | None) -> str:
        return "${RUIDONG_MODEL_FOR_dummy}"

    @staticmethod
    def uuid() -> str:
        return "00000000-0000-0000-0000-000000000000"

    @staticmethod
    def system_prompt(node) -> str:  # noqa: ANN001
        return "verifier system probe"

    @staticmethod
    def code_variables(node) -> list[dict[str, Any]]:  # noqa: ANN001
        return [{"variable": k, "value_selector": []} for k in node.inputs.keys()]
