"""TiktokenCostEstimator - V2.3 W1 real token counting via tiktoken.

Replaces V2.2 W1 char-length proxy. Uses cl100k_base (gpt-3.5/4 family
encoding) by default — close enough for OpenAI-compatible models served
by iruidong gateway. Falls back to char-length proxy if tiktoken import
fails (e.g., minimal environment).

Same Protocol as HeuristicCostEstimator; drop-in replacement.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.governance.cost_estimator import (
    DEFAULT_PRICE_INPUT_PER_1K_CNY,
    DEFAULT_PRICE_OUTPUT_PER_1K_CNY,
    HeuristicCostEstimator,
)
from app.core.governance.interface import CostEstimate

logger = logging.getLogger(__name__)


# System prompt token estimates (one-time encode cached at module load if available).
_INTENT_SYSTEM_TOKENS = 500
_RESOLVER_SYSTEM_TOKENS = 600
_CLASSIFY_SYSTEM_TOKENS = 500
_DESIGNER_SYSTEM_TOKENS = 700


def _try_import_tiktoken():
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return enc
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "tiktoken unavailable (%s); TiktokenCostEstimator falling back to char proxy",
            exc,
        )
        return None


class TiktokenCostEstimator(HeuristicCostEstimator):
    """V2.3: real token counting via tiktoken; falls back to heuristic on import failure.

    Inherits HeuristicCostEstimator so all V2.2 W1 tests pass with this drop-in.
    Override estimate_single / estimate_multi to use real token counts on user NL.
    """

    def __init__(
        self,
        price_input_per_1k_cny: float = DEFAULT_PRICE_INPUT_PER_1K_CNY,
        price_output_per_1k_cny: float = DEFAULT_PRICE_OUTPUT_PER_1K_CNY,
        encoding_name: str = "cl100k_base",
    ) -> None:
        super().__init__(price_input_per_1k_cny, price_output_per_1k_cny)
        self._enc = _try_import_tiktoken()
        if self._enc is None:
            self._encoding_name = "char-proxy-fallback"
        else:
            self._encoding_name = encoding_name

    def _count_tokens(self, text: str) -> int:
        if self._enc is None:
            return max(len(text), 20)  # char-proxy fallback (matches V2.2 W1)
        return len(self._enc.encode(text))

    def estimate_single(self, nl: str) -> CostEstimate:
        nl_tokens = self._count_tokens(nl)
        recall_context_tokens = 5 * 100  # 5 candidate atoms typical

        intent_in = nl_tokens + _INTENT_SYSTEM_TOKENS
        intent_out = 800
        resolver_in = (nl_tokens + recall_context_tokens + _RESOLVER_SYSTEM_TOKENS) * 2
        resolver_out = 200 * 2

        tokens_in = intent_in + resolver_in
        tokens_out = intent_out + resolver_out
        breakdown = {
            "intent_parser": self._cost(intent_in, intent_out),
            "resolver": self._cost(resolver_in, resolver_out),
            "compiler": 0.0,
            "validator": 0.0,
        }
        total = sum(breakdown.values())
        return CostEstimate(
            estimated_total_cny=round(total, 4),
            breakdown=breakdown,
            estimated_llm_calls=3,
            estimated_tokens_in=tokens_in,
            estimated_tokens_out=tokens_out,
            notes=[
                f"tiktoken single-agent estimate (encoding={self._encoding_name}, nl_tokens={nl_tokens})",
                f"prices: in={self.in_price}/k out={self.out_price}/k CNY",
            ],
            confidence=0.85,  # higher than heuristic (real token count)
        )

    def estimate_multi(
        self, nl: str, anticipated_specialists: int = 5
    ) -> CostEstimate:
        if anticipated_specialists < 1:
            anticipated_specialists = 1
        nl_tokens = self._count_tokens(nl)

        classify_in = nl_tokens + _CLASSIFY_SYSTEM_TOKENS
        classify_out = 200

        designer_in = nl_tokens + _DESIGNER_SYSTEM_TOKENS
        designer_out = 200 * anticipated_specialists + 100

        tokens_in = classify_in + designer_in
        tokens_out = classify_out + designer_out
        breakdown = {
            "industry_router": self._cost(classify_in, classify_out),
            "designer_extraction": self._cost(designer_in, designer_out),
            "composer": 0.0,
        }
        total = sum(breakdown.values())
        return CostEstimate(
            estimated_total_cny=round(total, 4),
            breakdown=breakdown,
            estimated_llm_calls=2,
            estimated_tokens_in=tokens_in,
            estimated_tokens_out=tokens_out,
            notes=[
                f"tiktoken multi-agent estimate (specialists={anticipated_specialists}, encoding={self._encoding_name})",
                f"prices: in={self.in_price}/k out={self.out_price}/k CNY",
            ],
            confidence=0.75,  # higher than V2.2 W1 multi (0.6)
        )

    @property
    def encoding_name(self) -> str:
        return self._encoding_name
