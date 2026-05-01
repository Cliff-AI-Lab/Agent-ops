"""IntentParser component.

NL -> StructuredIntent via LLM with JSON Schema-strict output.
"""

from app.core.pipelines.factory.intent_parser.interface import IntentParser
from app.core.pipelines.factory.intent_parser.impl import IntentParserImpl

__all__ = ["IntentParser", "IntentParserImpl"]
