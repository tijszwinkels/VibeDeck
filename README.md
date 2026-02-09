# VibeDeck

Live-updating transcript viewer and web frontend for Claude Code and OpenCode sessions.

## Overview

VibeDeck is a front-end to Claude Code and OpenCode.

![VibeDeck Interface](docs/screenshot.png)

Mainly designed to:

- Get an overview on what exactly the LLM did and why, as quickly as possible.
- Interact with previous sessions or create new sessions in existing or new project directories.
- Keep track of many sessions and tasks running in parallel. Don't lose track as you kick off edits in multiple worktrees and/or projects.

A little bit like Conductor, but works remotely as well and is more flexible, Linux and Mac OS, can use git & worktrees but doesn't require it. Displays *all* coding agent sessions on the system, also ones that are started from the Claude Code CLI or IDE integration. Focus on fully understanding what the agent is doing, rather than hiding details. 

## Features

* Designed to get a quick & complete overview of everything the coding-agents are doing. Keeps a list of changed files. All paths in the session are clickable to immediately open in the file-viewer. URLs are clickable to copy to clipboard.
* Runs as a webserver, remotely or on localhost
* Watches the file-system. View sessions that initiated inside VibeDeck, as well as cli, IDE integration, etc. Everything in one place.
* Automatically generate titles and summaries for all Claude Code sessions. Automatically detects when working in a worktree. Automatically color by 'TODO', 'In Progress', 'Waiting for input' or 'Done'. Automatically detect branch/worktrees.
* full-fledged Claude Code interface in the browser. Run locally or in a remote dev environment.
* Built-in file-browser with Browsing / Upload / Download / Delete file
* File preview: Text / Code / MD / Images / Audio / HTML / Git Diff
* Built-in terminal emulator per session
* File-tail functionality (follow) for logfiles
* Tooling for generating transcripts of Claude Code sessions, and searching previous sessions.
* Preliminary support for OpenCode (but be prepared to file issues).

## Motivation

A few months ago, it was still necessary to babysit each coding agent session. Now; Not so much. Just kick off every feature in a separate worktree, let it cook, and when it's done try to get context as efficiently as possible to understand what was changed and why, review, and test, and that's what this tool aims to do. 

I got inspired by Simon Willison's [claude-code-transcripts](https://simonwillison.net/2025/Dec/25/claude-code-transcripts/). I feel it's a much cleaner way of showing exactly what the models are doing, while still drawing attention to the actual model responses to the user. That layout became the base of VibeDeck.

## Installation

```bash
uv tool install vibedeck
```

Or run directly without installing:

```bash
uvx vibedeck
```

### Install from Git (latest main)

```bash
uv tool install git+https://github.com/tijszwinkels/vibedeck
```

## Usage

### Live Viewer (default)

Starts a web server and opens your browser to `http://localhost:8765`:

```bash
# Start the server (auto-discovers recent sessions)
vibedeck

# Custom port
vibedeck --port 9000

# Don't auto-open browser
vibedeck --no-open

# Re-summarize sessions after 3 minutes of inactivity
vibedeck --summarize-after-idle-for 180

# Skip Claude Code permission prompts (use with caution!)
vibedeck --dangerously-skip-permissions
```

### Export to HTML

Export session transcripts to static HTML files with pagination, search, and statistics:

```bash
# Export Claude Code session to HTML
vibedeck html ~/.claude/projects/.../session.jsonl -o ./output

# Export OpenCode session by ID
vibedeck html ses_xxx -o ./output

# Upload to GitHub Gist with preview URL
vibedeck html session.jsonl --gist

# Open in browser after export
vibedeck html session.jsonl -o ./output --open
```

### Export to Markdown

```bash
# Export to stdout
vibedeck md session.jsonl

# Export to file
vibedeck md session.jsonl -o transcript.md

# Export OpenCode session
vibedeck md ses_xxx -o transcript.md
```

## Configuration

CLI options can be set in a TOML config file. Config files are automatically loaded from the following locations (in order of priority, later files override earlier ones):

1. `~/.config/vibedeck/config.toml` - global user config
2. `.vibedeck.toml` - project-local config
3. `config.toml` - project-local config (alternative name)

You can also specify a config file explicitly:

```bash
vibedeck --config myconfig.toml
```

CLI arguments always override config file values. See `config.example.toml` for all options.

Example config:

```toml
[serve]
port = 9000
host = "0.0.0.0"
no_open = true
max_sessions = 50
fork = true
dangerously_skip_permissions = true
summary_log = "~/logs/session-summaries.jsonl"
summary_after_long_running = 120  # 2 minutes (default)
summarize_after_idle_for = 180    # 3 minutes (default)
```

## Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run development server
uv run vibedeck --debug
```

## Security

* Listens on localhost only by default. Has no authentication and no encryption and is not designed to listen on any other interfaces. Best accessed remotely through an SSH tunnel `ssh -L8765:localhost:8765 [HOST]`.

* Allows full filesystem traversal through the interface by default.

* This applies to Claude Code in general: LLMs are very powerful, but can do stupid things even without prompt injections. If you give claude code any significant access to your system, I highly recommend doing so on an isolated system / vm with limited access to your personal data and credentials. Take backups and frequent periodic snapshots (zfs is nice!). This is especially important with `--dangerously-skip-permissions` .  

## Known issues

* The permission system is young and clunky. It is possible to alter Claude Code permissions from within VibeDeck, but it's more comfortable to run from projects with pre-configured claude code permissions, or with --dangerously-skip-permissions (but make sure to take proper precautions!). File an issue if you run into problems.

* OpenCode support is not currently maintained very well, and misses some features such as the permissions system and automatic summaries. Create an issue if anything doesn't work.

## Credits

The graphical interface of this project is inspired on [claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) by [Simon Willison](https://simonwillison.net/). This project allows to generate html-pages from claude code transcripts, put those in gists, and attach them to commit logs, which is tremendously useful. The layout is adapted from that project.

## License

Apache 2.0
