import json

from backend.adapters.gemini_cli import parse_transcript_content


def _line(d: dict) -> str:
    return json.dumps(d)


def _sample_session() -> str:
    """A minimal, schema-accurate synthetic Gemini CLI session transcript.

    Real Gemini CLI JSONL is a hybrid checkpoint/event log: the first line is a
    header (sessionId/startTime/kind, no "type" key), most lines are standalone
    events with a top-level "type" ("user"/"gemini"/"info"), and one early line
    wraps the very first real message inside {"$set": {"messages": [...]}}.
    Later housekeeping "$set" lines (lastUpdated-only, or the final
    summary/memoryScratchpad line) carry no "messages" key and are ignored.
    """
    lines = [
        # Header line — no "type" key, no "$set" key.
        _line({
            "sessionId": "06ba9b64-5701-4a4e-b7eb-4bac2b449d5c",
            "projectHash": "7caa8e06c56b60fb427b988dd636bd970a01de49287b4b5f3231498ba62d6096",
            "startTime": "2026-06-29T13:49:00.000Z",
            "lastUpdated": "2026-06-29T13:49:00.000Z",
            "kind": "main",
        }),
        # First real message, wrapped in the initial $set checkpoint — injected
        # <session_context> text that must be skipped for label/task purposes.
        _line({
            "$set": {
                "messages": [{
                    "id": "d04923d38bb0f6017037e74183378ef4",
                    "timestamp": "2026-06-29T13:49:00.100Z",
                    "type": "user",
                    "content": [{"text": "<session_context>\nThis is the Gemini CLI...\n"}],
                }],
                "lastUpdated": "2026-06-29T13:49:00.100Z",
            },
        }),
        # Housekeeping $set line with no "messages" key — must be ignored, not crash.
        _line({"$set": {"lastUpdated": "2026-06-29T13:49:00.200Z"}}),
        # An "info" event — must be ignored for content extraction, but its
        # timestamp still counts toward the session's last-seen timestamp.
        _line({
            "id": "info-1",
            "timestamp": "2026-06-29T13:49:00.300Z",
            "type": "info",
            "content": "You have 1 extension with an update available.",
        }),
        # The real first user message.
        _line({
            "id": "user-1",
            "timestamp": "2026-06-29T13:49:01.000Z",
            "type": "user",
            "content": [{"text": "Fix the AI-47 ingestion bug please"}],
        }),
        # First "gemini" turn, with a shell tool call that commits — command
        # and result live together in the same object (no cross-event pairing
        # needed, unlike Claude Code/Codex).
        _line({
            "id": "gemini-1",
            "timestamp": "2026-06-29T13:49:05.000Z",
            "type": "gemini",
            "content": "",
            "model": "gemini-3.5-flash",
            "tokens": {"input": 100, "output": 10, "cached": 0, "thoughts": 5, "tool": 0, "total": 115},
            "toolCalls": [{
                "id": "run_shell_command__commit1",
                "name": "run_shell_command",
                "args": {"command": "git commit -am 'fix bug'"},
                "result": [{
                    "functionResponse": {
                        "id": "run_shell_command__commit1",
                        "name": "run_shell_command",
                        "response": {"output": "<untrusted_context>\nOutput: [main abc1234] fix bug\n</untrusted_context>"},
                    },
                }],
                "status": "success",
            }],
        }),
        # Duplicate of the exact same "gemini" event (same id) — a verified
        # real debounced-write artifact. Must not be double-counted.
        _line({
            "id": "gemini-1",
            "timestamp": "2026-06-29T13:49:05.000Z",
            "type": "gemini",
            "content": "",
            "model": "gemini-3.5-flash",
            "tokens": {"input": 100, "output": 10, "cached": 0, "thoughts": 5, "tool": 0, "total": 115},
            "toolCalls": [{
                "id": "run_shell_command__commit1",
                "name": "run_shell_command",
                "args": {"command": "git commit -am 'fix bug'"},
                "result": [{
                    "functionResponse": {
                        "id": "run_shell_command__commit1",
                        "name": "run_shell_command",
                        "response": {"output": "<untrusted_context>\nOutput: [main abc1234] fix bug\n</untrusted_context>"},
                    },
                }],
                "status": "success",
            }],
        }),
        # Second "gemini" turn, with a shell tool call that opens a PR.
        _line({
            "id": "gemini-2",
            "timestamp": "2026-06-29T13:49:10.000Z",
            "type": "gemini",
            "content": "",
            "model": "gemini-3.5-flash",
            "tokens": {"input": 150, "output": 20, "cached": 50, "thoughts": 8, "tool": 2, "total": 180},
            "toolCalls": [{
                "id": "run_shell_command__pr1",
                "name": "run_shell_command",
                "args": {"command": "gh pr create --title x --body y"},
                "result": [{
                    "functionResponse": {
                        "id": "run_shell_command__pr1",
                        "name": "run_shell_command",
                        "response": {"output": "https://github.com/gromano-ltx/ai_dash/pull/33\n"},
                    },
                }],
                "status": "success",
            }],
        }),
    ]
    return "\n".join(lines) + "\n"


