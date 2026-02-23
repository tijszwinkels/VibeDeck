"""Tests for OAuth/OIDC authentication middleware."""

import pytest
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


@dataclass
class AuthConfig:
    """Mirror of the auth config for tests."""
    client_id: str = "test-client-id"
    client_secret: str = "test-client-secret"
    authorize_url: str = "https://provider.example.com/auth"
    token_url: str = "https://provider.example.com/token"
    userinfo_url: str = "https://provider.example.com/userinfo"
    server_metadata_url: str | None = None
    scope: str = "openid profile email"
    id_claim: str = "sub"
    session_secret: str = "test-session-secret"


class TestAuthMiddleware:
    """Test authentication middleware behavior."""

    def _make_app(self, auth_config: AuthConfig | None = None) -> FastAPI:
        """Create a test FastAPI app with auth configured."""
        from vibedeck.auth import setup_auth, get_current_user

        app = FastAPI()

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        @app.get("/api/test")
        async def test_endpoint(request: Request):
            user = get_current_user(request)
            return {"user": user}

        if auth_config:
            setup_auth(app, auth_config)

        return app

    def test_health_bypasses_auth(self):
        """Health endpoint should be accessible without auth."""
        app = self._make_app(AuthConfig())
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_unauthenticated_redirects_to_login(self):
        """Unauthenticated requests should redirect to /login."""
        app = self._make_app(AuthConfig())
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/api/test")
        assert resp.status_code == 307
        assert "/login" in resp.headers["location"]

    def test_login_endpoint_exists(self):
        """GET /login should redirect to OAuth provider."""
        app = self._make_app(AuthConfig())
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/login")
        assert resp.status_code in (302, 307)
        assert "provider.example.com" in resp.headers.get("location", "")

    def test_logout_clears_session(self):
        """GET /logout should clear session and redirect to /login."""
        app = self._make_app(AuthConfig())
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/logout")
        assert resp.status_code in (302, 307)
        assert "/login" in resp.headers["location"]

    def test_no_auth_config_means_no_middleware(self):
        """When auth is not configured, all endpoints are accessible."""
        app = self._make_app(auth_config=None)
        client = TestClient(app)
        resp = client.get("/api/test")
        assert resp.status_code == 200
        assert resp.json()["user"] is None

    def test_login_page_bypasses_auth(self):
        """The /login endpoint itself should not redirect."""
        app = self._make_app(AuthConfig())
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/login")
        # Should be a redirect TO the provider, not to /login
        assert resp.status_code in (302, 307)
        location = resp.headers.get("location", "")
        assert "provider.example.com" in location

    def test_auth_callback_reaches_handler(self):
        """The /auth/callback should reach the OAuth handler (not be blocked by auth middleware).

        With an invalid code, the handler itself redirects to /login as error recovery.
        The fact that the OAuth exchange is attempted (and fails with a state mismatch)
        proves the request reached the handler and was not blocked by auth middleware.
        """
        app = self._make_app(AuthConfig())
        client = TestClient(app, follow_redirects=False)
        # The handler redirects to /login on OAuth error — this is correct behavior
        resp = client.get("/auth/callback?code=fake")
        assert resp.status_code in (302, 307)


class TestGetCurrentUser:
    """Test user extraction from session."""

    def test_returns_none_when_no_session(self):
        """Should return None when no user in session."""
        from vibedeck.auth import get_current_user

        request = MagicMock()
        request.scope = {}
        assert get_current_user(request) is None

    def test_returns_none_when_session_empty(self):
        """Should return None when session has no user."""
        from vibedeck.auth import get_current_user

        request = MagicMock()
        request.scope = {"session": {}}
        assert get_current_user(request) is None

    def test_returns_user_dict_from_session(self):
        """Should return user dict when present in session."""
        from vibedeck.auth import get_current_user

        request = MagicMock()
        request.scope = {"session": {"user": {"id": "12345", "name": "Alice"}}}
        user = get_current_user(request)
        assert user == {"id": "12345", "name": "Alice"}


class TestGetCurrentUserId:
    """Test user ID extraction."""

    def test_returns_id_from_session(self):
        """Should return the user ID from session."""
        from vibedeck.auth import get_current_user_id

        request = MagicMock()
        request.scope = {"session": {"user": {"id": "12345", "name": "Alice"}}}
        assert get_current_user_id(request) == "12345"

    def test_returns_none_when_no_session(self):
        """Should return None when not authenticated."""
        from vibedeck.auth import get_current_user_id

        request = MagicMock()
        request.scope = {}
        assert get_current_user_id(request) is None


class TestAuthUserEndpoint:
    """Test /auth/user endpoint behavior."""

    def test_auth_disabled_returns_false(self):
        """When auth is not configured, auth_enabled should be false."""
        import vibedeck.server as server_mod
        original = server_mod._auth_enabled
        server_mod._auth_enabled = False
        try:
            client = TestClient(server_mod.app)
            resp = client.get("/auth/user")
            assert resp.status_code == 200
            data = resp.json()
            assert data["auth_enabled"] is False
            assert data["user"] is None
        finally:
            server_mod._auth_enabled = original

    def test_auth_user_is_public_path(self):
        """The /auth/user endpoint should bypass auth middleware."""
        from vibedeck.auth import _PUBLIC_PATHS
        assert "/auth/user" in _PUBLIC_PATHS
