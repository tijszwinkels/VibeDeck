# VibeDeck GUI Commands

VibeDeck is a GUI wrapper for Claude Code. If you see this text, this session is likely running within VibeDeck. You can control the GUI using special command blocks:

~~~markdown
```vibedeck
<openFile path="/full/path/to/file.py" />
```
~~~

## Commands

### openFile

Opens a file in the preview pane. Use full paths (`/home/...` or `~/...`).

- `path` (required): Absolute or home-relative path
- `line` (optional): Line number to scroll to
- `follow` (optional): `"true"` to auto-scroll on file changes (for logfiles)

```vibedeck
<openFile path="~/project/src/main.py" line="42" />
```

```vibedeck
<openFile path="/home/user/logs/app.log" follow="true" />
```

### openUrl

Opens a URL in a sandboxed iframe.

```vibedeck
<openUrl url="http://localhost:3000" />
```
