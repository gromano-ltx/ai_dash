from datetime import date, datetime
from typing import NamedTuple, Optional


class ModelPrice(NamedTuple):
    input_per_1m_usd: float
    output_per_1m_usd: float


class EstimatedCost(NamedTuple):
    input_usd: float
    output_usd: float
    total_usd: float


# Cache reads are billed at a fraction of the base input price rather than
# being free. Verified against each provider's current official pricing docs
# (all three land on the same 10% figure):
#   Anthropic: https://platform.claude.com/docs/en/about-claude/pricing ("Cache read (hit): 0.1x base input price")
#   OpenAI:    https://developers.openai.com/api/docs/pricing (cached input = 10% of input, e.g. gpt-5.4 $2.50 -> $0.25)
#   Gemini:    https://ai.google.dev/gemini-api/docs/pricing (gemini-3.5-flash: $1.50 input -> $0.15 cached)
# Folded into `input_usd` below (not a separate field) since AgentRun only
# has input/output/total cost columns.
CACHE_READ_MULTIPLIER = 0.1

# Cache *writes* are a premium, not a discount: Anthropic charges 1.25x the
# base input price for a 5-minute cache write (2x for a 1-hour write — we
# have no way to tell which TTL was used from the aggregate usage field, so
# 1.25x is used as the conservative/common-case default). Source:
# https://platform.claude.com/docs/en/about-claude/pricing ("5-minute cache
# write: 1.25x base input price"). Only Claude Code's transcripts expose a
# cache_creation_input_tokens figure — Codex/Gemini's caching is fully
# automatic with no separate write-side token count, so this multiplier is
# only ever applied when that value is non-zero (anthropic rows).
CACHE_WRITE_MULTIPLIER = 1.25

# Keyed by provider, then an ordered list of (tier keyword, price) pairs.
# Matched as a case-insensitive substring of the run's `model` string: model
# strings are exact, dated/versioned IDs with no normalization anywhere in
# this codebase, so keyword matching survives new dated releases without a
# code change. First match wins, so order matters if keywords could overlap.
#
# Verified against official pricing pages on 2026-07-08:
#   Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
#   OpenAI:    https://developers.openai.com/api/docs/pricing (bare "gpt-5-codex", not the newer gpt-5.3-codex)
#   Gemini:    https://ai.google.dev/gemini-api/docs/pricing
PRICING: dict[str, list[tuple[str, ModelPrice]]] = {
    "anthropic": [
        # claude-sonnet-5 has its own time-boundaried introductory price —
        # handled as a special case in estimate_cost() below, checked before
        # this generic "sonnet" tier (which still covers 4.5/4.6).
        ("opus", ModelPrice(5.00, 25.00)),
        ("sonnet", ModelPrice(3.00, 15.00)),
        ("haiku", ModelPrice(1.00, 5.00)),
    ],
    "openai": [
        ("codex", ModelPrice(1.25, 10.00)),
    ],
    "gemini": [
        ("flash", ModelPrice(1.50, 9.00)),
    ],
}

# Claude Sonnet 5 introductory pricing: $2/$10 per MTok through 2026-08-31,
# then standard $3/$15 from 2026-09-01. Source: platform.claude.com pricing
# docs (see PRICING comment above).
_SONNET_5_INTRO_PRICE = ModelPrice(2.00, 10.00)
_SONNET_5_STANDARD_PRICE = ModelPrice(3.00, 15.00)
_SONNET_5_PRICE_CUTOVER = date(2026, 9, 1)


def _sonnet_5_price() -> ModelPrice:
    if datetime.utcnow().date() < _SONNET_5_PRICE_CUTOVER:
        return _SONNET_5_INTRO_PRICE
    return _SONNET_5_STANDARD_PRICE


def estimate_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> Optional[EstimatedCost]:
    model_lower = model.lower()

    price = None
    if provider == "anthropic" and "sonnet-5" in model_lower:
        price = _sonnet_5_price()
    else:
        tiers = PRICING.get(provider)
        if tiers:
            price = next((p for keyword, p in tiers if keyword in model_lower), None)

    if price is None:
        return None

    input_usd = input_tokens / 1_000_000 * price.input_per_1m_usd
    cache_read_usd = cached_input_tokens / 1_000_000 * price.input_per_1m_usd * CACHE_READ_MULTIPLIER
    cache_write_usd = cache_creation_input_tokens / 1_000_000 * price.input_per_1m_usd * CACHE_WRITE_MULTIPLIER
    total_input_usd = input_usd + cache_read_usd + cache_write_usd
    output_usd = output_tokens / 1_000_000 * price.output_per_1m_usd
    return EstimatedCost(total_input_usd, output_usd, total_input_usd + output_usd)
