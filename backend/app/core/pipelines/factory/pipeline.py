"""FactoryPipeline - top-level orchestration entry.

Composes IntentParser -> Resolver -> DSLCompiler -> DSLValidator.
Phase 2 wraps this in 6 Gate state machine.

Phase 1 W3 day 5 will tie everything together.
"""

from __future__ import annotations


class FactoryPipeline:
    """Top-level entry to the factory compiler.

    Phase 1: Synchronous "build once" mode.
    Phase 2: 6 Gate state machine wraps this.
    """

    def __init__(
        self,
        intent_parser=None,
        resolver=None,
        compiler=None,
        validator=None,
    ) -> None:
        self._intent_parser = intent_parser
        self._resolver = resolver
        self._compiler = compiler
        self._validator = validator

    async def build(self, nl: str) -> dict:
        raise NotImplementedError(
            "FactoryPipeline.build() will be wired in Phase 1 W3 day 5."
        )
