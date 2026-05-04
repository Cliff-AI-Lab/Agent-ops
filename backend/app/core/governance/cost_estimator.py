"""HeuristicCostEstimator - V2.2 W1 simple cost estimation.

Strategy: rough token estimate from NL length + known per-stage call counts,
multiplied by configurable per-1k-token CNY price.

V2.2: heuristic only (deterministic, fast, no API call).
V2.3+: switch to tiktoken / harness LLMClient.estimate_tokens.

All prices configurable via constructor; defaults are conservative for
"medium" model class (e.g., 4o-mini, qwen-plus equivalents).
"""
from __future__ import annotations

from app.core.governance.interface import CostEstimate


# Default rough conversion: 1 Chinese char ~= 1 token (input/output proxy)
# Pricing CNY per 1k tokens (medium-class model assumptions)
DEFAULT_PRICE_INPUT_PER_1K_CNY = 0.07
DEFAULT_PRICE_OUTPUT_PER_1K_CNY = 0.21


class HeuristicCostEstimator:
    """V2.2 W1 estimator. Token counting is char-length proxy."""

    def __init__(
        self,
        price_input_per_1k_cny: float = DEFAULT_PRICE_INPUT_PER_1K_CNY,
        price_output_per_1k_cny: float = DEFAULT_PRICE_OUTPUT_PER_1K_CNY,
    ) -> None:
        self.in_price = price_input_per_1k_cny
        self.out_price = price_output_per_1k_cny

    def _cost(self, tokens_in: int, tokens_out: int) -> float:
        return round(
            tokens_in / 1000 * self.in_price + tokens_out / 1000 * self.out_price,
            4,
        )

    def estimate_single(self, nl: str) -> CostEstimate:
        """FactoryPipeline single-agent path estimate.

        Stages:
          1. IntentParser   ~1 LLM call: NL in, ~500 tokens system prompt + ~800 tokens out
          2. Resolver       ~1-3 LLM calls (depends on number of steps; assume 2)
          3. Composer       0 LLM calls (template)
          4. Validator      0 LLM calls (schema check)
        Total: ~3 LLM calls.
        """
        nl_tokens = max(len(nl), 20)
        # System prompts + boilerplate
        system_overhead_in = 500 + 600  # intent system prompt + resolver rank prompt
        # User content per call ~ NL itself + recall context (~100 tokens per atom listed)
        recall_context = 5 * 100  # 5 candidate atoms typical

        intent_in = nl_tokens + 500
        intent_out = 800
        resolver_in = (nl_tokens + recall_context + 600) * 2  # 2 rank calls
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
                f"heuristic single-agent estimate (in={tokens_in}t out={tokens_out}t)",
                f"prices: in={self.in_price}/k out={self.out_price}/k CNY",
            ],
            confidence=0.7,
        )

    def estimate_multi(
        self, nl: str, anticipated_specialists: int = 5
    ) -> CostEstimate:
        """Multi-agent path estimate.

        Stages:
          1. IndustryRouter classify  ~1 LLM call
          2. Designer extraction      ~1 LLM call (LLM names specialists; output scales with count)
          3. Composer                 0 LLM calls
        Total: ~2 LLM calls plus output scales with specialists.
        """
        if anticipated_specialists < 1:
            anticipated_specialists = 1
        nl_tokens = max(len(nl), 20)

        classify_in = nl_tokens + 500
        classify_out = 200

        designer_system = 700  # system prompt with schema
        # Output: per specialist ~200 tokens (id/name/desc/nl_brief/handoffs)
        designer_in = nl_tokens + designer_system
        designer_out = 200 * anticipated_specialists + 100  # + triage targets / shared ctx

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
                f"heuristic multi-agent estimate (specialists={anticipated_specialists})",
                f"prices: in={self.in_price}/k out={self.out_price}/k CNY",
            ],
            confidence=0.6,  # lower than single since specialist count is anticipated
        )
