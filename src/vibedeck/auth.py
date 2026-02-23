"""OAuth/OIDC authentication middleware and routes.

Provides generic OAuth 2.0 / OIDC authentication via Authlib. Works with
any provider (GitHub, Google, GitLab, Keycloak, etc.) — all provider details
are configured explicitly via AuthConfig.

When auth is not configured, VibeDeck operates as today (no login required).
"""

from __future__ import annotations

import logging

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Paths that bypass authentication
_PUBLIC_PATHS = frozenset({"/login", "/auth/callback", "/health", "/favicon.ico"})


def get_current_user(request: Request) -> dict | None:
    """Extract user from session cookie.

    Args:
        request: Starlette/FastAPI request.

    Returns:
        User dict with 'id' and 'name' keys, or None if not authenticated.
    """
    session = request.scope.get("session")
    if session is None:
        return None
    return session.get("user")


def get_current_user_id(request: Request) -> str | None:
    """Extract just the user_id (directory name) from session.

    Args:
        request: Starlette/FastAPI request.

    Returns:
        User ID string, or None if not authenticated.
    """
    user = get_current_user(request)
    return user["id"] if user else None


class AuthRequiredMiddleware:
    """ASGI middleware that redirects unauthenticated requests to /login.

    Must be placed inside SessionMiddleware so that request.session is available.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Allow public paths and static assets
        if path in _PUBLIC_PATHS or path.startswith("/static/"):
            await self.app(scope, receive, send)
            return

        # Check authentication via session
        session = scope.get("session", {})
        if session.get("user") is not None:
            await self.app(scope, receive, send)
            return

        # Redirect to login
        response = RedirectResponse("/login", status_code=307)
        await response(scope, receive, send)


def setup_auth(app: FastAPI, config) -> None:
    """Configure OAuth and add auth routes/middleware to the app.

    Adds:
    - SessionMiddleware for cookie-based sessions
    - AuthRequiredMiddleware to redirect unauthenticated requests
    - GET /login — redirect to OAuth provider
    - GET /auth/callback — handle OAuth callback
    - GET /logout — clear session

    Args:
        app: FastAPI application instance.
        config: AuthConfig with OAuth provider settings.
    """
    if not config.session_secret:
        raise ValueError(
            "Auth is configured but session_secret is missing. "
            "Set [auth] session_secret in your config file."
        )
    if not config.client_id or not config.client_secret:
        raise ValueError(
            "Auth is configured but client_id or client_secret is missing. "
            "Set [auth] client_id and client_secret in your config file."
        )

    has_individual_urls = config.authorize_url and config.token_url and config.userinfo_url
    has_discovery = config.server_metadata_url
    if not has_individual_urls and not has_discovery:
        raise ValueError(
            "Auth requires either (authorize_url + token_url + userinfo_url) "
            "or server_metadata_url. Set these in [auth] config."
        )

    # Configure OAuth client
    oauth = OAuth()

    register_kwargs: dict = {
        "name": "provider",
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "client_kwargs": {"scope": config.scope},
    }

    if config.server_metadata_url:
        register_kwargs["server_metadata_url"] = config.server_metadata_url
    else:
        register_kwargs["authorize_url"] = config.authorize_url
        register_kwargs["access_token_url"] = config.token_url
        register_kwargs["userinfo_endpoint"] = config.userinfo_url

    oauth.register(**register_kwargs)

    # Store config for use in route closures
    _auth_state = {
        "oauth": oauth,
        "id_claim": config.id_claim,
        "userinfo_url": config.userinfo_url,
    }

    # Add middleware — order matters (LIFO):
    # AuthRequiredMiddleware added first, then SessionMiddleware wraps it.
    # This means SessionMiddleware runs first (outermost), setting up the session,
    # then AuthRequiredMiddleware checks authentication.
    app.add_middleware(AuthRequiredMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=config.session_secret)

    @app.get("/login")
    async def login(request: Request):
        """Redirect to OAuth provider for authentication."""
        provider = _auth_state["oauth"].create_client("provider")
        redirect_uri = str(request.url_for("auth_callback"))
        return await provider.authorize_redirect(request, redirect_uri)

    @app.get("/auth/callback")
    async def auth_callback(request: Request):
        """Handle OAuth callback — exchange code for token, fetch userinfo."""
        provider = _auth_state["oauth"].create_client("provider")

        try:
            token = await provider.authorize_access_token(request)
        except Exception as e:
            logger.error(f"OAuth token exchange failed: {e}")
            return RedirectResponse("/login")

        # Get userinfo — either from OIDC ID token or explicit userinfo endpoint
        userinfo = token.get("userinfo")
        if not userinfo:
            try:
                resp = await provider.get(
                    _auth_state["userinfo_url"] or "userinfo",
                    token=token,
                )
                userinfo = resp.json()
            except Exception as e:
                logger.error(f"Failed to fetch userinfo: {e}")
                return RedirectResponse("/login")

        # Extract the configured ID claim
        id_claim = _auth_state["id_claim"]
        user_id = userinfo.get(id_claim)
        if user_id is None:
            logger.error(
                f"id_claim '{id_claim}' not found in userinfo response. "
                f"Available claims: {list(userinfo.keys())}"
            )
            return RedirectResponse("/login")

        # Store user in session
        request.session["user"] = {
            "id": str(user_id),
            "name": userinfo.get("name") or userinfo.get("login") or str(user_id),
        }

        logger.info(f"User authenticated: {request.session['user']}")
        return RedirectResponse("/")

    @app.get("/logout")
    async def logout(request: Request):
        """Clear session and redirect to login."""
        request.session.clear()
        return RedirectResponse("/login")

    @app.get("/auth/user")
    async def auth_user(request: Request):
        """Get the currently authenticated user."""
        user = get_current_user(request)
        return {"user": user, "auth_enabled": True}

    logger.info("OAuth authentication enabled")
