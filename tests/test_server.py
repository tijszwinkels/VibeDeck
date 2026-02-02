"""Tests for the FastAPI server."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibedeck import server, sessions, broadcasting
from vibedeck.server import app
from vibedeck.sessions import add_session
from vibedeck.routes.sessions import configure_session_routes


@pytest.fixture
def home_tmp_path():
    """Create a temporary directory within the user's home directory.

    This is needed for file preview tests since the API restricts access
    to files within the home directory only.
    """
    home = Path.home()
    with tempfile.TemporaryDirectory(dir=home, prefix=".test_") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(autouse=True)
def reset_server_state():
    """Reset server state before each test."""
    sessions.get_sessions().clear()
    broadcasting.get_clients().clear()
    sessions.get_known_session_files().clear()
    server.set_send_enabled(False)  # Reset send feature state
    server.set_default_send_backend(None)  # Reset default backend
    # Configure session routes with server dependencies
    configure_session_routes(
        get_server_backend=server.get_server_backend,
        get_backend_for_session=server.get_backend_for_session,
        is_send_enabled=server.is_send_enabled,
        is_fork_enabled=server.is_fork_enabled,
        is_skip_permissions=server.is_skip_permissions,
        get_default_send_backend=server.get_default_send_backend,
        get_allowed_directories=server.get_allowed_directories,
        add_allowed_directory=server.add_allowed_directory,
        run_cli_for_session=server.run_cli_for_session,
        broadcast_session_status=server._broadcast_session_status,
        summarize_session_async=server._summarize_session_async,
        get_summarizer=server.get_summarizer,
        get_idle_summary_model=server.get_idle_summary_model,
        cached_models=server._cached_models,
    )
    yield
    sessions.get_sessions().clear()
    broadcasting.get_clients().clear()
    sessions.get_known_session_files().clear()
    server.set_send_enabled(False)
    server.set_default_send_backend(None)


class TestServerEndpoints:
    """Tests for server endpoints."""

    def test_index_returns_html(self, temp_jsonl_file):
        """Test that index returns HTML page."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "VibeDeck" in response.text

    def test_index_includes_css(self, temp_jsonl_file):
        """Test that index includes CSS."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/")
        assert ":root" in response.text
        assert "--bg-color" in response.text

    def test_index_includes_sse_script(self, temp_jsonl_file):
        """Test that index includes JS module script tag."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/")
        # JS is now loaded as an ES module
        assert 'type="module"' in response.text
        assert 'src="/static/js/app.js"' in response.text

    def test_index_includes_sidebar(self, temp_jsonl_file):
        """Test that index includes sidebar elements."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/")
        assert "sidebar" in response.text
        assert "project-list" in response.text

    def test_health_check(self, temp_jsonl_file):
        """Test health check endpoint."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "sessions" in data
        assert "clients" in data

    def test_sessions_endpoint(self, temp_jsonl_file):
        """Test sessions list endpoint."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert len(data["sessions"]) == 1


class TestSessionManagement:
    """Tests for session management functions."""

    def test_add_session(self, temp_jsonl_file):
        """Test adding a session."""
        info, evicted_id = add_session(temp_jsonl_file)

        assert info is not None
        assert evicted_id is None
        assert info.session_id == temp_jsonl_file.stem
        assert info.path == temp_jsonl_file

    def test_add_duplicate_session(self, temp_jsonl_file):
        """Test that adding duplicate session returns None."""
        info1, _ = add_session(temp_jsonl_file)
        info2, evicted_id = add_session(temp_jsonl_file)

        assert info1 is not None
        assert info2 is None
        assert evicted_id is None

    def test_add_empty_session_skipped(self, tmp_path):
        """Test that empty session files are skipped."""
        empty_file = tmp_path / "empty.jsonl"
        empty_file.write_text("")  # 0 bytes

        info, evicted_id = add_session(empty_file)

        assert info is None
        assert evicted_id is None
        assert "empty" not in sessions.get_sessions()

    def test_session_limit_with_eviction(self, tmp_path):
        """Test that session limit evicts oldest sessions."""
        import time

        # Create more sessions than the limit, with slight time delays
        for i in range(sessions.MAX_SESSIONS + 2):
            session_file = tmp_path / f"session_{i}.jsonl"
            session_file.write_text('{"type": "user"}\n')
            add_session(session_file)
            time.sleep(0.01)  # Ensure different mtime

        # Should still have MAX_SESSIONS (oldest got evicted)
        assert len(sessions.get_sessions()) == sessions.MAX_SESSIONS
        # First session should have been evicted
        assert "session_0" not in sessions.get_sessions()

    def test_session_limit_without_eviction(self, tmp_path):
        """Test that session limit is respected when eviction is disabled."""
        # Create more sessions than the limit without eviction
        for i in range(sessions.MAX_SESSIONS + 2):
            session_file = tmp_path / f"session_{i}.jsonl"
            session_file.write_text('{"type": "user"}\n')
            add_session(session_file, evict_oldest=False)

        # Should stop at MAX_SESSIONS
        assert len(sessions.get_sessions()) == sessions.MAX_SESSIONS

    def test_remove_session(self, temp_jsonl_file):
        """Test removing a session."""
        info, _ = add_session(temp_jsonl_file)
        session_id = info.session_id

        assert sessions.remove_session(session_id) is True
        assert session_id not in sessions.get_sessions()

    def test_remove_nonexistent_session(self):
        """Test removing a session that doesn't exist."""
        assert sessions.remove_session("nonexistent") is False

    def test_get_sessions_list(self, temp_jsonl_file):
        """Test getting the sessions list."""
        add_session(temp_jsonl_file)
        sessions_list = sessions.get_sessions_list()

        assert len(sessions_list) == 1
        assert sessions_list[0]["id"] == temp_jsonl_file.stem


