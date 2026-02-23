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

**Backend implementation complete.** All 497 tests pass (51 new).

### What's implemented

- **`src/vibedeck/backends/isolation/`** — New backend package:
  - `discovery.py` — Per-user session discovery (`find_sessions_for_user`, `find_sessions_for_all_users`, `get_session_owner`)
  - `containers.py` — Docker container lifecycle (`ContainerManager` with create/exec/start commands, env file loading)
  - `backend.py` — `IsolationBackend` class implementing `CodingToolBackend` protocol
- **`src/vibedeck/auth.py`** — OAuth/OIDC via Authlib with `SessionMiddleware` + `AuthRequiredMiddleware`
- **`src/vibedeck/config.py`** — Added `IsolationConfig` and `AuthConfig` dataclasses
- **`config.toml.example`** — Example config with GitHub, Google, and Keycloak examples
- **Backend registration** in `registry.py`
- **Server integration** in `__init__.py` (isolation backend init, auth setup) and `server.py` (session owner callback)
- **User-scoped routes** in `routes/sessions.py` (filtered session list, access checks on session endpoints)
- **Dependencies** added: `authlib`, `httpx`, `itsdangerous`

### What's NOT implemented yet

- Frontend auth UI (login/logout buttons, user indicator) — Requirement 6
- Container `ensure_container()` async method (actually creating/starting Docker containers at runtime) — currently only builds commands
- SSE event stream filtering by user (events are broadcast to all connected clients)
- Frontend changes for new session creation with user context
