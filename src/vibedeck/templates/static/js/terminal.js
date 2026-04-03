/**
 * Terminal integration using xterm.js
 *
 * Supports two modes that swap what's shown in the main content area:
 *   - **Transcript mode** (default): session messages/transcript
 *   - **Terminal mode**: interactive CLI session (e.g. `claude --resume`)
 *     or a plain shell fallback
 *
 * The terminal toggle replaces the transcript view with a full-size terminal.
 * The file tree in the right pane remains available in both modes.
 * The input bar is hidden when the terminal is active (you type directly
 * into the terminal).
 */

import { dom, state } from './state.js';
import {
    createKittyKeyboardState,
    resetKittyKeyboardState,
    getKittyKeyboardFlags,
    processKittyKeyboardProtocolOutput,
    encodeKittyKeyEvent,
} from './terminal-keyboard.js';

// Terminal state
let terminal = null;
let fitAddon = null;
let webSocket = null;
let terminalEnabled = false;
const kittyKeyboardState = createKittyKeyboardState();

// Track which session the current terminal connection belongs to
let activeTerminalSessionId = null;

/**
 * Initialize the terminal module.
 * Checks if terminal is enabled and sets up event listeners.
 */
export async function initTerminal() {
    // Check if terminal feature is enabled
    try {
        const response = await fetch('api/terminal/enabled');
        const data = await response.json();
        terminalEnabled = data.enabled;
    } catch (e) {
        console.warn('Failed to check terminal status:', e);
        terminalEnabled = false;
    }

    if (!terminalEnabled) {
        const toggleBtn = document.getElementById('terminal-toggle-btn');
        if (toggleBtn) {
            toggleBtn.style.display = 'none';
        }
        return;
    }

    // Set up toggle button
    const toggleBtn = document.getElementById('terminal-toggle-btn');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleTerminal);
    }

    // Load xterm.js dynamically
    await loadXterm();
}

/**
 * Load xterm.js and addons from CDN.
 */
async function loadXterm() {
    if (window.Terminal) return;

    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css';
    document.head.appendChild(link);

    await loadScript('https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js');
    await loadScript('https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js');
    await loadScript('https://cdn.jsdelivr.net/npm/@xterm/addon-web-links@0.11.0/lib/addon-web-links.min.js');
}

/**
 * Helper to load a script dynamically.
 */
function loadScript(src) {
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = src;
        script.onload = resolve;
        script.onerror = reject;
        document.head.appendChild(script);
    });
}

/**
 * Toggle between transcript mode and terminal mode.
 */
export function toggleTerminal() {
    if (!terminalEnabled) return;

    state.terminalOpen = !state.terminalOpen;

    const toggleBtn = document.getElementById('terminal-toggle-btn');
    if (state.terminalOpen) {
        toggleBtn?.classList.add('active');
        showTerminalView();
    } else {
        toggleBtn?.classList.remove('active');
        showTranscriptView();
    }
}

/**
 * Switch to terminal view: hide transcript, show terminal, hide input bar.
 */
function showTerminalView() {
    dom.mainContent?.classList.add('terminal-active');
    dom.inputBar?.classList.add('hidden');
    dom.floatingControls?.style.setProperty('display', 'none');

    openTerminal();
}

/**
 * Switch to transcript view: show transcript, hide terminal, show input bar.
 */
function showTranscriptView() {
    dom.mainContent?.classList.remove('terminal-active');
    if (state.sendEnabled) {
        dom.inputBar?.classList.remove('hidden');
    }
    dom.floatingControls?.style.removeProperty('display');

    closeTerminal();
}

/**
 * Open terminal and connect to WebSocket.
 */
