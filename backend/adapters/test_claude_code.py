import json

from backend.adapters.claude_code import parse_transcript_content


def _line(d: dict) -> str:
    return json.dumps(d)


def _sample_session() -> str:
    """A minimal, schema-accurate synthetic Claude Code session transcript,
    focused on token/cache accounting (this adapter's other behaviors —
    commit/PR extraction, ticket refs, subagent detection — are already
    covered by production usage predating this test file's existence;
    this file's scope is just the new cached_input_tokens capture)."""
    lines = [
        _line({
            "type": "user",
            "timestamp": "2026-04-16T16:01:55.000Z",
            "isMeta": True,
            "sessionId": "sess-1",
            "gitBranch": "main",
            "cwd": "/Users/gromano/repos/ai_dash",
        }),
        _line({
            "type": "assistant",
            "timestamp": "2026-04-16T16:02:00.000Z",
            "sessionId": "sess-1",
            "requestId": "req-1",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 300,
                    "cache_creation_input_tokens": 50,
                },
                "content": [],
            },
        }),
        # Duplicate requestId — must not be double-counted (existing seen_request_ids dedup).
        _line({
            "type": "assistant",
            "timestamp": "2026-04-16T16:02:00.500Z",
            "sessionId": "sess-1",
            "requestId": "req-1",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 300,
                    "cache_creation_input_tokens": 50,
                },
                "content": [],
            },
        }),
        _line({
            "type": "assistant",
            "timestamp": "2026-04-16T16:03:00.000Z",
            "sessionId": "sess-1",
            "requestId": "req-2",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 150,
                    "cache_read_input_tokens": 250,
                    "cache_creation_input_tokens": 0,
                },
                "content": [],
            },
        }),
    ]
    return "\n".join(lines) + "\n"


def test_parse_transcript_content_returns_none_for_empty_content():
    assert parse_transcript_content("") is None


def test_parse_transcript_content_sets_provider_anthropic():
    run = parse_transcript_content(_sample_session())
    assert run.provider == "anthropic"


def test_parse_transcript_content_input_and_output_tokens_unaffected_by_fix():
    run = parse_transcript_content(_sample_session())
    # req-1 (deduped, not double-counted) + req-2: input 5+3=8, output 200+150=350.
    # Anthropic's usage.input_tokens already excludes cache reads — no change
    # to this math from this fix, verified explicitly here.
    assert run.input_tokens == 8
    assert run.output_tokens == 350


def test_parse_transcript_content_captures_cached_input_tokens_in_meta():
    run = parse_transcript_content(_sample_session())
    # req-1 (deduped) cache_read_input_tokens=300 + req-2's 250 = 550.
    assert run.meta["cached_input_tokens"] == 550
