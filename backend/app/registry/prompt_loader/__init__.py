"""Prompt Loader: load and validate prompt YAML files into PromptDef Pydantic.

Activates registry shelf #2 (Prompts) per [[资产中心/总览]].
Prompts are first-class versioned assets referenced by ResolvedNode.prompt_id.
"""

from app.registry.prompt_loader.interface import PromptLoader
from app.registry.prompt_loader.impl import PromptLoaderImpl
from app.registry.prompt_loader.models import (
    PromptDef,
    PromptInput,
    PromptMetrics,
    PromptOutputSchema,
    PromptProvenance,
    PromptTestCase,
)

__all__ = [
    "PromptDef",
    "PromptInput",
    "PromptLoader",
    "PromptLoaderImpl",
    "PromptMetrics",
    "PromptOutputSchema",
    "PromptProvenance",
    "PromptTestCase",
]
