"""Canonical Ontology Objects for Agent Ops (blueprint §8).

All types exported here are the authoritative Ontology vocabulary used across
agents, tools, workflows, and replay logs.
"""
from __future__ import annotations

from app.ontology.objects.agent_card import (
    AgentCard,
    AuthMode,
    CostProfile,
    HealthStatus,
    TransportType,
)
from app.ontology.objects.agent_contract import (
    AgentContract,
    AgentExample,
    AgentGenerationMetadata,
    AgentTier,
)
from app.ontology.objects.artifact import Artifact, ArtifactType
from app.ontology.objects.capability_contract import (
    ActivationStatus,
    CapabilityContract,
    RiskLevel,
)
from app.ontology.objects.eval import EvalCase, EvalOutcome, EvalResult
from app.ontology.objects.generated_asset import AssetStatus, GeneratedAsset
from app.ontology.objects.policy_decision import (
    ActionCategory,
    Decision,
    PolicyDecision,
)
from app.ontology.objects.run_event import EventType, Layer, RunEvent
from app.ontology.objects.task_run import CostSummary, RunStatus, StepRun, TaskRun
from app.ontology.objects.preset_bundle import (
    PresetActivation,
    PresetBundle,
    PresetEntryInjection,
    PresetFile,
    PresetFramework,
)
from app.ontology.objects.tool_contract import (
    PricingModel,
    ToolActivation,
    ToolAuthMode,
    ToolCategory,
    ToolContract,
    ToolExample,
    ToolTransport,
)
from app.product_types import ALL_PRODUCT_TYPES, UI_PRODUCT_TYPES, ProductType
from app.ontology.objects.preset_bundle import (
    PresetActivation,
    PresetBundle,
    PresetEntryInjection,
    PresetFile,
    PresetFramework,
)
from app.ontology.objects.ui import (
    BrandTokens,
    CodeArtifact,
    FileEntry,
    PageBlueprint,
    RequirementSpec,
    UIBlueprint,
)
from app.ontology.objects.workflow_spec import (
    ApprovalNode,
    FailureAction,
    FailurePolicy,
    SuccessCriterion,
    TriggerType,
    WorkflowSpec,
    WorkflowStep,
)

__all__ = [
    # §8.1
    "CapabilityContract", "RiskLevel", "ActivationStatus",
    # §8.2
    "AgentCard", "TransportType", "AuthMode", "CostProfile", "HealthStatus",
    # AgentContract (Batch Y — three-tier ontology pyramid top tier)
    "AgentContract", "AgentExample", "AgentGenerationMetadata", "AgentTier",
    # §8.3
    "WorkflowSpec", "WorkflowStep", "ApprovalNode", "FailurePolicy",
    "SuccessCriterion", "TriggerType", "FailureAction",
    # §8.4 / §8.5
    "TaskRun", "StepRun", "RunStatus", "CostSummary",
    # §8.6
    "Artifact", "ArtifactType",
    # §8.7
    "RunEvent", "EventType", "Layer",
    # §7 Policy (prepared for Batch G)
    "PolicyDecision", "Decision", "ActionCategory",
    # M1 Eval (prepared for Batch G)
    "EvalCase", "EvalResult", "EvalOutcome",
    # Batch X — Marketplace / Asset Hub
    "GeneratedAsset", "AssetStatus",
    # Tool Wiki (Batch C+)
    "ToolContract", "ToolExample", "ToolCategory", "ToolTransport",
    "ToolAuthMode", "ToolActivation", "PricingModel",
    # PresetBundle (Batch C++)
    "PresetBundle", "PresetFile", "PresetEntryInjection",
    "PresetFramework", "PresetActivation",
    # ProductType (Batch C++++)
    "ProductType", "ALL_PRODUCT_TYPES", "UI_PRODUCT_TYPES",
    # UI domain (re-exports)
    "RequirementSpec", "PageBlueprint", "BrandTokens",
    "UIBlueprint", "FileEntry", "CodeArtifact",
]
