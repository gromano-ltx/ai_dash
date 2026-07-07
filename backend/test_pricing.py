from backend.pricing import estimate_cost


def test_matches_anthropic_sonnet_tier():
    result = estimate_cost("anthropic", "claude-sonnet-4-5-20250929", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 3.00
    assert result.output_usd == 15.00
    assert result.total_usd == 18.00


def test_matches_anthropic_opus_tier():
    result = estimate_cost("anthropic", "claude-opus-4-20250514", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 15.00
    assert result.output_usd == 75.00


def test_matches_anthropic_haiku_tier():
    result = estimate_cost("anthropic", "claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 0.80
    assert result.output_usd == 4.00


def test_matches_openai_tier():
    result = estimate_cost("openai", "gpt-5-codex", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 1.25
    assert result.output_usd == 10.00


def test_matches_gemini_tier():
    result = estimate_cost("gemini", "gemini-3.5-flash", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 0.35
    assert result.output_usd == 1.05


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