class TestSendFeature:
    """Tests for the send message feature."""

    def test_send_enabled_endpoint_disabled(self):
        """Test /send-enabled returns false when disabled."""
        client = TestClient(app)
        response = client.get("/send-enabled")
        assert response.status_code == 200
        assert response.json() == {"enabled": False}

    def test_send_enabled_endpoint_enabled(self):
        """Test /send-enabled returns true when enabled."""
        server.set_send_enabled(True)
        client = TestClient(app)
        response = client.get("/send-enabled")
        assert response.status_code == 200
        assert response.json() == {"enabled": True}

    def test_send_returns_403_when_disabled(self, temp_jsonl_file):
        """Test that send endpoint returns 403 when feature is disabled."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.post(
            f"/sessions/{temp_jsonl_file.stem}/send", json={"message": "test message"}
        )
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"].lower()

    def test_send_returns_404_for_unknown_session(self, temp_jsonl_file):
        """Test that send returns 404 for unknown session."""
        server.set_send_enabled(True)
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.post(
            "/sessions/nonexistent/send", json={"message": "test message"}
        )
        assert response.status_code == 404

    def test_send_returns_400_for_empty_message(self, temp_jsonl_file):
        """Test that send returns 400 for empty message."""
        server.set_send_enabled(True)
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.post(
            f"/sessions/{temp_jsonl_file.stem}/send", json={"message": "   "}
        )
        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()

    def test_session_status_endpoint(self, temp_jsonl_file):
        """Test session status endpoint."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.get(f"/sessions/{temp_jsonl_file.stem}/status")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == temp_jsonl_file.stem
        assert data["running"] is False
        assert data["queued_messages"] == 0

    def test_session_status_404_for_unknown(self):
        """Test session status returns 404 for unknown session."""
        client = TestClient(app)
        response = client.get("/sessions/nonexistent/status")
        assert response.status_code == 404

    def test_interrupt_returns_403_when_disabled(self, temp_jsonl_file):
        """Test that interrupt returns 403 when feature is disabled."""
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.post(f"/sessions/{temp_jsonl_file.stem}/interrupt")
        assert response.status_code == 403

    def test_interrupt_returns_404_for_unknown_session(self):
        """Test that interrupt returns 404 for unknown session."""
        server.set_send_enabled(True)
        client = TestClient(app)

        response = client.post("/sessions/nonexistent/interrupt")
        assert response.status_code == 404

    def test_interrupt_returns_409_when_not_running(self, temp_jsonl_file):
        """Test that interrupt returns 409 when no process is running."""
        server.set_send_enabled(True)
        add_session(temp_jsonl_file)
        client = TestClient(app)

        response = client.post(f"/sessions/{temp_jsonl_file.stem}/interrupt")
        assert response.status_code == 409
        assert "no process running" in response.json()["detail"].lower()

    def test_new_session_returns_403_when_disabled(self):
        """Test that new session returns 403 when feature is disabled."""
        client = TestClient(app)

        response = client.post("/sessions/new", json={"message": "Hello"})
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"].lower()

    def test_new_session_returns_400_for_empty_message(self):
        """Test that new session returns 400 for empty message."""
        server.set_send_enabled(True)
        client = TestClient(app)

        response = client.post("/sessions/new", json={"message": "   "})
        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()

    def test_new_session_returns_400_for_invalid_model_index(self):
        """Test that new session validates model_index against cached models.

        Note: This test only validates when using a backend that supports models.
        For backends without model support (like Claude Code), model_index is ignored.
        """
        server.set_send_enabled(True)
        client = TestClient(app)

        # Get backends to find one that supports models
        backends_resp = client.get("/backends")
        backends = backends_resp.json()["backends"]
        model_backend = next(
            (b for b in backends if b.get("supports_models")), None
        )

        if model_backend is None:
            # No backend supports models, nothing to test
            pytest.skip("No backend supports model selection")

        # Model index without fetching models first (cache is empty)
        response = client.post(
            "/sessions/new",
            json={"message": "test", "backend": model_backend["name"], "model_index": 999},
        )
        assert response.status_code == 400
        assert "invalid model_index" in response.json()["detail"].lower()


class TestDefaultSendBackend:
    """Tests for the default send backend feature."""

    def test_default_send_backend_endpoint_returns_null(self):
        """Test /default-send-backend returns null when not set."""
        client = TestClient(app)
        response = client.get("/default-send-backend")
        assert response.status_code == 200
        assert response.json() == {"backend": None}

    def test_default_send_backend_endpoint_returns_value(self):
        """Test /default-send-backend returns value when set."""
        server.set_default_send_backend("opencode")
        try:
            client = TestClient(app)
            response = client.get("/default-send-backend")
            assert response.status_code == 200
            assert response.json() == {"backend": "opencode"}
        finally:
            # Reset for other tests
            server._default_send_backend = None

    def test_get_default_send_backend_returns_none_initially(self):
        """Test get_default_send_backend returns None when not set."""
        assert server.get_default_send_backend() is None

    def test_set_and_get_default_send_backend(self):
        """Test set and get default send backend."""
        server.set_default_send_backend("claude-code")
        try:
            assert server.get_default_send_backend() == "claude-code"
        finally:
            server._default_send_backend = None


class TestBackendsEndpoint:
    """Tests for the backends listing endpoint."""

    def test_backends_endpoint_returns_list(self):
        """Test /backends returns a list of backends."""
        client = TestClient(app)
        response = client.get("/backends")
        assert response.status_code == 200
        data = response.json()
        assert "backends" in data
        assert isinstance(data["backends"], list)
        # Should have at least one backend (the default)
        assert len(data["backends"]) >= 1

    def test_backends_endpoint_includes_required_fields(self):
        """Test each backend has required fields."""
        client = TestClient(app)
        response = client.get("/backends")
        assert response.status_code == 200
        data = response.json()

        for backend in data["backends"]:
            assert "name" in backend
            assert "cli_available" in backend
            assert "supports_models" in backend

    def test_backend_models_endpoint_404_for_unknown(self):
        """Test /backends/{name}/models returns 404 for unknown backend."""
        client = TestClient(app)
        response = client.get("/backends/nonexistent/models")
        assert response.status_code == 404

    def test_backend_models_endpoint_returns_list(self):
        """Test /backends/{name}/models returns a list."""
        client = TestClient(app)

        # First get the backends to find one that exists
        backends_response = client.get("/backends")
        backends = backends_response.json()["backends"]
        if not backends:
            pytest.skip("No backends available")

        backend_name = backends[0]["name"]
        response = client.get(f"/backends/{backend_name}/models")
        assert response.status_code == 200
        data = response.json()
        assert "models" in data
        assert isinstance(data["models"], list)


