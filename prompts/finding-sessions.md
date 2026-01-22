# Finding and Reading Previous Sessions

VibeDeck provides tools to search and read previous Claude Code and OpenCode sessions.

## Finding Previous Sessions

Use the `vibedeck search` command to find sessions containing specific phrases:

```bash
uvx git+https://github.com/tijszwinkels/vibedeck search "phrase to find"
```

This shows the 5 most recent matches with 2 messages of context around each match.

## Reading Previous Sessions

To read a full session transcript:

```bash
uvx git+https://github.com/tijszwinkels/vibedeck md SESSION_FILE --hide-tools | less
```

Use `--hide-tools` for readable conversation flow; omit it when you need to see the actual code changes.

For OpenCode sessions, use the session ID directly:
```bash
uvx git+https://github.com/tijszwinkels/vibedeck md ses_xxx --hide-tools
```

## Tips

- Export to a tempfile and read selectively for large sessions
- Spawn subagents to search for specific content within a conversation
