# OpenCode Data Model

This document describes the storage format and semantics of OpenCode's session data.

## Storage Hierarchy

```
PROJECT                           (one per git repo/directory)
└── id: SHA hash of worktree path
└── worktree: "/home/user/myproject"

    ↓ has many

SESSION                           (one conversation thread)
└── id: "ses_..."
└── projectID: links to project
└── title: "Optimizing discovery.py session lookup"
└── directory: working directory

    ↓ has many

MESSAGE                           (one turn in conversation)
└── id: "msg_..."
└── sessionID: links to session
└── role: "user" | "assistant"
└── parentID: previous message (for assistant msgs)
└── tokens, cost, finish reason

    ↓ has many

PART                              (incremental content within a message)
└── id: "prt_..."
└── messageID: links to message
└── sessionID: links to session (denormalized)
└── type: "step-start" | "text" | "tool" | "step-finish"
```

## Storage Layout

```
~/.local/share/opencode/storage/
├── project/
│   └── {projectID}.json                    # Project metadata
├── session/
│   └── {projectID}/
│       └── {sessionID}.json                # Session metadata
├── message/
│   └── {sessionID}/
│       └── {messageID}.json                # Message metadata
└── part/
    └── {messageID}/
        └── {partID}.json                   # Part content
```

## Entity Semantics

### Project

Represents a **codebase/repository**.

| Field | Description |
|-------|-------------|
| `id` | SHA hash of the worktree path |
| `worktree` | Absolute path to the project directory |
| `vcs` | Version control system (e.g., "git") |
| `sandboxes` | Associated sandbox environments |
| `time.created` | Creation timestamp (ms) |
| `time.updated` | Last update timestamp (ms) |

### Session

A **conversation thread** within a project. Like a "Claude Code session" - one task or feature discussion.

| Field | Description |
|-------|-------------|
| `id` | Session ID (e.g., "ses_46275e2c6ffe...") |
| `projectID` | Links to parent project |
| `directory` | Working directory for the session |
| `title` | Auto-generated summary of the conversation |
| `version` | OpenCode version that created the session |
| `summary.additions` | Total lines added |
| `summary.deletions` | Total lines deleted |
| `summary.files` | Number of files changed |

### Message

One turn in the conversation. Two roles:

| Role | Description |
|------|-------------|
| **user** | Human input. Contains: agent mode selected, model preference |
| **assistant** | AI response. Contains: parentID, token counts, cost, finish reason |

**User message fields:**

| Field | Description |
|-------|-------------|
| `id` | Message ID (e.g., "msg_b996eae6d001...") |
| `sessionID` | Links to parent session |
| `role` | "user" |
| `time.created` | Timestamp when sent |
| `agent` | Agent mode (e.g., "build", "ask") |
| `model.providerID` | Provider (e.g., "anthropic") |
| `model.modelID` | Model (e.g., "claude-opus-4-5") |

**Assistant message fields:**

| Field | Description |
|-------|-------------|
| `id` | Message ID |
| `sessionID` | Links to parent session |
| `role` | "assistant" |
| `parentID` | Links to the user message this responds to |
| `time.created` | Start timestamp |
| `time.completed` | End timestamp |
| `modelID` | Model used |
| `providerID` | Provider used |
| `mode` | Agent mode |
| `path.cwd` | Current working directory |
| `path.root` | Project root |
| `tokens.input` | Input tokens |
| `tokens.output` | Output tokens |
| `tokens.reasoning` | Reasoning tokens (if extended thinking) |
| `tokens.cache.read` | Cached tokens read |
| `tokens.cache.write` | Cached tokens written |
| `cost` | Cost in dollars |
| `finish` | Finish reason: "tool-calls" or "end-turn" |

### Part

**Streaming increments** within an assistant message. Parts are written as they happen, enabling live progress display.

| Type | When | Contains |
|------|------|----------|
| `step-start` | Beginning of API call | Git `snapshot` hash |
| `text` | Model outputs text | `text` field with content |
| `tool` | Model calls a tool | `tool`, `callID`, `state` |
| `step-finish` | End of API call | `reason`, token counts, cost |

**Common part fields:**

| Field | Description |
|-------|-------------|
| `id` | Part ID (e.g., "prt_b9974d8bd001...") |
| `sessionID` | Links to session (denormalized for quick lookup) |
| `messageID` | Links to parent message |
| `type` | Part type |

**Tool part structure:**

```json
{
  "type": "tool",
  "tool": "read",
  "callID": "toolu_01...",
  "state": {
    "status": "completed",
    "input": { "filePath": "/path/to/file" },
    "output": "file contents...",
    "time": { "start": 1767806134140, "end": 1767806134143 }
  }
}
```

Tool status values: `pending` → `running` → `completed` | `error`

**Step-finish reasons:**

| Reason | Meaning |
|--------|---------|
| `tool-calls` | Model made tool calls, agentic loop continues |
| `end-turn` | Model finished responding, back to user |

## The Agentic Loop

A single assistant message can span multiple API round-trips when using tools:

```
User sends message
    ↓
┌─────────────────────────────────────────┐
│ step-start (snapshot git state)         │
│ text: "I'll help you with..."           │
│ tool: read file1.py                     │
│ tool: read file2.py    (parallel)       │
│ step-finish (reason: tool-calls)        │
├─────────────────────────────────────────┤
│ step-start                              │  ← Loop continues
│ text: "Based on what I found..."        │
│ tool: edit file1.py                     │
│ step-finish (reason: tool-calls)        │
├─────────────────────────────────────────┤
│ step-start                              │
│ text: "Done! Here's what I changed..."  │
│ step-finish (reason: end-turn)          │  ← Finished
└─────────────────────────────────────────┘
    ↓
Back to user
```

Each `step-start`/`step-finish` pair represents one API call. Multiple steps within a single message = the agentic tool-use loop.

## Design Rationale

1. **Streaming**: Parts are written incrementally, enabling real-time UI updates
2. **Granular storage**: Each tool call is a separate file, easy to query/index
3. **Recovery**: If the process crashes, partial progress is preserved
4. **Denormalization**: `sessionID` is duplicated in parts for O(1) lookup without joining
