"""Validator component.

DSL -> ValidationReport (schema check + dry-run + LLM repair loop).
"""

from app.core.pipelines.factory.validator.interface import DSLValidator
from app.core.pipelines.factory.validator.impl import DSLValidatorImpl

__all__ = ["DSLValidator", "DSLValidatorImpl"]
