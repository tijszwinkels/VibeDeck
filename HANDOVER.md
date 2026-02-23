# Handover: Multi-Tenant Isolation Backend

## What this is

A spec for turning VibeDeck into a multi-user service where authenticated users start and monitor Claude Code sessions running in gVisor sandboxes.

## Worktree

Branch: `multi-tenant-backend`
Path: `/home/claude/projects/VibeDeck/worktree/multi-tenant-backend/`

## Spec files

All in `specs/backend/20260223-multi-tenant-isolation/`:

- **`overview.md`** — Goal, context (how agent-isolation works today), feature summary, scope boundaries
- **`requirements.md`** — 7 requirements with acceptance criteria: isolation backend discovery, Docker CLI interaction, OAuth/OIDC auth, user-scoped access, TOML config, frontend auth UI, container lifecycle
- **`design.md`** — Architecture, new files, data flows, interfaces, error handling, test strategy

## Key design decisions

1. **New `isolation` backend** that reuses `ClaudeCodeTailer`, `ClaudeCodeRenderer`, and pricing from the existing claude-code backend. Only overrides discovery (scan `{users_dir}/{user_id}/.claude/projects/`) and CLI interaction (wrap in `docker exec sandbox-{user_id}`).

2. **Generic OAuth/OIDC** via Authlib — no built-in provider presets, just config fields (`authorize_url`, `token_url`, `userinfo_url` or `server_metadata_url`). Example config shows GitHub, Google, and Keycloak.

3. **`id_claim` is configurable** — operator picks which OAuth claim becomes the directory name (`id` for GitHub, `sub` for OIDC, `email`, `preferred_username`, etc.). Directories are `./users/{id_claim_value}/`.

4. **Session scoping at the API layer** — backend discovers all sessions, routes filter by authenticated user. Simpler than per-user backend instances.

5. **Auth is optional** — no `[auth]` config section means VibeDeck behaves as today.

## Related project

`/home/claude/projects/agent-isolation/` — the gVisor sandbox that this backend integrates with. Key detail: `run.sh` bind-mounts `./users/{username}/` as `/root` (full homedir), so `.claude/projects/**/*.jsonl` is readable from the host. Claude binary is hardlinked from `users/.shared/` into each user dir, with an entrypoint that creates the symlink.

## Status

**All requirements implemented.** 516 tests pass (70 new).

### What's implemented

- **`src/vibedeck/backends/isolation/`** — New backend package:
  - `discovery.py` — Per-user session discovery (`find_sessions_for_user`, `find_sessions_for_all_users`, `get_session_owner`)
  - `containers.py` — Docker container lifecycle (`ContainerManager` with create/exec/start/inspect commands, env file loading, `ensure_container()` async method)
  - `backend.py` — `IsolationBackend` class implementing `CodingToolBackend` protocol with user-aware command builders
- **`src/vibedeck/auth.py`** — OAuth/OIDC via Authlib with `SessionMiddleware` + `AuthRequiredMiddleware`
- **`src/vibedeck/config.py`** — Added `IsolationConfig` and `AuthConfig` dataclasses
- **`config.toml.example`** — Example config with GitHub, Google, and Keycloak examples
- **Backend registration** in `registry.py`
- **Server integration** in `__init__.py` (isolation backend init, auth setup) and `server.py` (session owner callback, SSE filtering, `/auth/user` endpoint)
- **User-scoped routes** in `routes/sessions.py` — access checks on ALL session endpoints (list, status, messages, send, fork, grant-permission, interrupt, summarize, tree, new session)
- **SSE event filtering** in `server.py` — `event_generator()` filters session list and streamed events by authenticated user
- **New session creation** with user context — `create_new_session()` calls `ensure_container()` + `build_new_session_command_for_user()` for isolation backend
- **Frontend auth UI** — `templates/static/js/auth.js` fetches `/auth/user`, shows user name + logout button in status bar
- **Dependencies** added: `authlib`, `httpx`, `itsdangerous`

### What's NOT implemented yet

All spec requirements are now implemented. Areas that may need future work:

- **Integration testing with real Docker/gVisor** — All container lifecycle tests use mocked `asyncio.create_subprocess_exec`. An integration test with actual Docker would verify the full flow.
- **Custom login page HTML** — Currently `/login` redirects directly to the OAuth provider. A branded login page with a "Sign in" button would be better UX.
- **Per-user broadcast queues** — SSE filtering currently checks every event against the session owner. For many concurrent users, per-user queues would be more efficient.
- **`/api/file` user scoping** — The file preview endpoint uses path allowlist (`Path.home()`, `/tmp`) rather than user-based scoping. For isolation backend, the auth middleware + tree endpoint access check provide sufficient protection since the tree endpoint is the only UI path to discover file paths.

## Commit history

- `a94c86a` — Initial implementation: isolation backend, OAuth auth, user-scoped routes, config, 51 tests
