# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**VibeDeck** is a live-updating transcript viewer and static exporter for Claude Code and OpenCode sessions. Also functions as a **web-based frontend** (like Conductor) for sending messages to sessions. Message sending is enabled by default; use `--disable-send` to turn it off. Use `--fork` to enable session forking.

## Commands

```bash
uv sync                    # Install dependencies
uv run pytest              # Run all tests
uv run pytest tests/test_export.py -v  # Run specific test
uv run vibedeck --debug    # Run dev server
```

## Architecture

See `src/vibedeck/`:
- **`backends/`** - Pluggable backend system (`protocol.py` defines interfaces, `claude_code/` and `opencode/` implement them)
- **`server.py`** - FastAPI app with SSE streaming, session management, file tree API
- **`export.py`** - Static HTML/Markdown generation with gist upload
- **`templates/`** - Jinja2 templates and modular JS frontend

## Features

- **Live transcript streaming** - `server.py` (SSE via `/events`), `templates/static/js/messaging.js`
- **Session discovery** - `backends/*/discovery.py`, `sessions.py`
- **Session tabs** - `templates/static/js/sessions.js`
- **File tree navigator** - `server.py` (`/sessions/{id}/tree`), `templates/static/js/filetree.js`
- **File preview modal** - `server.py` (`/api/file`), `templates/static/js/preview.js`
- **Clickable file paths** - `backends/shared/rendering.py` (`make_paths_clickable`)
- **Send messages to sessions** - `server.py` (`/sessions/{id}/send`), `templates/static/js/messaging.js`
- **Fork sessions** - `server.py` (`/sessions/{id}/fork`), `backends/*/cli.py`
- **New session creation** - `server.py` (`/sessions/new`)
- **Thinking level detection** - `backends/thinking.py`
- **Static HTML export** - `export.py`
- **Markdown export** - `export.py`
- **Gist upload** - `export.py` (`upload_to_gist`)
- **Token usage/cost tracking** - `backends/*/pricing.py`
- **Multi-backend support** - `backends/multi.py`, `backends/registry.py`
- **GUI Commands** - `templates/static/js/commands.js`, see `@prompts/gui-commands.md`

## Adding a Backend

1. Create `backends/newbackend/` implementing `CodingToolBackend` protocol
2. Register in `backends/registry.py`

## Setting the Session Title

You can set a custom title for the current session by emitting a `vibedeck` command block in your output. The VibeDeck frontend intercepts this on render and POSTs to `/api/session-titles/set`, persisting to `~/.config/vibedeck/session-titles.json`.

```vibedeck
<setTitle title="Investigating channel rename flow" />
```

Emit a new block whenever the session's focus changes — it auto-fires on every render (including catchup replay), and last write wins. Pass an empty string to clear and revert to the default title.

## Commit Transcripts

On every commit, publish a gist of the conversation transcript and add the preview URL to the commit message.

### Find Your Session File

**For Claude Code sessions:**
```bash
# Find the project directory
ls ~/.claude/projects/ | grep $(basename $PWD)

# List recent sessions (most recent first)
ls -t ~/.claude/projects/-home-claude-projects-vibedeck/*.jsonl | head -5
```

**For OpenCode sessions:**
```bash
# List recent OpenCode sessions (most recent first)
ls -lt ~/.local/share/opencode/storage/message/ | head -10
```

### Generate and Upload Transcript

**For Claude Code sessions:**
```bash
# Using this project's built-in export (recommended)
uv run vibedeck html "$(ls -t ~/.claude/projects/-home-claude-projects-vibedeck/*.jsonl | head -1)" --gist

# Or with explicit session file
uv run vibedeck html ~/.claude/projects/-home-claude-projects-vibedeck/SESSION_ID.jsonl --gist
```

**For OpenCode sessions:**
```bash
# Using session ID directly (e.g., ses_xxx)
uv run vibedeck html ses_461e502a0ffez5ZvcUYkCT4iaj --gist

# Or find the most recent session and export it
uv run vibedeck html "$(ls -t ~/.local/share/opencode/storage/message/ | head -1)" --gist
```

The command outputs:
- `Gist: https://gist.github.com/...` - the raw gist URL
- `Preview: https://gisthost.github.io/?GIST_ID/index.html` - the preview URL to use

### Add to Commit Message

Add the preview URL to the commit message footer:
```
transcript: https://gisthost.github.io/?GIST_ID/index.html
```

### Safety: DO NOT generate transcript gists if ANY of these conditions apply:
- Transcript contains secrets, API keys, passwords, or tokens
- A `.env` file or any config file containing secrets was read
- Log files were read
- Database queries were executed
- Any other potentially sensitive information was accessed

When in doubt, skip the transcript.
