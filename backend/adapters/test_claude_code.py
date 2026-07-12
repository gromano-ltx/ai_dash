import json
from unittest.mock import patch

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


def test_parse_transcript_content_does_not_call_get_user():
    # AI-51 finding 1: parse_transcript_content() is invoked directly from
    # backend.api.routes.ingest_transcript (an async route), which always
    # overwrites run.user with api_key.user two lines later — so calling the
    # blocking `git config user.name` subprocess here is pure wasted work
    # (and needlessly stalls the event loop). The real user for the local
    # watcher path is attached separately by parse_transcript() below.
    with patch("backend.adapters.claude_code._get_user", side_effect=AssertionError("should not be called")):
        run = parse_transcript_content(_sample_session())
    assert run.user is None


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


def test_parse_transcript_content_captures_cache_creation_input_tokens_in_meta():
    run = parse_transcript_content(_sample_session())
    # req-1 (deduped) cache_creation_input_tokens=50 + req-2's 0 = 50.
    assert run.meta["cache_creation_input_tokens"] == 50


def test_parse_transcript_content_status_running_when_mtime_is_recent():
    import time
    run = parse_transcript_content(_sample_session(), mtime=time.time())
    assert run.status == "running"
    assert run.ended_at is None


def test_parse_transcript_content_status_done_when_mtime_is_old():
    import time
    run = parse_transcript_content(_sample_session(), mtime=time.time() - 3600)
    assert run.status == "done"
    assert run.ended_at is not None


def test_parse_transcript_content_status_running_just_under_5min_threshold():
    import time
    run = parse_transcript_content(_sample_session(), mtime=time.time() - 299)
    assert run.status == "running"


def test_parse_transcript_content_status_done_at_5min_threshold():
    import time
    run = parse_transcript_content(_sample_session(), mtime=time.time() - 300)
    assert run.status == "done"


def test_parse_transcript_content_status_done_when_no_mtime_given():
    run = parse_transcript_content(_sample_session())
    assert run.status == "done"


def test_parse_transcript_still_sets_user_for_local_watcher_path(tmp_path):
    # parse_transcript(path) backs the local watcher.py scan, which has no
    # api_key/user override — unlike parse_transcript_content() called
    # directly from the HTTP ingest route, this path must still attach the
    # real local git user.
    from backend.adapters.claude_code import parse_transcript

    jsonl_path = tmp_path / "sess-1.jsonl"
    jsonl_path.write_text(_sample_session())
    with patch("backend.adapters.claude_code._get_user", return_value="gabby"):
        run = parse_transcript(jsonl_path)
    assert run.user == "gabby"