async function openTerminal() {
    const container = document.getElementById('terminal-container');
    if (!container) return;

    // Create terminal instance if it doesn't exist yet
    if (!terminal) {
        terminal = new window.Terminal({
            cursorBlink: true,
            fontSize: 14,
            fontFamily: 'ui-monospace, "SF Mono", Menlo, Monaco, "Cascadia Mono", "Segoe UI Mono", "Roboto Mono", monospace',
            theme: getTerminalTheme(),
            allowProposedApi: true,
        });

        fitAddon = new window.FitAddon.FitAddon();
        terminal.loadAddon(fitAddon);

        const webLinksAddon = new window.WebLinksAddon.WebLinksAddon();
        terminal.loadAddon(webLinksAddon);

        terminal.open(container);

        // Focus terminal when clicking on container
        container.addEventListener('click', () => terminal.focus());

        terminal.attachCustomKeyEventHandler((event) => {
            if (event.type !== 'keydown') {
                return true;
            }

            const kittySequence = encodeKittyKeyEvent(event, getKittyKeyboardFlags(kittyKeyboardState));
            if (!kittySequence) {
                return true;
            }

            if (webSocket && webSocket.readyState === WebSocket.OPEN) {
                event.preventDefault();
                webSocket.send(JSON.stringify({ type: 'input', data: kittySequence }));
                return false;
            }

            return true;
        });

        // Handle terminal input -> WebSocket
        terminal.onData(data => {
            if (webSocket && webSocket.readyState === WebSocket.OPEN) {
                webSocket.send(JSON.stringify({ type: 'input', data }));
            }
        });

        // Handle terminal resize -> WebSocket
        terminal.onResize(({ cols, rows }) => {
            if (webSocket && webSocket.readyState === WebSocket.OPEN) {
                webSocket.send(JSON.stringify({ type: 'resize', cols, rows }));
            }
        });

        // Refit on window resize
        window.addEventListener('resize', () => {
            if (state.terminalOpen && fitAddon) {
                fitAddon.fit();
            }
        });
    }

    resetKittyKeyboardState(kittyKeyboardState);

    // Fit after layout settles (terminal view needs to be visible first)
    setTimeout(() => {
        if (fitAddon) fitAddon.fit();
        terminal?.focus();
    }, 50);

    connectWebSocket();
}

/**
 * Get terminal theme based on current page theme.
 */
function getTerminalTheme() {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    if (isDark) {
        return {
            background: '#1a1a1a',
            foreground: '#e0e0e0',
            cursor: '#ffffff',
            cursorAccent: '#1a1a1a',
            selection: 'rgba(255, 255, 255, 0.3)',
        };
    } else {
        return {
            background: '#ffffff',
            foreground: '#1a1a1a',
            cursor: '#000000',
            cursorAccent: '#ffffff',
            selection: 'rgba(0, 0, 0, 0.3)',
        };
    }
}

/**
 * Connect to terminal WebSocket.
 *
 * Checks if the active session supports terminal mode, and connects
 * as a session terminal if so. Falls back to a plain shell otherwise.
 */
async function connectWebSocket() {
    if (webSocket && webSocket.readyState === WebSocket.OPEN) {
        return;
    }

    const sessionId = state.activeSessionId;
    const session = sessionId ? state.sessions?.get(sessionId) : null;

    // Don't try session terminal for pending sessions
    if (session && !session.pending && sessionId) {
        // Check if backend supports terminal mode before connecting
        try {
            const resp = await fetch('api/terminal/session/' + encodeURIComponent(sessionId) + '/supports-terminal');
            const data = await resp.json();
            if (data.supported) {
                connectSessionTerminal(sessionId, session);
                return;
            }
        } catch (e) {
            console.warn('Failed to check session terminal support:', e);
        }
    }

    // Fallback to plain shell
    connectPlainShell(session);
}

/**
 * Connect as a session-specific interactive terminal.
 */
function connectSessionTerminal(sessionId, session) {
    const wsUrl = new URL('ws/session-terminal/' + encodeURIComponent(sessionId), window.location.href);
    wsUrl.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

    activeTerminalSessionId = sessionId;
    resetKittyKeyboardState(kittyKeyboardState);

    // Clear terminal and show connecting message
    if (terminal) {
        terminal.clear();
        terminal.write('\x1b[2m[Connecting to session terminal...]\x1b[0m\r\n');
    }

    webSocket = new WebSocket(wsUrl.href);

    webSocket.onopen = () => {
        if (terminal) {
            terminal.clear();
        }
        // Send initial resize
        if (terminal && fitAddon) {
            fitAddon.fit();
            const { cols, rows } = terminal;
            webSocket.send(JSON.stringify({ type: 'resize', cols, rows }));
        }
    };

    webSocket.onmessage = handleWebSocketMessage;

    webSocket.onclose = (event) => {
        // Code 4005 = backend doesn't support terminal mode, fall back to shell
        if (event.code === 4005) {
            if (terminal) {
                terminal.clear();
            }
            connectPlainShell(session);
            return;
        }

        // Code 4001 = killed for transcript send, don't reconnect
        if (event.code === 4001) {
            activeTerminalSessionId = null;
            return;
        }

        if (state.terminalOpen && event.code !== 1000) {
            // Unexpected close - try to reconnect
            activeTerminalSessionId = null;
            setTimeout(() => {
                if (state.terminalOpen) {
                    terminal?.write('\r\n[Reconnecting...]\r\n');
                    connectWebSocket();
                }
            }, 2000);
        } else {
            activeTerminalSessionId = null;
        }
    };

    webSocket.onerror = (error) => {
        console.error('Session terminal WebSocket error:', error);
    };
}

