"""Tests for the export module (HTML and Markdown export)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# --- Fixtures ---


@pytest.fixture
def sample_claude_code_session(tmp_path):
    """Create a sample Claude Code JSONL session file."""
    session_file = tmp_path / "session.jsonl"
    messages = [
        {
            "type": "user",
            "timestamp": "2024-12-30T10:00:00.000Z",
            "message": {"content": "Hello, Claude!"},
        },
        {
            "type": "assistant",
            "timestamp": "2024-12-30T10:00:01.000Z",
            "message": {
                "content": [
                    {"type": "text", "text": "Hello! How can I help you today?"}
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2024-12-30T10:01:00.000Z",
            "message": {"content": "Write a hello world function"},
        },
        {
            "type": "assistant",
            "timestamp": "2024-12-30T10:01:05.000Z",
            "message": {
                "content": [
                    {"type": "text", "text": "I'll create that for you."},
                    {
                        "type": "tool_use",
                        "id": "tool_123",
                        "name": "Write",
                        "input": {
                            "file_path": "/tmp/hello.py",
                            "content": "def hello():\n    print('Hello!')\n",
                        },
                    },
                ]
            },
        },
    ]
    with open(session_file, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return session_file


@pytest.fixture
def sample_opencode_session(tmp_path):
    """Create a sample OpenCode session directory structure."""
    # Create storage structure: message/{session_id}/*.json and part/{msg_id}/*.json
    storage_dir = tmp_path / "storage"
    session_id = "ses_test123"
    msg_dir = storage_dir / "message" / session_id
    part_dir = storage_dir / "part"
    msg_dir.mkdir(parents=True)
    part_dir.mkdir(parents=True)

    # Create message files
    msg1_id = "msg_001"
    msg1 = {
        "id": msg1_id,
        "info": {
            "role": "user",
            "time": {"created": 1704020400000},  # 2024-12-31T10:00:00
        },
        "parts": [{"type": "text", "text": "Hello from OpenCode!", "id": "prt_001"}],
    }
    (msg_dir / f"{msg1_id}.json").write_text(json.dumps(msg1))

    msg2_id = "msg_002"
    msg2 = {
        "id": msg2_id,
        "info": {"role": "assistant", "time": {"created": 1704020401000}},
        "parts": [
            {"type": "text", "text": "Hello! I'm here to help.", "id": "prt_002"}
        ],
    }
    (msg_dir / f"{msg2_id}.json").write_text(json.dumps(msg2))

    return storage_dir, session_id


# --- Tests for detect_session_backend ---


class TestDetectSessionBackend:
    """Tests for backend detection logic."""

    def test_detects_claude_code_jsonl(self, sample_claude_code_session):
        """Should detect Claude Code backend from .jsonl file."""
        from vibedeck.export import detect_session_backend

        backend = detect_session_backend(sample_claude_code_session)
        assert backend == "claude_code"

    def test_detects_opencode_session_id(self, sample_opencode_session, monkeypatch):
        """Should detect OpenCode backend from session ID."""
        from vibedeck.export import detect_session_backend

        storage_dir, session_id = sample_opencode_session
        # Patch the opencode storage path
        monkeypatch.setattr(
            "vibedeck.export.get_opencode_storage_dir",
            lambda: storage_dir,
        )

        backend = detect_session_backend(Path(session_id))
        assert backend == "opencode"

    def test_raises_on_invalid_session(self, tmp_path):
        """Should raise ValueError for invalid session path."""
        from vibedeck.export import detect_session_backend

        with pytest.raises(ValueError, match="Cannot determine session type"):
            detect_session_backend(tmp_path / "nonexistent.txt")


# --- Tests for resolve_session_path (CLI helper) ---


class TestResolveSessionPath:
    """Tests for session path resolution in CLI."""

    def test_resolves_existing_file(self, sample_claude_code_session):
        """Should return Path for existing file."""
        from vibedeck import resolve_session_path

        result = resolve_session_path(str(sample_claude_code_session))
        assert result == sample_claude_code_session

    def test_resolves_opencode_session_id(self, sample_opencode_session, monkeypatch):
        """Should resolve OpenCode session ID to Path."""
        from vibedeck import resolve_session_path

        storage_dir, session_id = sample_opencode_session
        # Patch Path.home() to use our temp dir
        opencode_base = storage_dir.parent  # parent of 'storage'
        monkeypatch.setattr(Path, "home", lambda: opencode_base.parent.parent.parent)

        # Create the expected path structure
        expected_path = (
            Path.home()
            / ".local"
            / "share"
            / "opencode"
            / "storage"
            / "message"
            / session_id
        )
        expected_path.mkdir(parents=True, exist_ok=True)

        result = resolve_session_path(session_id)
        assert result == Path(session_id)

    def test_raises_on_nonexistent_session(self):
        """Should raise BadParameter for nonexistent session."""
        import click
        from vibedeck import resolve_session_path

        with pytest.raises(click.BadParameter, match="Session not found"):
            resolve_session_path("nonexistent_session_xyz")


# --- Tests for HTML export ---


class TestGenerateHtml:
    """Tests for HTML generation."""

    def test_generates_html_files(self, sample_claude_code_session, tmp_path):
        """Should generate index.html and page files."""
        from vibedeck.export import generate_html

        output_dir = tmp_path / "output"
        generate_html(sample_claude_code_session, output_dir)

        assert (output_dir / "index.html").exists()
        assert (output_dir / "page-001.html").exists()

    def test_html_contains_user_messages(self, sample_claude_code_session, tmp_path):
        """Should include user messages in HTML output."""
        from vibedeck.export import generate_html

        output_dir = tmp_path / "output"
        generate_html(sample_claude_code_session, output_dir)

        page_content = (output_dir / "page-001.html").read_text()
        assert "Hello, Claude!" in page_content

    def test_html_contains_assistant_messages(
        self, sample_claude_code_session, tmp_path
    ):
        """Should include assistant messages in HTML output."""
        from vibedeck.export import generate_html

        output_dir = tmp_path / "output"
        generate_html(sample_claude_code_session, output_dir)

        page_content = (output_dir / "page-001.html").read_text()
        assert "How can I help you today?" in page_content

    def test_html_index_contains_prompts(self, sample_claude_code_session, tmp_path):
        """Should list user prompts in index."""
        from vibedeck.export import generate_html

        output_dir = tmp_path / "output"
        generate_html(sample_claude_code_session, output_dir)

        index_content = (output_dir / "index.html").read_text()
        assert "Hello, Claude!" in index_content

    def test_html_title_reflects_claude_code_backend(
        self, sample_claude_code_session, tmp_path
    ):
        """Should show 'Claude Code' in title for Claude Code sessions."""
        from vibedeck.export import generate_html

        output_dir = tmp_path / "output"
        generate_html(sample_claude_code_session, output_dir)

        index_content = (output_dir / "index.html").read_text()
        page_content = (output_dir / "page-001.html").read_text()

        assert "<h1>Claude Code transcript</h1>" in index_content
        assert "Claude Code transcript - Index" in index_content
        assert "Claude Code transcript</a> - page 1/" in page_content

    def test_html_title_reflects_opencode_backend(
        self, sample_opencode_session, tmp_path, monkeypatch
    ):
        """Should show 'OpenCode' in title for OpenCode sessions."""
        from vibedeck.export import generate_html

        storage_dir, session_id = sample_opencode_session
        monkeypatch.setattr(
            "vibedeck.export.get_opencode_storage_dir",
            lambda: storage_dir,
        )

        output_dir = tmp_path / "output"
        generate_html(Path(session_id), output_dir)

        index_content = (output_dir / "index.html").read_text()
        page_content = (output_dir / "page-001.html").read_text()

        assert "<h1>OpenCode transcript</h1>" in index_content
        assert "OpenCode transcript - Index" in index_content
        assert "OpenCode transcript</a> - page 1/" in page_content

    def test_html_pagination_with_many_prompts(self, tmp_path):
        """Should paginate when there are many prompts."""
        from vibedeck.export import generate_html

        # Create session with 12 prompts (should create 3 pages with 5 per page)
        session_file = tmp_path / "large_session.jsonl"
        messages = []
        for i in range(12):
            messages.append(
                {
                    "type": "user",
                    "timestamp": f"2024-12-30T10:{i:02d}:00.000Z",
                    "message": {"content": f"Prompt number {i + 1}"},
                }
            )
            messages.append(
                {
                    "type": "assistant",
                    "timestamp": f"2024-12-30T10:{i:02d}:01.000Z",
                    "message": {
                        "content": [{"type": "text", "text": f"Response {i + 1}"}]
                    },
                }
            )

        with open(session_file, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

        output_dir = tmp_path / "output"
        generate_html(session_file, output_dir)

        assert (output_dir / "page-001.html").exists()
        assert (output_dir / "page-002.html").exists()
        assert (output_dir / "page-003.html").exists()


# --- Tests for Markdown export ---


class TestExportMarkdown:
    """Tests for Markdown export."""

    def test_exports_markdown_to_file(self, sample_claude_code_session, tmp_path):
        """Should export markdown to file."""
        from vibedeck.export import export_markdown

        output_file = tmp_path / "transcript.md"
        result = export_markdown(sample_claude_code_session, output_file)

        assert output_file.exists()
        assert result == str(output_file)

    def test_exports_markdown_to_string(self, sample_claude_code_session):
        """Should return markdown string when no output path."""
        from vibedeck.export import export_markdown

        result = export_markdown(sample_claude_code_session, None)

        assert isinstance(result, str)
        assert "Hello, Claude!" in result

    def test_markdown_contains_prompts(self, sample_claude_code_session):
        """Should include user prompts in markdown."""
        from vibedeck.export import export_markdown

        result = export_markdown(sample_claude_code_session, None)

        assert "## Prompt 1" in result
        assert "Hello, Claude!" in result

    def test_markdown_contains_assistant_responses(self, sample_claude_code_session):
        """Should include assistant responses in markdown."""
        from vibedeck.export import export_markdown

        result = export_markdown(sample_claude_code_session, None)

        assert "**Assistant**" in result
        assert "How can I help you today?" in result

    def test_markdown_contains_tool_usage(self, sample_claude_code_session):
        """Should include tool usage in markdown."""
        from vibedeck.export import export_markdown

        result = export_markdown(sample_claude_code_session, None)

        assert "### Tool: Write" in result
        assert "hello.py" in result


# --- Tests for analyze_conversation ---


class TestAnalyzeConversation:
    """Tests for conversation analysis (stats extraction)."""

    def test_counts_tool_usage(self, sample_claude_code_session):
        """Should count tool usage correctly."""
        from vibedeck.export import (
            parse_session_entries,
            analyze_conversation,
        )

        entries, backend = parse_session_entries(sample_claude_code_session)
        # analyze_conversation takes the raw entries list
        stats = analyze_conversation(entries, backend)

        assert "Write" in stats["tool_counts"]
        assert stats["tool_counts"]["Write"] == 1


# --- Tests for gist preview JS injection ---


class TestGistPreviewJs:
    """Tests for gist preview JavaScript injection."""

    def test_injects_js_into_html(self, sample_claude_code_session, tmp_path):
        """Should inject gist preview JS into HTML files."""
        from vibedeck.export import (
            generate_html,
            inject_gist_preview_js,
        )

        output_dir = tmp_path / "output"
        generate_html(sample_claude_code_session, output_dir)
        inject_gist_preview_js(output_dir)

        index_content = (output_dir / "index.html").read_text()
        assert "gisthost.github.io" in index_content
        assert "rewriteLinks" in index_content

    def test_js_rewrites_relative_links(self, sample_claude_code_session, tmp_path):
        """Should include link rewriting logic in injected JS."""
        from vibedeck.export import (
            generate_html,
            inject_gist_preview_js,
        )

        output_dir = tmp_path / "output"
        generate_html(sample_claude_code_session, output_dir)
        inject_gist_preview_js(output_dir)

        page_content = (output_dir / "page-001.html").read_text()
        assert "setAttribute('href'" in page_content


# --- Tests for extract_text_from_content ---


class TestExtractTextFromContent:
    """Tests for text extraction from message content."""

    def test_extracts_from_string(self):
        """Should extract text from string content."""
        from vibedeck.export import extract_text_from_content

        result = extract_text_from_content("Hello, world!")
        assert result == "Hello, world!"

    def test_extracts_from_list_of_blocks(self):
        """Should extract text from list of content blocks."""
        from vibedeck.export import extract_text_from_content

        content = [
            {"type": "text", "text": "First part."},
            {"type": "image", "data": "..."},
            {"type": "text", "text": "Second part."},
        ]
        result = extract_text_from_content(content)
        assert "First part." in result
        assert "Second part." in result

    def test_returns_empty_for_none(self):
        """Should return empty string for None."""
        from vibedeck.export import extract_text_from_content

        result = extract_text_from_content(None)
        assert result == ""