class TestStaticJsEndpoint:
    """Tests for the static JS file serving endpoint."""

    def test_serve_js_app_module(self):
        """Test serving the main app.js module."""
        client = TestClient(app)
        response = client.get("/static/js/app.js")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/javascript"
        assert "import" in response.text  # ES module syntax

    def test_serve_js_state_module(self):
        """Test serving the state.js module."""
        client = TestClient(app)
        response = client.get("/static/js/state.js")
        assert response.status_code == 200
        assert "export" in response.text  # ES module syntax

    def test_serve_js_utils_module(self):
        """Test serving the utils.js module."""
        client = TestClient(app)
        response = client.get("/static/js/utils.js")
        assert response.status_code == 200
        assert "export" in response.text

    def test_serve_js_not_found(self):
        """Test 404 for non-existent JS file."""
        client = TestClient(app)
        response = client.get("/static/js/nonexistent.js")
        assert response.status_code == 404

    def test_serve_js_path_traversal_blocked(self):
        """Test that path traversal is blocked."""
        client = TestClient(app)
        response = client.get("/static/js/../../../etc/passwd.js")
        assert response.status_code == 404

    def test_serve_js_non_js_extension_blocked(self):
        """Test that non-.js files are blocked."""
        client = TestClient(app)
        response = client.get("/static/js/app.py")
        assert response.status_code == 404


class TestFilePreviewAPI:
    """Tests for the file preview API endpoint."""

    def test_get_file_success(self, home_tmp_path):
        """Test successful file fetch."""
        test_file = home_tmp_path / "test.py"
        test_file.write_text("print('hello')")

        client = TestClient(app)
        response = client.get(f"/api/file?path={test_file}")

        assert response.status_code == 200
        data = response.json()
        assert data["content"] == "print('hello')"
        assert data["filename"] == "test.py"
        assert data["language"] == "python"
        assert data["truncated"] is False
        assert data["size"] == 14  # len("print('hello')")

    def test_get_file_not_found(self, home_tmp_path):
        """Test 404 for missing file in home directory."""
        client = TestClient(app)
        # Use a path within home directory that doesn't exist
        response = client.get(f"/api/file?path={home_tmp_path}/nonexistent.py")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_file_directory_rejected(self, home_tmp_path):
        """Test that directories are rejected."""
        client = TestClient(app)
        response = client.get(f"/api/file?path={home_tmp_path}")

        assert response.status_code == 400
        assert "not a file" in response.json()["detail"].lower()

    def test_get_file_binary_rejected(self, home_tmp_path):
        """Test binary file rejection."""
        binary_file = home_tmp_path / "image.png"
        binary_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00")

        client = TestClient(app)
        response = client.get(f"/api/file?path={binary_file}")

        assert response.status_code == 400
        assert "binary" in response.json()["detail"].lower()

    def test_get_file_truncation(self, home_tmp_path):
        """Test large file truncation."""
        large_file = home_tmp_path / "large.txt"
        # Write slightly more than 1MB
        large_file.write_text("x" * (1024 * 1024 + 1000))

        client = TestClient(app)
        response = client.get(f"/api/file?path={large_file}")

        assert response.status_code == 200
        data = response.json()
        assert data["truncated"] is True
        assert len(data["content"]) == 1024 * 1024

    def test_get_file_language_detection(self, home_tmp_path):
        """Test language detection from extensions."""
        test_cases = [
            (".py", "python"),
            (".js", "javascript"),
            (".ts", "typescript"),
            (".rs", "rust"),
            (".go", "go"),
            (".json", "json"),
            (".md", "markdown"),
            (".yaml", "yaml"),
        ]

        client = TestClient(app)
        for ext, expected_lang in test_cases:
            test_file = home_tmp_path / f"test{ext}"
            test_file.write_text("// code")

            response = client.get(f"/api/file?path={test_file}")
            assert response.status_code == 200
            assert response.json()["language"] == expected_lang, f"Failed for {ext}"

    def test_get_file_unknown_extension(self, home_tmp_path):
        """Test unknown extension returns null language."""
        test_file = home_tmp_path / "test.xyz"
        test_file.write_text("some content")

        client = TestClient(app)
        response = client.get(f"/api/file?path={test_file}")

        assert response.status_code == 200
        assert response.json()["language"] is None

    def test_get_file_makefile(self, home_tmp_path):
        """Test Makefile detection without extension."""
        makefile = home_tmp_path / "Makefile"
        makefile.write_text("all:\n\techo hello")

        client = TestClient(app)
        response = client.get(f"/api/file?path={makefile}")

        assert response.status_code == 200
        assert response.json()["language"] == "makefile"

    def test_get_file_dockerfile(self, home_tmp_path):
        """Test Dockerfile detection without extension."""
        dockerfile = home_tmp_path / "Dockerfile"
        dockerfile.write_text("FROM python:3.11")

        client = TestClient(app)
        response = client.get(f"/api/file?path={dockerfile}")

        assert response.status_code == 200
        assert response.json()["language"] == "dockerfile"

    def test_get_file_absolute_path_returned(self, home_tmp_path):
        """Test that absolute path is returned."""
        test_file = home_tmp_path / "test.txt"
        test_file.write_text("content")

        client = TestClient(app)
        response = client.get(f"/api/file?path={test_file}")

        assert response.status_code == 200
        # Path should be absolute
        assert response.json()["path"].startswith("/")

    def test_get_file_markdown_rendering(self, home_tmp_path):
        """Test markdown files return rendered HTML."""
        md_file = home_tmp_path / "test.md"
        md_content = """# Hello World

This is a **bold** paragraph.

| Column A | Column B |
|----------|----------|
| Value 1  | Value 2  |
"""
        md_file.write_text(md_content)

        client = TestClient(app)
        response = client.get(f"/api/file?path={md_file}")

        assert response.status_code == 200
        data = response.json()
        assert data["language"] == "markdown"
        assert data["content"] == md_content  # Raw content still returned
        assert data["rendered_html"] is not None
        # Check rendered HTML contains expected elements
        assert "<h1>" in data["rendered_html"]
        assert "<strong>bold</strong>" in data["rendered_html"]
        assert "<table>" in data["rendered_html"]
        assert "<th>" in data["rendered_html"]

    def test_get_file_non_markdown_no_rendered_html(self, home_tmp_path):
        """Test non-markdown files don't have rendered_html."""
        py_file = home_tmp_path / "test.py"
        py_file.write_text("print('hello')")

        client = TestClient(app)
        response = client.get(f"/api/file?path={py_file}")

        assert response.status_code == 200
        data = response.json()
        assert data["language"] == "python"
        assert data["rendered_html"] is None

    def test_get_file_path_traversal_blocked(self):
        """Test that path traversal outside home directory is blocked."""
        client = TestClient(app)

        # Try to access system files outside home directory
        response = client.get("/api/file?path=/etc/passwd")
        assert response.status_code == 403
        assert "allowed directories" in response.json()["detail"]

        # Try with path traversal
        response = client.get("/api/file?path=/home/../etc/passwd")
        assert response.status_code == 403
        assert "allowed directories" in response.json()["detail"]

    def test_get_file_markdown_html_escaped(self, home_tmp_path):
        """Test that dangerous HTML in markdown is sanitized to prevent XSS."""
        md_file = home_tmp_path / "xss.md"
        # Try to inject XSS via raw HTML in markdown
        md_content = """# Test

<script>alert('xss')</script>

<img src=x onerror="alert('xss')">

Normal **bold** text.
"""
        md_file.write_text(md_content)

        client = TestClient(app)
        response = client.get(f"/api/file?path={md_file}")

        assert response.status_code == 200
        data = response.json()
        rendered = data["rendered_html"]

        # Dangerous tags like <script> are removed entirely by nh3
        assert "<script>" not in rendered
        assert "alert('xss')" not in rendered

        # <img> is allowed but dangerous attributes like onerror are stripped
        assert 'onerror="' not in rendered
        # The img tag itself may be present but without dangerous attributes
        if "<img" in rendered:
            assert 'onerror' not in rendered

        # Normal markdown should still work
        assert "<strong>bold</strong>" in rendered