/**
 * Connect as a plain shell terminal (fallback).
 */
function connectPlainShell(session) {
    let cwd = null;
    if (session?.cwd) {
        cwd = session.cwd;
    }

    const wsUrl = new URL('ws/terminal', window.location.href);
    wsUrl.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    if (cwd) {
        wsUrl.searchParams.set('cwd', cwd);
    }

    activeTerminalSessionId = null;
    resetKittyKeyboardState(kittyKeyboardState);
    webSocket = new WebSocket(wsUrl.href);

    webSocket.onopen = () => {
        // Send initial resize
        if (terminal && fitAddon) {
            fitAddon.fit();
            const { cols, rows } = terminal;
            webSocket.send(JSON.stringify({ type: 'resize', cols, rows }));
        }
    };

    webSocket.onmessage = handleWebSocketMessage;

    webSocket.onclose = (event) => {
        if (state.terminalOpen && event.code !== 1000) {
            setTimeout(() => {
                if (state.terminalOpen) {
                    terminal?.write('\r\n[Reconnecting...]\r\n');
                    connectWebSocket();
                }
            }, 2000);
        }
    };

    webSocket.onerror = (error) => {
        console.error('Terminal WebSocket error:', error);
    };
}

/**
 * Shared WebSocket message handler for both terminal modes.
 */
function handleWebSocketMessage(event) {
    try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'output' && terminal) {
            const { output, responses } = processKittyKeyboardProtocolOutput(kittyKeyboardState, msg.data);
            for (const response of responses) {
                if (webSocket && webSocket.readyState === WebSocket.OPEN) {
                    webSocket.send(JSON.stringify({ type: 'input', data: response }));
                }
            }
            if (output) {
                terminal.write(output);
            }
        } else if (msg.type === 'exit') {
            resetKittyKeyboardState(kittyKeyboardState);
            terminal?.write('\r\n[Process exited]\r\n');
        } else if (msg.type === 'killed') {
            resetKittyKeyboardState(kittyKeyboardState);
            terminal?.write('\r\n\x1b[2m[Terminal closed — switched to transcript mode]\x1b[0m\r\n');
        } else if (msg.type === 'error') {
            console.error('Terminal error:', msg.message);
            terminal?.write(`\r\n[Error: ${msg.message}]\r\n`);
        }
    } catch (e) {
        console.error('Failed to parse terminal message:', e);
    }
}

/**
 * Close terminal and disconnect WebSocket.
 */
function closeTerminal() {
    activeTerminalSessionId = null;
    resetKittyKeyboardState(kittyKeyboardState);
    if (webSocket) {
        webSocket.close(1000, 'User closed terminal');
        webSocket = null;
    }
}

/**
 * Kill the session terminal for the active session.
 *
 * Called by the messaging module before sending a message via transcript mode
 * so the terminal doesn't show stale state. The session can always be
 * re-opened later via --resume.
 *
 * Also switches back to transcript view.
 *
 * @returns {Promise<boolean>} True if a terminal was killed.
 */
export async function killSessionTerminal() {
    const sessionId = activeTerminalSessionId;
    if (!sessionId) return false;

    try {
        const response = await fetch(
            'api/terminal/session/' + encodeURIComponent(sessionId) + '/kill',
            { method: 'POST' }
        );
        const data = await response.json();

        if (data.killed) {
            // Clean up local state
            activeTerminalSessionId = null;
            if (webSocket) {
                webSocket = null;
            }

            // Switch back to transcript view
            state.terminalOpen = false;
            const toggleBtn = document.getElementById('terminal-toggle-btn');
            toggleBtn?.classList.remove('active');
            showTranscriptView();
        }

        return data.killed;
    } catch (e) {
        console.error('Failed to kill session terminal:', e);
        return false;
    }
}

/**
 * Check if a session terminal is currently active for the given session.
 */
export function hasActiveSessionTerminal(sessionId) {
    return activeTerminalSessionId === sessionId;
}

/**
 * Update terminal theme when page theme changes.
 */
export function updateTerminalTheme() {
    if (terminal) {
        terminal.options.theme = getTerminalTheme();
    }
}

/**
 * Check if terminal is available/enabled.
 */
export function isTerminalEnabled() {
    return terminalEnabled;
}

/**
 * No-op kept for API compatibility — the right pane layout is now
 * independent of the terminal state.
 */
export function updateRightPaneLayout() {
    // Terminal is in the main content area now, not the right pane.
    // This function is retained in case other modules call it.
}
