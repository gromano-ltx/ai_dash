import pytest
from fastapi import HTTPException

from backend.adapters import claude_code, codex, gemini_cli
from backend.api.routes import _select_parser


def test_select_parser_dispatches_anthropic():
    assert _select_parser("anthropic") is claude_code.parse_transcript_content


def test_select_parser_dispatches_openai():
    assert _select_parser("openai") is codex.parse_transcript_content


def test_select_parser_dispatches_gemini():
    assert _select_parser("gemini") is gemini_cli.parse_transcript_content


def test_select_parser_rejects_unknown_provider():
    # Previously silently fell back to the Claude Code parser, mislabeling
    # any typo'd/case-mismatched/not-yet-supported provider as
    # provider="anthropic" with no error. Must now reject explicitly.
    for bad_provider in ("bogus", "Anthropic", "openAI", "geminiCLI"):
        with pytest.raises(HTTPException) as exc_info:
            _select_parser(bad_provider)
        assert exc_info.value.status_code == 422
