// Commands module - Parse and execute VibeDeck GUI commands
//
// Commands are embedded as ```vibedeck code blocks in the LLM's markdown output,
// which get rendered as <pre><code class="language-vibedeck"> in HTML.
//
// ## Adding a New Command
//
// 1. Add a regex match in executeCommandBlock() to detect your command:
//      const myCommandMatch = block.match(/<myCommand\s+([^>]*)\/>/i);
//
// 2. Create an async execute function (e.g., executeMyCommand(attrs, sessionId)):
//    - Parse attributes using parseAttributes()
//    - Implement the command logic
//    - Use dynamic imports for preview.js functions to avoid circular deps
//
// 3. Update prompts/gui-commands.md to document the new command for LLMs
//    (without this, LLMs won't know the command exists!)
//
// 4. If the command needs new preview.js functions, add and export them there

import { state } from './state.js';

// Counter for unique command IDs
let commandIdCounter = 0;

/**
 * Parse VibeDeck commands from HTML content, add execute buttons, and optionally auto-execute.
 *
 * @param {string} html - HTML content that may contain vibedeck commands
 * @param {string} sessionId - Current session ID for path resolution
 * @param {boolean} isLiveStreaming - If true, we're in live streaming mode (catchup complete)
 * @returns {string} HTML with execute buttons added to vibedeck blocks
 */
export function parseAndExecuteCommands(html, sessionId, isLiveStreaming = true) {
    // Match <pre><code class="language-vibedeck">...</code></pre> blocks
    const vibedeckPattern = /<pre><code([^>]*class="[^"]*language-vibedeck[^"]*"[^>]*)>([\s\S]*?)<\/code><\/pre>/gi;

    // Replace each vibedeck block with one that has an execute button
    const modifiedHtml = html.replace(vibedeckPattern, (match, attrs, content) => {
        const commandId = `vibedeck-cmd-${++commandIdCounter}`;
        const commandBlock = decodeHtmlEntities(content);

        // Store command data for later execution
        pendingCommands.set(commandId, { block: commandBlock, sessionId });

        // Only auto-execute if:
        // 1. We're in live streaming mode (catchup complete), AND
        // 2. The command has follow="true" attribute
        if (isLiveStreaming && commandHasFollow(commandBlock)) {
            executeCommandBlock(commandBlock, sessionId);
        }

        // Add execute button outside the code block, at top-right
        const buttonHtml = `<button class="vibedeck-execute-btn" data-command-id="${commandId}" title="Execute command">▶</button>`;

        return `<div class="vibedeck-command-wrapper">${buttonHtml}<pre><code${attrs}>${content}</code></pre></div>`;
    });

    return modifiedHtml;
}

/**
 * Check if a command block has follow="true" attribute.
 */
function commandHasFollow(block) {
    // Match follow="true" in any command
    return /follow="true"/i.test(block);
}

// Store pending commands for manual execution
const pendingCommands = new Map();

/**
 * Initialize click handlers for vibedeck execute buttons.
 * Call this after the DOM is ready.
 */
export function initCommandButtons() {
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('.vibedeck-execute-btn');
        if (!btn) return;

        const commandId = btn.dataset.commandId;
        const command = pendingCommands.get(commandId);
        if (command) {
            executeCommandBlock(command.block, command.sessionId);
            // Visual feedback
            btn.textContent = '✓';
            setTimeout(() => btn.textContent = '▶', 1000);
        }
    });

    // Listen for programmatic command execution (e.g., from artifacts panel)
    window.addEventListener('vibedeck-command', (e) => {
        const { command } = e.detail;
        if (command) {
            executeCommandBlock(command, state.activeSessionId);
        }
    });
}

/**
 * Decode HTML entities in text.
 */
function decodeHtmlEntities(text) {
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
}

/**
 * Execute a single command block.
 */
function executeCommandBlock(block, sessionId) {
    const openFileMatch = block.match(/<openFile\s+([^>]*)\/>/i);
    if (openFileMatch) {
        const attrs = parseAttributes(openFileMatch[1]);
        executeOpenFile(attrs, sessionId);
        return;
    }

    const openUrlMatch = block.match(/<openUrl\s+([^>]*)\/>/i);
    if (openUrlMatch) {
        const attrs = parseAttributes(openUrlMatch[1]);
        executeOpenUrl(attrs);
        return;
    }

    console.warn('Unknown VibeDeck command:', block);
}

/**
 * Parse XML-style attributes from a string.
 */
function parseAttributes(attrString) {
    const attrs = {};
    const attrPattern = /(\w+)="([^"]*)"/g;
    let match;
    while ((match = attrPattern.exec(attrString)) !== null) {
        attrs[match[1]] = match[2];
    }
    return attrs;
}

/**
 * Execute an openFile command.
 */
async function executeOpenFile(attrs, sessionId) {
    const { path, follow, line } = attrs;
    if (!path) {
        console.warn('openFile: missing path attribute');
        return;
    }

    try {
        const resolvedPath = await resolvePath(path, sessionId);
        if (!resolvedPath) {
            console.warn('openFile: path resolution failed for', path);
            return;
        }

        const { openPreviewPane, setFollowMode, scrollToLine } = await import('./preview.js');

        if (follow === 'true') {
            setFollowMode(true);
        }

        await openPreviewPane(resolvedPath);

        if (line) {
            const lineNum = parseInt(line, 10);
            if (!isNaN(lineNum) && lineNum > 0) {
                setTimeout(() => scrollToLine(lineNum), 100);
            }
        }
    } catch (err) {
        console.error('openFile: error executing command', err);
    }
}

/**
 * Execute an openUrl command.
 */
async function executeOpenUrl(attrs) {
    const { url } = attrs;
    if (!url) {
        console.warn('openUrl: missing url attribute');
        return;
    }

    if (!url.startsWith('http://') && !url.startsWith('https://')) {
        console.warn('openUrl: invalid URL scheme, must be http or https');
        return;
    }

    try {
        const { openUrlPane } = await import('./preview.js');
        openUrlPane(url);
    } catch (err) {
        console.error('openUrl: error executing command', err);
    }
}

/**
 * Resolve a path using the server API.
 */
async function resolvePath(path, sessionId) {
    try {
        const params = new URLSearchParams({ path });
        if (sessionId) {
            params.set('session_id', sessionId);
        }

        const response = await fetch(`/api/path/resolve?${params}`);
        if (!response.ok) {
            return null;
        }

        const data = await response.json();
        return data.resolved || null;
    } catch (err) {
        console.error('resolvePath: fetch error', err);
        return null;
    }
}
