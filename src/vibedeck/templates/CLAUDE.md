# Frontend Notes

## Clipboard

Use `copyToClipboard(text, btn)` from `utils.js`. Handles HTTP fallback. Don't use `navigator.clipboard` directly.

## File Tree

Open directory in pane: `openRightPane()` then `loadFileTree(sessionId, path)` from `filetree.js`.
