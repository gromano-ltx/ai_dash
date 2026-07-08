from backend.pricing import estimate_cost


def test_matches_anthropic_sonnet_tier():
    # claude-sonnet-4-5 — NOT the sonnet-5 special case, so it always uses
    # the plain (non-time-boundaried) sonnet tier price.
    result = estimate_cost("anthropic", "claude-sonnet-4-5-20250929", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 3.00
    assert result.output_usd == 15.00
    assert result.total_usd == 18.00


def test_matches_anthropic_sonnet_5_introductory_price():
    # Introductory pricing ($2/$10) applies through 2026-08-31; "today" in
    # this test suite is always before that cutover.
    result = estimate_cost("anthropic", "claude-sonnet-5", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 2.00
    assert result.output_usd == 10.00


def test_matches_anthropic_opus_tier():
    result = estimate_cost("anthropic", "claude-opus-4-20250514", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 5.00
    assert result.output_usd == 25.00


def test_matches_anthropic_haiku_tier():
    result = estimate_cost("anthropic", "claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 1.00
    assert result.output_usd == 5.00


def test_matches_openai_tier():
    result = estimate_cost("openai", "gpt-5-codex", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 1.25
    assert result.output_usd == 10.00


def test_matches_gemini_tier():
    result = estimate_cost("gemini", "gemini-3.5-flash", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 1.50
    assert result.output_usd == 9.00


def test_matches_openai_tier_for_a_different_model_version():
    result = estimate_cost("openai", "gpt-6-codex-preview", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 1.25
    assert result.output_usd == 10.00


def test_matches_gemini_tier_for_a_different_model_version():
    result = estimate_cost("gemini", "gemini-4.0-flash", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 1.50
    assert result.output_usd == 9.00


def test_cached_input_tokens_priced_at_ten_percent_of_input_rate():
    # sonnet's input rate is $3/MTok, so 1M fully-cached input tokens should
    # cost $0.30 (10%), folded into input_usd (no separate cached field).
    result = estimate_cost("anthropic", "claude-sonnet-4-5-20250929", 0, 0, cached_input_tokens=1_000_000)
    assert result is not None
    assert round(result.input_usd, 4) == 0.30
    assert round(result.total_usd, 4) == 0.30


def test_cached_input_tokens_default_to_zero():
    with_cache = estimate_cost("anthropic", "claude-sonnet-4-5-20250929", 1000, 0)
    without_cache_arg = estimate_cost("anthropic", "claude-sonnet-4-5-20250929", 1000, 0, 0)
    assert with_cache == without_cache_arg


def test_matching_is_case_insensitive():
    result = estimate_cost("anthropic", "Claude-SONNET-4-5-20250929", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 3.00


def test_unknown_provider_returns_none():
    assert estimate_cost("azure", "gpt-4", 1000, 1000) is None


def test_unmatched_model_within_known_provider_returns_none():
    assert estimate_cost("anthropic", "claude-instant-1.2", 1000, 1000) is None


def test_zero_tokens_returns_zero_cost_not_none():
    result = estimate_cost("anthropic", "claude-sonnet-4-5-20250929", 0, 0)
    assert result is not None
    assert result.input_usd == 0.0
    assert result.output_usd == 0.0
    assert result.total_usd == 0.0


def test_total_is_input_plus_output():
    result = estimate_cost("anthropic", "claude-sonnet-4-5-20250929", 500_000, 250_000)
    assert result is not None
    assert result.total_usd == result.input_usd + result.output_usd
    assert round(result.input_usd, 4) == 1.50
    assert round(result.output_usd, 4) == 3.75