def test_parse_transcript_content_returns_none_for_empty_content():
    assert parse_transcript_content("") is None


def test_parse_transcript_content_sets_provider_gemini():
    run = parse_transcript_content(_sample_session())
    assert run.provider == "gemini"


def test_parse_transcript_content_does_not_set_user():
    # AI-51 finding 1: this adapter's parse_transcript_content() is only ever
    # invoked from backend.api.routes.ingest_transcript (an async route),
    # which always overwrites run.user with api_key.user two lines later —
    # calling the blocking `git config user.name` subprocess here would be
    # pure wasted work with no consumer of its result.
    run = parse_transcript_content(_sample_session())
    assert run.user is None


def test_parse_transcript_content_uses_session_id_as_run_id():
    run = parse_transcript_content(_sample_session())
    assert run.id == "06ba9b64-5701-4a4e-b7eb-4bac2b449d5c"


def test_parse_transcript_content_uses_model_from_gemini_event():
    run = parse_transcript_content(_sample_session())
    assert run.model == "gemini-3.5-flash"


def test_parse_transcript_content_sums_new_input_tokens_excluding_cached():
    run = parse_transcript_content(_sample_session())
    # Turn 1 (deduped): input=100, cached=0 → new=100. Turn 2: input=150,
    # cached=50 → new=100. Total: 200 — NOT 250 (which would include turn 2's
    # cached tokens), and NOT 100+100+150=350 (which would double-count the
    # verified real duplicate-line case for turn 1).
    assert run.input_tokens == 200


def test_parse_transcript_content_captures_cached_input_tokens_in_meta():
    run = parse_transcript_content(_sample_session())
    # Turn 1 cached=0 (deduped despite the duplicate line), turn 2 cached=50.
    assert run.meta["cached_input_tokens"] == 50


def test_parse_transcript_content_sums_output_plus_thoughts_plus_tool():
    run = parse_transcript_content(_sample_session())
    # Turn 1 (deduped): 10+5+0=15. Turn 2: 20+8+2=30. Total: 45.
    assert run.output_tokens == 45


def test_parse_transcript_content_extracts_commit_hash_from_combined_tool_call():
    run = parse_transcript_content(_sample_session())
    assert run.git_commits == ["abc1234"]


def test_parse_transcript_content_extracts_pr_url_from_combined_tool_call():
    run = parse_transcript_content(_sample_session())
    assert run.git_prs == ["https://github.com/gromano-ltx/ai_dash/pull/33"]


def test_parse_transcript_content_extracts_ticket_ref():
    run = parse_transcript_content(_sample_session())
    assert "AI-47" in run.ticket_refs


def test_parse_transcript_content_skips_session_context_for_label():
    run = parse_transcript_content(_sample_session())
    # The first real message (unwrapped from $set) is <session_context>...;
    # the label must come from the real second user message instead.
    assert "session_context" not in run.label
    assert "Fix the AI-47" in run.label


def test_parse_transcript_content_meta_has_no_git_branch_or_cwd():
    run = parse_transcript_content(_sample_session())
    assert run.meta["git_branch"] is None
    assert run.meta["cwd"] is None


def test_parse_transcript_content_passes_through_parent_id():
    run = parse_transcript_content(_sample_session(), parent_id="parent-session-xyz")
    assert run.parent_id == "parent-session-xyz"


def test_parse_transcript_content_status_done_when_mtime_is_old():
    import time
    run = parse_transcript_content(_sample_session(), mtime=time.time() - 3600)
    assert run.status == "done"
    assert run.ended_at is not None


def test_parse_transcript_content_status_running_when_mtime_is_recent():
    import time
    run = parse_transcript_content(_sample_session(), mtime=time.time())
    assert run.status == "running"
    assert run.ended_at is None


def test_parse_transcript_content_ignores_info_events_but_counts_their_timestamp():
    # The "info" event's timestamp (13:49:00.300Z) is earlier than the last
    # "gemini" event (13:49:10.000Z), so it shouldn't change ended_at here —
    # this just confirms parsing an "info" event doesn't crash or corrupt
    # first_user_text/tokens.
    run = parse_transcript_content(_sample_session(), mtime=0.0)
    assert run is not None
    assert "update available" not in run.label
