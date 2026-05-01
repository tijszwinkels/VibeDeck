"""Unit tests for ``vibedeck.backends.claude_code.env.scrub_anthropic_env``."""

from __future__ import annotations

import pytest

from vibedeck.backends.claude_code.env import (
    ANTHROPIC_PASSTHROUGH_VARS,
    CLAUDE_CODE_NORMALIZER_KEY,
    scrub_anthropic_env,
)


class _StubBackend:
    """Minimal stand-in for a CodingToolBackend with just the field we read."""

    def __init__(self, normalizer_key: str) -> None:
        self.normalizer_key = normalizer_key


SAMPLE_ENV = {
    "ANTHROPIC_AUTH_TOKEN": "tok",
    "ANTHROPIC_API_KEY": "key",
    "ANTHROPIC_BASE_URL": "https://example.com",
    "PATH": "/usr/bin",
    "FOO": "bar",
}


@pytest.fixture
def with_oauth_creds(tmp_path, monkeypatch):
    """Point CLAUDE_OAUTH_CREDENTIALS_PATH at an existing credentials file."""
    creds = tmp_path / ".credentials.json"
    creds.write_text("{}")
    monkeypatch.setattr(
        "vibedeck.backends.claude_code.env.CLAUDE_OAUTH_CREDENTIALS_PATH",
        creds,
    )
    return creds


@pytest.fixture
def without_oauth_creds(tmp_path, monkeypatch):
    """Point CLAUDE_OAUTH_CREDENTIALS_PATH at a missing file."""
    creds = tmp_path / "nope" / ".credentials.json"
    monkeypatch.setattr(
        "vibedeck.backends.claude_code.env.CLAUDE_OAUTH_CREDENTIALS_PATH",
        creds,
    )
    return creds


def test_returns_independent_copy(without_oauth_creds):
    env_in = dict(SAMPLE_ENV)
    env_out = scrub_anthropic_env(env_in)
    env_out["NEW_KEY"] = "1"
    assert "NEW_KEY" not in env_in


def test_no_creds_keeps_anthropic_vars(without_oauth_creds):
    env_out = scrub_anthropic_env(dict(SAMPLE_ENV))
    for var in ANTHROPIC_PASSTHROUGH_VARS:
        assert var in env_out


def test_no_creds_with_backend_keeps_anthropic_vars(without_oauth_creds):
    env_out = scrub_anthropic_env(
        dict(SAMPLE_ENV),
        backend=_StubBackend(CLAUDE_CODE_NORMALIZER_KEY),
    )
    for var in ANTHROPIC_PASSTHROUGH_VARS:
        assert var in env_out


def test_creds_no_backend_strips(with_oauth_creds):
    env_out = scrub_anthropic_env(dict(SAMPLE_ENV))
    for var in ANTHROPIC_PASSTHROUGH_VARS:
        assert var not in env_out
    # Non-Anthropic vars are preserved.
    assert env_out["PATH"] == "/usr/bin"
    assert env_out["FOO"] == "bar"


def test_creds_claude_code_backend_strips(with_oauth_creds):
    env_out = scrub_anthropic_env(
        dict(SAMPLE_ENV),
        backend=_StubBackend(CLAUDE_CODE_NORMALIZER_KEY),
    )
    for var in ANTHROPIC_PASSTHROUGH_VARS:
        assert var not in env_out


@pytest.mark.parametrize("normalizer_key", ["opencode", "codex", "pi"])
def test_creds_non_claude_backend_keeps(with_oauth_creds, normalizer_key):
    env_in = dict(SAMPLE_ENV)
    env_out = scrub_anthropic_env(env_in, backend=_StubBackend(normalizer_key))
    for var in ANTHROPIC_PASSTHROUGH_VARS:
        assert env_out[var] == env_in[var]


def test_backend_without_normalizer_key_attr_keeps(with_oauth_creds):
    """An object that lacks ``normalizer_key`` is treated as non-Claude."""

    class _Bare:
        pass

    env_in = dict(SAMPLE_ENV)
    env_out = scrub_anthropic_env(env_in, backend=_Bare())
    for var in ANTHROPIC_PASSTHROUGH_VARS:
        assert env_out[var] == env_in[var]


def test_default_env_uses_os_environ(with_oauth_creds, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-dropped")
    monkeypatch.setenv("UNRELATED_VAR_XYZ", "kept")
    out = scrub_anthropic_env()
    assert "ANTHROPIC_API_KEY" not in out
    assert out.get("UNRELATED_VAR_XYZ") == "kept"


def test_input_env_not_mutated(with_oauth_creds):
    env_in = dict(SAMPLE_ENV)
    snapshot = dict(env_in)
    scrub_anthropic_env(env_in, backend=_StubBackend(CLAUDE_CODE_NORMALIZER_KEY))
    assert env_in == snapshot
