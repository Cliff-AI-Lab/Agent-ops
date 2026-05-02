"""Phase 7 MultiAgentComposer component."""
from app.core.pipelines.factory.multi_agent_composer.interface import MultiAgentComposer
from app.core.pipelines.factory.multi_agent_composer.impl import (
    OpenAIAgentsSDKComposerImpl,
    _agent_var,
)

__all__ = [
    "MultiAgentComposer",
    "OpenAIAgentsSDKComposerImpl",
    "_agent_var",
]