class TestFileRawAPI:
    """Tests for the /api/file/raw endpoint (for serving images)."""

    def test_get_image_png(self, home_tmp_path):
        """Test serving a PNG image."""
        png_file = home_tmp_path / "test.png"
        # Create a minimal valid PNG (1x1 pixel, red)
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
            b"\x00\x00\x00\x03\x00\x01\x00\x05\xfe\xd4\xef\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        png_file.write_bytes(png_data)

        client = TestClient(app)
        response = client.get(f"/api/file/raw?path={png_file}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == png_data

    def test_get_image_jpeg(self, home_tmp_path):
        """Test serving a JPEG image."""
        jpg_file = home_tmp_path / "test.jpg"
        # Create a minimal valid JPEG
        jpg_data = bytes(
            [0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00]
            + [0] * 10
            + [0xFF, 0xD9]
        )
        jpg_file.write_bytes(jpg_data)

        client = TestClient(app)
        response = client.get(f"/api/file/raw?path={jpg_file}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"

    def test_get_image_svg(self, home_tmp_path):
        """Test serving an SVG image."""
        svg_file = home_tmp_path / "test.svg"
        svg_content = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
        svg_file.write_bytes(svg_content)

        client = TestClient(app)
        response = client.get(f"/api/file/raw?path={svg_file}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/svg+xml"
        assert response.content == svg_content

    def test_get_raw_file_not_found(self, home_tmp_path):
        """Test 404 for missing file."""
        client = TestClient(app)
        response = client.get(f"/api/file/raw?path={home_tmp_path}/nonexistent.png")

        assert response.status_code == 404

    def test_get_raw_file_directory_rejected(self, home_tmp_path):
        """Test that directories are rejected."""
        client = TestClient(app)
        response = client.get(f"/api/file/raw?path={home_tmp_path}")

        assert response.status_code == 400

    def test_get_raw_file_outside_home_rejected(self):
        """Test that files outside home directory are rejected."""
        client = TestClient(app)
        response = client.get("/api/file/raw?path=/etc/passwd")

        assert response.status_code == 403
        assert "allowed directories" in response.json()["detail"]

    def test_get_raw_file_unknown_extension(self, home_tmp_path):
        """Test unknown extension returns octet-stream."""
        unknown_file = home_tmp_path / "test.xyz"
        unknown_file.write_bytes(b"some binary data")

        client = TestClient(app)
        response = client.get(f"/api/file/raw?path={unknown_file}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"

    def test_get_raw_file_has_cache_header(self, home_tmp_path):
        """Test that response includes cache header."""
        png_file = home_tmp_path / "test.png"
        png_file.write_bytes(b"\x89PNG\r\n\x1a\n")

        client = TestClient(app)
        response = client.get(f"/api/file/raw?path={png_file}")

        assert response.status_code == 200
        assert "cache-control" in response.headers
        assert "max-age" in response.headers["cache-control"]


class TestPathTypeAPI:
    """Tests for the path type check API endpoint."""

    def test_file_returns_type_file(self, home_tmp_path):
        """Test checking an existing file returns type 'file'."""
        test_file = home_tmp_path / "test.py"
        test_file.write_text("print('hello')")

        client = TestClient(app)
        response = client.get(f"/api/path/type?path={test_file}")

        assert response.status_code == 200
        assert response.json()["type"] == "file"

    def test_directory_returns_type_directory(self, home_tmp_path):
        """Test checking a directory returns type 'directory'."""
        client = TestClient(app)
        response = client.get(f"/api/path/type?path={home_tmp_path}")

        assert response.status_code == 200
        assert response.json()["type"] == "directory"

    def test_not_exists_returns_404(self, home_tmp_path):
        """Test checking a non-existent path returns 404."""
        client = TestClient(app)
        response = client.get(f"/api/path/type?path={home_tmp_path}/nonexistent.py")

        assert response.status_code == 404

    def test_outside_home_returns_404(self):
        """Test that paths outside home directory return 404."""
        client = TestClient(app)
        response = client.get("/api/path/type?path=/etc/passwd")

        assert response.status_code == 404

    def test_tilde_expansion(self, home_tmp_path):
        """Test tilde expansion for home directory."""
        from pathlib import Path

        home = Path.home()
        test_file = home_tmp_path / "tilde_test.txt"
        test_file.write_text("test")

        # Get the path relative to home
        relative_path = test_file.relative_to(home)

        client = TestClient(app)
        response = client.get(f"/api/path/type?path=~/{relative_path}")

        assert response.status_code == 200
        assert response.json()["type"] == "file"


class TestFileWatchAPI:
    """Tests for the file watch SSE endpoint."""

    def test_file_watch_not_found(self, home_tmp_path):
        """Test 404 for non-existent file."""
        client = TestClient(app)
        response = client.get(f"/api/file/watch?path={home_tmp_path}/nonexistent.py")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_file_watch_directory_rejected(self, home_tmp_path):
        """Test 400 for directories."""
        client = TestClient(app)
        response = client.get(f"/api/file/watch?path={home_tmp_path}")
        assert response.status_code == 400
        assert "not a file" in response.json()["detail"].lower()

    def test_file_watch_path_traversal_blocked(self):
        """Test 403 for paths outside home directory."""
        client = TestClient(app)
        response = client.get("/api/file/watch?path=/etc/passwd")
        assert response.status_code == 403
        assert "allowed directories" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_file_watch_initial_content(self, home_tmp_path):
        """Test that initial content is sent on connect."""
        import json
        from unittest.mock import MagicMock

        from vibedeck.routes.files import _file_watch_generator

        test_file = home_tmp_path / "test.txt"
        test_file.write_text("initial content")

        # Create a mock request with async is_disconnected method
        mock_request = MagicMock()

        async def mock_is_disconnected():
            return False

        mock_request.is_disconnected = mock_is_disconnected

        # Get the first event from the generator
        generator = _file_watch_generator(test_file, mock_request)
        event = await generator.__anext__()

        assert event["event"] == "initial"
        data = json.loads(event["data"])
        assert data["content"] == "initial content"
        assert data["size"] == 15
        assert data["truncated"] is False

        # Clean up generator
        await generator.aclose()

    @pytest.mark.asyncio
    async def test_file_watch_binary_rejected(self, home_tmp_path):
        """Test binary file returns error event."""
        import json
        from unittest.mock import MagicMock

        from vibedeck.routes.files import _file_watch_generator

        binary_file = home_tmp_path / "image.png"
        binary_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00")

        # Create a mock request
        mock_request = MagicMock()

        # Get the first event from the generator
        generator = _file_watch_generator(binary_file, mock_request)
        event = await generator.__anext__()

        assert event["event"] == "error"
        data = json.loads(event["data"])
        assert "binary" in data["message"].lower()

        # Clean up generator
        await generator.aclose()

    @pytest.mark.asyncio
    async def test_file_watch_append_detection(self, home_tmp_path):
        """Test append detection - file grows, only new bytes sent."""
        import json
        from unittest.mock import MagicMock, patch

        from vibedeck.routes.files import _file_watch_generator

        test_file = home_tmp_path / "append_test.txt"
        test_file.write_text("initial")

        mock_request = MagicMock()
        disconnect_after = 2
        call_count = 0

        async def mock_is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > disconnect_after

        mock_request.is_disconnected = mock_is_disconnected

        # Mock watchfiles.awatch to control when "changes" are detected
        async def mock_awatch(*args, **kwargs):
            # First, append to the file
            with open(test_file, "a") as f:
                f.write(" appended")
            # Yield a fake change event (content doesn't matter, generator re-stats the file)
            yield {("modified", str(test_file))}

        with patch("vibedeck.server.watchfiles.awatch", mock_awatch):
            generator = _file_watch_generator(test_file, mock_request, follow=True)

            # Get initial event
            event = await generator.__anext__()
            assert event["event"] == "initial"
            initial_data = json.loads(event["data"])
            assert initial_data["content"] == "initial"
            initial_size = initial_data["size"]

            # Get append event
            event = await generator.__anext__()
            assert event["event"] == "append"
            data = json.loads(event["data"])
            assert data["content"] == " appended"
            assert data["offset"] == initial_size

            await generator.aclose()

    @pytest.mark.asyncio
    async def test_file_watch_truncation_detection(self, home_tmp_path):
        """Test truncation detection - file shrinks, full content sent."""
        import json
        from unittest.mock import MagicMock, patch

        from vibedeck.routes.files import _file_watch_generator

        test_file = home_tmp_path / "truncate_test.txt"
        test_file.write_text("long initial content here")

        mock_request = MagicMock()
        call_count = 0

        async def mock_is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > 2

        mock_request.is_disconnected = mock_is_disconnected

        # Mock watchfiles.awatch to control when "changes" are detected
        async def mock_awatch(*args, **kwargs):
            # Truncate file (write shorter content)
            test_file.write_text("short")
            yield {("modified", str(test_file))}

        with patch("vibedeck.server.watchfiles.awatch", mock_awatch):
            generator = _file_watch_generator(test_file, mock_request, follow=True)

            # Get initial event
            event = await generator.__anext__()
            assert event["event"] == "initial"

            # Get replace event (truncation triggers replace, not append)
            event = await generator.__anext__()
            assert event["event"] == "replace"
            data = json.loads(event["data"])
            assert data["content"] == "short"
            assert data["size"] == 5

            await generator.aclose()

    @pytest.mark.asyncio
    async def test_file_watch_inode_change(self, home_tmp_path):
        """Test inode change detection - file replaced, full content sent."""
        import json
        import os
        from unittest.mock import MagicMock, patch

        from vibedeck.routes.files import _file_watch_generator

        test_file = home_tmp_path / "inode_test.txt"
        test_file.write_text("original content")

        mock_request = MagicMock()
        call_count = 0

        async def mock_is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > 2

        mock_request.is_disconnected = mock_is_disconnected

        # Mock watchfiles.awatch to control when "changes" are detected
        async def mock_awatch(*args, **kwargs):
            # Replace file (creates new inode on most filesystems)
            temp_file = home_tmp_path / "inode_test.txt.tmp"
            temp_file.write_text("replaced content")
            os.replace(temp_file, test_file)
            yield {("modified", str(test_file))}

        with patch("vibedeck.server.watchfiles.awatch", mock_awatch):
            generator = _file_watch_generator(test_file, mock_request, follow=True)

            # Get initial event
            event = await generator.__anext__()
            assert event["event"] == "initial"
            initial_data = json.loads(event["data"])
            initial_inode = initial_data["inode"]

            # Get replace event (inode change triggers replace)
            event = await generator.__anext__()
            assert event["event"] == "replace"
            data = json.loads(event["data"])
            assert data["content"] == "replaced content"
            # Verify inode changed
            assert data["inode"] != initial_inode

            await generator.aclose()

    @pytest.mark.asyncio
    async def test_file_watch_file_deleted(self, home_tmp_path):
        """Test file deletion - error event sent."""
        import json
        from unittest.mock import MagicMock, patch

        from vibedeck.routes.files import _file_watch_generator

        test_file = home_tmp_path / "delete_test.txt"
        test_file.write_text("will be deleted")

        mock_request = MagicMock()
        call_count = 0

        async def mock_is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > 2

        mock_request.is_disconnected = mock_is_disconnected

        # Mock watchfiles.awatch to control when "changes" are detected
        async def mock_awatch(*args, **kwargs):
            # Delete file before yielding change event
            test_file.unlink()
            yield {("deleted", str(test_file))}

        with patch("vibedeck.server.watchfiles.awatch", mock_awatch):
            generator = _file_watch_generator(test_file, mock_request, follow=True)

            # Get initial event
            event = await generator.__anext__()
            assert event["event"] == "initial"

            # Get error event (file deleted triggers error)
            event = await generator.__anext__()
            assert event["event"] == "error"
            data = json.loads(event["data"])
            # Error message can be "deleted" or "not found" depending on how file system reports it
            assert "deleted" in data["message"].lower() or "not found" in data["message"].lower()

            await generator.aclose()

    @pytest.mark.asyncio
    async def test_file_watch_client_disconnect(self, home_tmp_path):
        """Test that generator exits cleanly on client disconnect."""
        import json
        from unittest.mock import MagicMock

        from vibedeck.routes.files import _file_watch_generator

        test_file = home_tmp_path / "disconnect_test.txt"
        test_file.write_text("test content")

        mock_request = MagicMock()

        # Simulate immediate disconnect after initial content
        async def mock_is_disconnected():
            return True

        mock_request.is_disconnected = mock_is_disconnected

        generator = _file_watch_generator(test_file, mock_request, follow=True)

        # Get initial event
        event = await generator.__anext__()
        assert event["event"] == "initial"
        data = json.loads(event["data"])
        assert data["content"] == "test content"

        # Generator should stop after disconnect check
        # The watchfiles loop should exit due to disconnect
        await generator.aclose()
        # If we get here without hanging, the test passes

    @pytest.mark.asyncio
    async def test_file_watch_follow_false_sends_changed_event(self, home_tmp_path):
        """Test that follow=false sends 'changed' event instead of content."""
        import json
        from unittest.mock import MagicMock, patch

        from vibedeck.routes.files import _file_watch_generator

        test_file = home_tmp_path / "follow_false_test.txt"
        test_file.write_text("initial")

        mock_request = MagicMock()
        call_count = 0

        async def mock_is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > 2

        mock_request.is_disconnected = mock_is_disconnected

        # Mock watchfiles.awatch to control when "changes" are detected
        async def mock_awatch(*args, **kwargs):
            # Append to file
            with open(test_file, "a") as f:
                f.write(" more")
            yield {("modified", str(test_file))}

        with patch("vibedeck.server.watchfiles.awatch", mock_awatch):
            # Note: follow=False
            generator = _file_watch_generator(test_file, mock_request, follow=False)

            # Get initial event (still sends full content on connect)
            event = await generator.__anext__()
            assert event["event"] == "initial"

            # Should get 'changed' event, not 'append' (because follow=False)
            event = await generator.__anext__()
            assert event["event"] == "changed"
            data = json.loads(event["data"])
            # Changed event should have size and inode but not content
            assert "size" in data
            assert "inode" in data
            assert "content" not in data

            await generator.aclose()


class TestSessionTreeAPI:
    """Tests for the session file tree API endpoint."""

    def test_tree_returns_404_for_unknown_session(self):
        """Test tree endpoint returns 404 for unknown session."""
        client = TestClient(app)
        response = client.get("/sessions/nonexistent/tree")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_tree_returns_error_when_no_project_path(self, temp_jsonl_file):
        """Test tree returns error when session has no project path."""
        info, _ = add_session(temp_jsonl_file)
        # Explicitly clear project path to test this case
        info.project_path = None
        client = TestClient(app)

        response = client.get(f"/sessions/{temp_jsonl_file.stem}/tree")
        assert response.status_code == 200
        data = response.json()
        assert data["tree"] is None
        assert "no project path" in data["error"].lower()

    def test_tree_returns_directory_listing(self, home_tmp_path, temp_jsonl_file):
        """Test tree returns directory listing for valid session with project path."""
        # Create a session with a project path
        info, _ = add_session(temp_jsonl_file)
        info.project_path = str(home_tmp_path)

        # Create some test files and directories
        (home_tmp_path / "file1.py").write_text("# test")
        (home_tmp_path / "file2.js").write_text("// test")
        subdir = home_tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("nested")

        client = TestClient(app)
        response = client.get(f"/sessions/{temp_jsonl_file.stem}/tree")

        assert response.status_code == 200
        data = response.json()
        assert data["tree"] is not None
        assert "children" in data["tree"]

        # Check that files are listed
        names = [child["name"] for child in data["tree"]["children"]]
        assert "file1.py" in names
        assert "file2.js" in names
        assert "subdir" in names

    def test_tree_with_explicit_path(self, home_tmp_path, temp_jsonl_file):
        """Test tree with explicit path parameter."""
        info, _ = add_session(temp_jsonl_file)
        info.project_path = str(home_tmp_path)

        # Create a subdirectory with files
        subdir = home_tmp_path / "mysubdir"
        subdir.mkdir()
        (subdir / "inner.txt").write_text("inner content")

        client = TestClient(app)
        response = client.get(
            f"/sessions/{temp_jsonl_file.stem}/tree?path={subdir}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tree"] is not None
        names = [child["name"] for child in data["tree"]["children"]]
        assert "inner.txt" in names

    def test_tree_returns_error_for_nonexistent_path(self, home_tmp_path, temp_jsonl_file):
        """Test tree returns error for non-existent path."""
        info, _ = add_session(temp_jsonl_file)
        info.project_path = str(home_tmp_path)

        client = TestClient(app)
        response = client.get(
            f"/sessions/{temp_jsonl_file.stem}/tree?path={home_tmp_path}/nonexistent"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tree"] is None
        assert "does not exist" in data["error"]

    def test_tree_tilde_expansion(self, home_tmp_path, temp_jsonl_file):
        """Test tree expands tilde in path."""
        info, _ = add_session(temp_jsonl_file)
        info.project_path = str(home_tmp_path)

        # Create a test file
        (home_tmp_path / "tilde_test.txt").write_text("test")

        # Get path relative to home
        relative_path = home_tmp_path.relative_to(Path.home())

        client = TestClient(app)
        response = client.get(
            f"/sessions/{temp_jsonl_file.stem}/tree?path=~/{relative_path}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tree"] is not None
        names = [child["name"] for child in data["tree"]["children"]]
        assert "tilde_test.txt" in names

    def test_tree_excludes_hidden_files(self, home_tmp_path, temp_jsonl_file):
        """Test tree excludes hidden files and directories."""
        info, _ = add_session(temp_jsonl_file)
        info.project_path = str(home_tmp_path)

        # Create visible and hidden files
        (home_tmp_path / "visible.txt").write_text("visible")
        (home_tmp_path / ".hidden").write_text("hidden")
        (home_tmp_path / ".hiddendir").mkdir()

        client = TestClient(app)
        response = client.get(f"/sessions/{temp_jsonl_file.stem}/tree")

        assert response.status_code == 200
        data = response.json()
        names = [child["name"] for child in data["tree"]["children"]]
        assert "visible.txt" in names
        assert ".hidden" not in names
        assert ".hiddendir" not in names

    def test_tree_excludes_common_ignored_dirs(self, home_tmp_path, temp_jsonl_file):
        """Test tree excludes common ignored directories like node_modules."""
        info, _ = add_session(temp_jsonl_file)
        info.project_path = str(home_tmp_path)

        # Create various directories
        (home_tmp_path / "src").mkdir()
        (home_tmp_path / "node_modules").mkdir()
        (home_tmp_path / "__pycache__").mkdir()
        (home_tmp_path / "venv").mkdir()

        client = TestClient(app)
        response = client.get(f"/sessions/{temp_jsonl_file.stem}/tree")

        assert response.status_code == 200
        data = response.json()
        names = [child["name"] for child in data["tree"]["children"]]
        assert "src" in names
        assert "node_modules" not in names
        assert "__pycache__" not in names
        assert "venv" not in names

    def test_tree_directories_sorted_before_files(self, home_tmp_path, temp_jsonl_file):
        """Test tree sorts directories before files."""
        info, _ = add_session(temp_jsonl_file)
        info.project_path = str(home_tmp_path)

        # Create files and dirs (names chosen to test alphabetic sorting)
        (home_tmp_path / "aaa_file.txt").write_text("file")
        (home_tmp_path / "zzz_dir").mkdir()

        client = TestClient(app)
        response = client.get(f"/sessions/{temp_jsonl_file.stem}/tree")

        assert response.status_code == 200
        data = response.json()
        children = data["tree"]["children"]

        # Find indices
        dir_idx = next(i for i, c in enumerate(children) if c["name"] == "zzz_dir")
        file_idx = next(i for i, c in enumerate(children) if c["name"] == "aaa_file.txt")

        # Directory should come before file despite alphabetical order
        assert dir_idx < file_idx

    def test_tree_returns_home_path(self, home_tmp_path, temp_jsonl_file):
        """Test tree response includes home path for navigation."""
        info, _ = add_session(temp_jsonl_file)
        info.project_path = str(home_tmp_path)

        client = TestClient(app)
        response = client.get(f"/sessions/{temp_jsonl_file.stem}/tree")

        assert response.status_code == 200
        data = response.json()
        assert "home" in data
        assert data["home"] == str(Path.home())


class TestFileDeleteAPI:
    """Tests for the /api/file/delete endpoint."""

    def test_delete_file_success(self, home_tmp_path):
        """Test successful file deletion."""
        test_file = home_tmp_path / "to_delete.txt"
        test_file.write_text("delete me")

        client = TestClient(app)
        response = client.post(
            "/api/file/delete",
            json={"path": str(test_file)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["error"] is None
        assert not test_file.exists()

    def test_delete_empty_directory_success(self, home_tmp_path):
        """Test successful empty directory deletion."""
        empty_dir = home_tmp_path / "empty_dir"
        empty_dir.mkdir()

        client = TestClient(app)
        response = client.post(
            "/api/file/delete",
            json={"path": str(empty_dir)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert not empty_dir.exists()

    def test_delete_non_empty_directory_rejected(self, home_tmp_path):
        """Test that non-empty directories cannot be deleted."""
        non_empty_dir = home_tmp_path / "non_empty"
        non_empty_dir.mkdir()
        (non_empty_dir / "file.txt").write_text("content")

        client = TestClient(app)
        response = client.post(
            "/api/file/delete",
            json={"path": str(non_empty_dir)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not empty" in data["error"].lower()
        assert non_empty_dir.exists()

    def test_delete_file_not_found(self, home_tmp_path):
        """Test deletion of non-existent file."""
        client = TestClient(app)
        response = client.post(
            "/api/file/delete",
            json={"path": str(home_tmp_path / "nonexistent.txt")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_delete_outside_home_rejected(self):
        """Test that files outside home directory cannot be deleted."""
        client = TestClient(app)
        response = client.post(
            "/api/file/delete",
            json={"path": "/etc/passwd"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "allowed directories" in data["error"].lower()

    def test_delete_path_traversal_blocked(self, home_tmp_path):
        """Test that path traversal attempts are blocked."""
        client = TestClient(app)
        response = client.post(
            "/api/file/delete",
            json={"path": str(home_tmp_path / ".." / ".." / "etc" / "passwd")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "allowed directories" in data["error"].lower()


class TestFileDownloadEndpoint:
    """Tests for file download endpoint."""

    def test_download_file_success(self, home_tmp_path):
        """Test downloading a file."""
        test_file = home_tmp_path / "download_test.txt"
        test_file.write_text("Test content for download")

        client = TestClient(app)
        response = client.get(f"/api/file/download?path={test_file}")

        assert response.status_code == 200
        assert response.content == b"Test content for download"
        assert "attachment" in response.headers.get("content-disposition", "")
        assert "download_test.txt" in response.headers.get("content-disposition", "")

    def test_download_binary_file(self, home_tmp_path):
        """Test downloading a binary file."""
        test_file = home_tmp_path / "test.bin"
        test_file.write_bytes(bytes([0, 1, 2, 3, 255]))

        client = TestClient(app)
        response = client.get(f"/api/file/download?path={test_file}")

        assert response.status_code == 200
        assert response.content == bytes([0, 1, 2, 3, 255])

    def test_download_nonexistent_file(self, home_tmp_path):
        """Test that downloading a nonexistent file returns 404."""
        client = TestClient(app)
        response = client.get(f"/api/file/download?path={home_tmp_path}/nonexistent.txt")

        assert response.status_code == 404

    def test_download_directory_fails(self, home_tmp_path):
        """Test that downloading a directory fails."""
        client = TestClient(app)
        response = client.get(f"/api/file/download?path={home_tmp_path}")

        assert response.status_code == 400

    def test_download_path_traversal_blocked(self, home_tmp_path):
        """Test that path traversal attempts are blocked."""
        client = TestClient(app)
        response = client.get(
            f"/api/file/download?path={home_tmp_path}/../../../etc/passwd"
        )

        assert response.status_code == 403


class TestFileUploadEndpoint:
    """Tests for file upload endpoint."""

    def test_upload_file_success(self, home_tmp_path):
        """Test uploading a file."""
        client = TestClient(app)
        response = client.post(
            f"/api/file/upload?directory={home_tmp_path}&filename=uploaded.txt",
            content=b"Uploaded content",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["path"] == str(home_tmp_path / "uploaded.txt")

        # Verify file was actually created
        uploaded = home_tmp_path / "uploaded.txt"
        assert uploaded.exists()
        assert uploaded.read_text() == "Uploaded content"

    def test_upload_binary_file(self, home_tmp_path):
        """Test uploading a binary file."""
        binary_content = bytes([0, 1, 2, 255, 128])

        client = TestClient(app)
        response = client.post(
            f"/api/file/upload?directory={home_tmp_path}&filename=test.bin",
            content=binary_content,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        uploaded = home_tmp_path / "test.bin"
        assert uploaded.read_bytes() == binary_content

    def test_upload_to_nonexistent_directory(self, home_tmp_path):
        """Test uploading to nonexistent directory."""
        client = TestClient(app)
        response = client.post(
            f"/api/file/upload?directory={home_tmp_path}/nonexistent&filename=test.txt",
            content=b"content",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_upload_to_file_path(self, home_tmp_path):
        """Test uploading to a file path (not directory)."""
        file_path = home_tmp_path / "existing.txt"
        file_path.write_text("existing")

        client = TestClient(app)
        response = client.post(
            f"/api/file/upload?directory={file_path}&filename=test.txt",
            content=b"content",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not a directory" in data["error"].lower()

    def test_upload_path_traversal_in_filename(self, home_tmp_path):
        """Test that path traversal in filename is sanitized."""
        client = TestClient(app)
        response = client.post(
            f"/api/file/upload?directory={home_tmp_path}&filename=../../../etc/evil.txt",
            content=b"malicious content",
        )

        assert response.status_code == 200
        data = response.json()
        # Should sanitize to just "evil.txt" in the target directory
        assert data["success"] is True
        assert "evil.txt" in data["path"]
        assert home_tmp_path.as_posix() in data["path"]

    def test_upload_path_traversal_in_directory(self, home_tmp_path):
        """Test that path traversal in directory is blocked."""
        client = TestClient(app)
        response = client.post(
            "/api/file/upload?directory=/etc&filename=test.txt",
            content=b"content",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "allowed directories" in data["error"].lower()

    def test_upload_overwrites_existing_file(self, home_tmp_path):
        """Test that uploading overwrites an existing file."""
        existing = home_tmp_path / "existing.txt"
        existing.write_text("old content")

        client = TestClient(app)
        response = client.post(
            f"/api/file/upload?directory={home_tmp_path}&filename=existing.txt",
            content=b"new content",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert existing.read_text() == "new content"


class TestArchivedSessionsEndpoints:
    """Tests for archived sessions endpoints."""

    @pytest.fixture(autouse=True)
    def use_temp_config_dir(self, home_tmp_path, monkeypatch):
        """Use a temporary config directory to avoid deleting user's real config."""
        from vibedeck.routes import archives
        temp_config_dir = home_tmp_path / "config"
        temp_config_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(archives, "CONFIG_DIR", temp_config_dir)
        yield
        # Cleanup happens automatically when home_tmp_path is removed

    def test_get_archived_sessions_empty(self):
        """Test getting archived sessions when none exist."""
        client = TestClient(app)
        response = client.get("/api/archived-sessions")
        assert response.status_code == 200
        assert response.json() == {"archived": []}

    def test_archive_session(self):
        """Test archiving a session."""
        client = TestClient(app)
        response = client.post(
            "/api/archived-sessions/archive",
            json={"session_id": "test-session-123"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "archived"
        assert data["session_id"] == "test-session-123"

        # Verify it's in the list
        response = client.get("/api/archived-sessions")
        assert "test-session-123" in response.json()["archived"]

    def test_archive_session_already_archived(self):
        """Test archiving an already archived session."""
        client = TestClient(app)
        # Archive once
        client.post(
            "/api/archived-sessions/archive",
            json={"session_id": "test-session-123"}
        )
        # Archive again
        response = client.post(
            "/api/archived-sessions/archive",
            json={"session_id": "test-session-123"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "already_archived"

    def test_unarchive_session(self):
        """Test unarchiving a session."""
        client = TestClient(app)
        # First archive
        client.post(
            "/api/archived-sessions/archive",
            json={"session_id": "test-session-456"}
        )
        # Then unarchive
        response = client.post(
            "/api/archived-sessions/unarchive",
            json={"session_id": "test-session-456"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unarchived"
        assert data["session_id"] == "test-session-456"

        # Verify it's no longer in the list
        response = client.get("/api/archived-sessions")
        assert "test-session-456" not in response.json()["archived"]

    def test_unarchive_session_not_archived(self):
        """Test unarchiving a session that isn't archived."""
        client = TestClient(app)
        response = client.post(
            "/api/archived-sessions/unarchive",
            json={"session_id": "nonexistent-session"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "not_archived"

    def test_archived_sessions_persisted_to_file(self):
        """Test that archived sessions are persisted to file."""
        client = TestClient(app)
        client.post(
            "/api/archived-sessions/archive",
            json={"session_id": "persistent-session"}
        )

        # Check the file exists and contains the session
        from vibedeck.routes.archives import _get_archived_sessions_path
        config_path = _get_archived_sessions_path()
        assert config_path.exists()

        import json
        with open(config_path) as f:
            data = json.load(f)
        assert "persistent-session" in data["archived"]


# Note: SSE endpoint streaming tests are skipped because TestClient
# doesn't handle SSE event generators well. The endpoint is tested
# manually and through integration tests.
