"""UI domain objects — re-export existing contracts under the Ontology namespace.

These are the UI-line domain-specific Objects (RequirementSpec, UIBlueprint,
CodeArtifact, ...) aligned with the generic Artifact Object in
``ontology/objects/artifact.py``.

Backward compatible: the authoritative definitions still live in
``app.core.stability.contracts``. This file is a namespace bridge so new code
can write:

    from app.ontology.objects.ui import UIBlueprint

while legacy imports keep working.
"""
from __future__ import annotations

from app.core.stability.contracts import (
    BrandTokens,
    CodeArtifact,
    FileEntry,
    PageBlueprint,
    RequirementSpec,
    UIBlueprint,
)

__all__ = [
    "RequirementSpec",
    "PageBlueprint",
    "BrandTokens",
    "UIBlueprint",
    "FileEntry",
    "CodeArtifact",
]
