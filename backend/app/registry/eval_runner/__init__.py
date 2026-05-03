"""EvalRunner: load eval cases, run through factory pipeline, score.

Phase 1 W2 + Phase 4 prep: this is shelf #8 (EvalSets) going live.
"""

from app.registry.eval_runner.models import (
    EvalCase,
    EvalCaseResult,
    EvalReport,
    EvalSet,
    ExpectedShape,
)
from app.registry.eval_runner.interface import EvalRunner
from app.registry.eval_runner.impl import EvalRunnerImpl
from app.registry.eval_runner.loader import load_eval_set, load_eval_sets
from app.registry.eval_runner.multi_agent_runner import MultiAgentEvalRunnerImpl

__all__ = [
    "EvalCase",
    "EvalCaseResult",
    "EvalReport",
    "EvalRunner",
    "EvalRunnerImpl",
    "MultiAgentEvalRunnerImpl",
    "EvalSet",
    "ExpectedShape",
    "load_eval_set",
    "load_eval_sets",
]
