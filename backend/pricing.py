from typing import NamedTuple, Optional


class ModelPrice(NamedTuple):
    input_per_1m_usd: float
    output_per_1m_usd: float


class EstimatedCost(NamedTuple):
    input_usd: float
    output_usd: float
    total_usd: float


# Keyed by provider, then an ordered list of (tier keyword, price) pairs.
# Matched as a case-insensitive substring of the run's `model` string: model
# strings are exact, dated/versioned IDs with no normalization anywhere in
# this codebase, so keyword matching survives new dated releases without a
# code change. First match wins, so order matters if keywords could overlap.
#
# NEEDS VERIFICATION against current official pricing before merging: these
# are best-effort placeholder figures, not confirmed current prices.
PRICING: dict[str, list[tuple[str, ModelPrice]]] = {
    "anthropic": [
        ("opus", ModelPrice(15.00, 75.00)),
        ("sonnet", ModelPrice(3.00, 15.00)),
        ("haiku", ModelPrice(0.80, 4.00)),
    ],
    "openai": [
        ("gpt-5-codex", ModelPrice(1.25, 10.00)),
    ],
    "gemini": [
        ("gemini-3.5-flash", ModelPrice(0.35, 1.05)),
    ],
}


def estimate_cost(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> Optional[EstimatedCost]:
    tiers = PRICING.get(provider)
    if not tiers:
        return None
    model_lower = model.lower()
    for keyword, price in tiers:
        if keyword in model_lower:
            input_usd = input_tokens / 1_000_000 * price.input_per_1m_usd
            output_usd = output_tokens / 1_000_000 * price.output_per_1m_usd
            return EstimatedCost(input_usd, output_usd, input_usd + output_usd)
    return None
