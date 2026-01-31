# Commit Transcripts Installation

Install the commit transcripts prompt to automatically generate and attach conversation transcripts to git commits.

## Prerequisites

Check if uv is installed:
```bash
command -v uv
```

If not installed, ask the user for permission to install uv. Explain that:
- uv is required to run VibeDeck's transcript export
- It installs to ~/.local/bin (no root required)
- Installation command: `curl -LsSf https://astral.sh/uv/install.sh | sh`

After installation, restart the shell or run `source ~/.local/bin/env` to add uv to PATH.

## Installation Steps

1. Create the prompt includes directory:
```bash
mkdir -p ~/.claude/prompt-includes
```

2. Download the commit transcripts prompt:
```bash
curl -o ~/.claude/prompt-includes/commit-transcripts.md \
  https://raw.githubusercontent.com/tijszwinkels/VibeDeck/main/prompts/commit-transcripts.md
```

3. Add the following line to your `~/.claude/CLAUDE.md`:
```
@~/.claude/prompt-includes/commit-transcripts.md
```

## What This Does

Once installed, Claude Code will automatically generate gists of conversation transcripts and include links in commit messages.
