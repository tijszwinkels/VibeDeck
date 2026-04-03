/**
 * Terminal integration using xterm.js
 *
 * Supports two modes:
 *   - **Session terminal**: Runs an interactive CLI session (e.g. `claude --resume`)
 *     via the /ws/terminal/session/{id} endpoint. Activated when toggling terminal
 *     on a session whose backend supports it.
 *   - **Plain shell**: A regular shell PTY via /ws/terminal. Used as fallback when
 *     the backend doesn't support session terminals, or when no session is active.
 *
 * The terminal toggle works independently from the file view toggle (folder button):
 *   - File view only: right pane shows file tree + preview
 *   - Terminal only: right pane shows terminal filling the pane
 *   - Both: split view with file tree/preview on top, terminal on bottom
 *   - Neither: right pane is closed
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

    // Set up resize handle
    const resizeHandle = document.getElementById('terminal-resize-handle');
    if (resizeHandle) {
        initResizeHandle(resizeHandle);
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
 * Toggle terminal on/off independently from file view.
 */
export function toggleTerminal() {
    if (!terminalEnabled) return;

    state.terminalOpen = !state.terminalOpen;

    const toggleBtn = document.getElementById('terminal-toggle-btn');
    if (state.terminalOpen) {
        toggleBtn?.classList.add('active');
        openTerminal();
    } else {
        toggleBtn?.classList.remove('active');
        closeTerminal();
    }

    updateRightPaneLayout();
}

/**
 * Update the right pane layout based on which toggles are active.
 *
 * Central layout manager called by both file view toggle (folder button)
 * and terminal toggle. Determines:
 *   - Whether the right pane is open or closed
 *   - Which layout mode: file-only (default), terminal-only, or split
 */
export function updateRightPaneLayout() {
    const pane = dom.previewPane;
    if (!pane) return;

    const fileViewActive = state.previewPaneOpen;
    const terminalActive = state.terminalOpen;

    // Remove all layout mode classes
    pane.classList.remove('terminal-only', 'split-mode');

    if (!fileViewActive && !terminalActive) {
        // Neither active - close the right pane
        pane.classList.remove('open');
        dom.mainContent?.classList.remove('preview-open');
        dom.inputBar?.classList.remove('preview-open');
        dom.floatingControls?.classList.remove('preview-open');
    } else {
        // At least one is active - open the pane
        pane.classList.add('open');
        dom.mainContent?.classList.add('preview-open');
        dom.inputBar?.classList.add('preview-open');
        dom.floatingControls?.classList.add('preview-open');

        if (terminalActive && !fileViewActive) {
            pane.classList.add('terminal-only');
        } else if (terminalActive && fileViewActive) {
            pane.classList.add('split-mode');
        }
        // else: only fileView - default layout (no extra class needed)
    }

    // Refit terminal if visible
    if (terminalActive && terminal && fitAddon) {
        setTimeout(() => fitAddon.fit(), 100);
    }
}

/**
 * Open terminal and connect to WebSocket.
 * If a session is active and supports terminal mode, connects as a session terminal.
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
        fitAddon.fit();

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

    // Apply persisted height
    const panel = document.getElementById('terminal-panel');
    if (panel && state.terminalHeight) {
        panel.style.height = `${state.terminalHeight}px`;
    }

    resetKittyKeyboardState(kittyKeyboardState);
    connectWebSocket();

    // Focus terminal after layout settles
    setTimeout(() => terminal?.focus(), 100);
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
                // Don't try to close again - server already closed it
                webSocket = null;
            }

            // Close the terminal UI
            state.terminalOpen = false;
            const toggleBtn = document.getElementById('terminal-toggle-btn');
            toggleBtn?.classList.remove('active');
            updateRightPaneLayout();
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
 * Initialize resize handle for terminal panel.
 */
function initResizeHandle(handle) {
    let startY = 0;
    let startHeight = 0;

    handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        startY = e.clientY;
        const panel = document.getElementById('terminal-panel');
        startHeight = panel?.offsetHeight || 200;

        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
        document.body.style.cursor = 'ns-resize';
        document.body.style.userSelect = 'none';
    });

    function onMouseMove(e) {
        const delta = startY - e.clientY;
        const newHeight = Math.max(100, Math.min(window.innerHeight * 0.8, startHeight + delta));

        const panel = document.getElementById('terminal-panel');
        if (panel) {
            panel.style.height = `${newHeight}px`;
            state.terminalHeight = newHeight;
        }

        if (fitAddon) {
            fitAddon.fit();
        }
    }

    function onMouseUp() {
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        localStorage.setItem('terminalHeight', state.terminalHeight);
    }
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
