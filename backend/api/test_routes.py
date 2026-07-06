from backend.adapters import claude_code, codex
from backend.api.routes import _select_parser


def test_select_parser_dispatches_anthropic():
    assert _select_parser("anthropic") is claude_code.parse_transcript_content


def test_select_parser_dispatches_openai():
    assert _select_parser("openai") is codex.parse_transcript_content


def test_select_parser_defaults_to_claude_code_for_unknown_provider():
    assert _select_parser("gemini") is claude_code.parse_transcript_content
    assert _select_parser("bogus") is claude_code.parse_transcript_content
