"""ModelRouter: maps (task_type, size) -> concrete model name at runtime.

Per [[资产中心/横切-LLM模型路由表]] core rule: NEVER hardcode model names.
Instead, atoms specify llm_task_type + llm_size, and ModelRouter resolves
the actual model id by either:
  1. settings.harness_default_model (V1 simple)
  2. Querying /v1/models with task-type/size filtering (Phase 2)

Phase 2 will also add fallback chain handling per llm_fallback_chain.
"""

from __future__ import annotations

from typing import Literal

from app.config import get_settings


Size = Literal["小", "中", "大"]


class ModelRouter:
    """V1: returns settings.harness_default_model regardless of task_type/size.

    Trace emits the requested (task_type, size) for future telemetry-based
    routing decisions.
    """

    def __init__(self, default_model: str | None = None) -> None:
        self._default_model = default_model

    def resolve(
        self,
        task_type: str,
        size: Size,
        properties: list[str] | None = None,
    ) -> str:
        """Return a concrete model id.

        Args:
            task_type: One of [[横切-LLM模型路由表]] task types.
            size: '小' / '中' / '大'.
            properties: Hints like ['json_response_format', 'long_context'].

        Returns:
            Model id usable with LLMClient.chat(model=...).

        Raises:
            ValueError: If no model is configured.
        """
        if self._default_model:
            return self._default_model
        settings = get_settings()
        if not settings.harness_default_model:
            raise ValueError(
                "No model configured. Set HARNESS_DEFAULT_MODEL env or pass "
                "default_model to ModelRouter. ModelRouter V1 does not yet "
                "auto-discover; Phase 2 will add /v1/models routing."
            )
        return settings.harness_default_model

    async def fallback_chain(
        self,
        task_type: str,
        size: Size,
    ) -> list[str]:
        """V1 stub: returns just [primary]. Phase 2 expands to real chain."""
        primary = self.resolve(task_type, size)
        return [primary]
