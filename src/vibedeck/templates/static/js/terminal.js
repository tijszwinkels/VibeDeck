/**
 * Terminal integration using xterm.js
 *
 * Provides per-session embedded terminals in the right pane with WebSocket
 * connections to server-side PTYs. Each VibeDeck session gets its own
 * independent terminal instance with preserved scrollback.
 *
 * The terminal toggle works independently from the file view toggle:
 *   - File view only: right pane shows file tree + preview
 *   - Terminal only: right pane shows terminal filling the pane
 *   - Both: split view with file tree/preview on top, terminal on bottom
 *   - Neither: right pane is closed
 */

import { dom, state } from './state.js';

/**
 * Per-session terminal state.
 * Map<sessionId, { terminal, fitAddon, webSocket, containerEl }>
 */
const sessionTerminals = new Map();

let terminalEnabled = false;

/**
 * Get the terminal entry for the currently active session.
 * @returns {Object|null} Terminal entry or null if none exists
 */
function getActiveEntry() {
    if (!state.activeSessionId) return null;
    return sessionTerminals.get(state.activeSessionId) || null;
}

/**
 * Initialize the terminal module.
 * Checks if terminal is enabled and sets up event listeners.
 */
export async function initTerminal() {
    // Check if terminal feature is enabled
    try {
        const response = await fetch('/api/terminal/enabled');
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

    // Refit terminal on window resize
    window.addEventListener('resize', () => {
        if (state.terminalOpen) {
            const entry = getActiveEntry();
            if (entry?.fitAddon) {
                entry.fitAddon.fit();
            }
        }
    });

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
    if (terminalActive) {
        const entry = getActiveEntry();
        if (entry?.terminal && entry?.fitAddon) {
            setTimeout(() => entry.fitAddon.fit(), 100);
        }
    }
}

/**
 * Create a terminal for the specified session.
 * @param {string} sessionId - The session to create terminal for
 * @returns {Object} The created terminal entry
 */
function createTerminalForSession(sessionId) {
    const panel = dom.terminalPanel;
    if (!panel) {
        console.error('Terminal panel not found');
        return null;
    }

    // Create container element
    const containerEl = document.createElement('div');
    containerEl.className = 'terminal-container';
    containerEl.dataset.session = sessionId;
    panel.appendChild(containerEl);

    // Create xterm.js instance
    const terminal = new window.Terminal({
        cursorBlink: true,
        fontSize: 12,
        fontFamily: 'ui-monospace, "SF Mono", Menlo, Monaco, "Cascadia Mono", "Segoe UI Mono", "Roboto Mono", monospace',
        theme: getTerminalTheme(),
        allowProposedApi: true,
    });

    // Load addons
    const fitAddon = new window.FitAddon.FitAddon();
    terminal.loadAddon(fitAddon);

    const webLinksAddon = new window.WebLinksAddon.WebLinksAddon();
    terminal.loadAddon(webLinksAddon);

    // Open terminal in container
    terminal.open(containerEl);
    fitAddon.fit();

    // Create entry object (webSocket will be set by connectWebSocketForSession)
    const entry = { terminal, fitAddon, webSocket: null, containerEl };
    sessionTerminals.set(sessionId, entry);

    // Focus terminal when clicking on container
    containerEl.addEventListener('click', () => terminal.focus());

    // Handle terminal input -> WebSocket
    // Uses closure over entry so it always gets current webSocket after reconnects
    terminal.onData(data => {
        if (entry.webSocket && entry.webSocket.readyState === WebSocket.OPEN) {
            entry.webSocket.send(JSON.stringify({ type: 'input', data }));
        }
    });

    // Handle terminal resize -> WebSocket
    terminal.onResize(({ cols, rows }) => {
        if (entry.webSocket && entry.webSocket.readyState === WebSocket.OPEN) {
            entry.webSocket.send(JSON.stringify({ type: 'resize', cols, rows }));
        }
    });

    // Connect WebSocket
    connectWebSocketForSession(sessionId);

    return entry;
}

/**
 * Connect WebSocket for the specified session's terminal.
 * @param {string} sessionId - The session to connect WebSocket for
 */
function connectWebSocketForSession(sessionId) {
    const entry = sessionTerminals.get(sessionId);
    if (!entry) return;

    // Don't reconnect if already open
    if (entry.webSocket && entry.webSocket.readyState === WebSocket.OPEN) {
        return;
    }

    // Get working directory from session
    const session = state.sessions?.get(sessionId);
    const cwd = session?.cwd || null;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    let url = `${protocol}//${window.location.host}/ws/terminal`;
    const params = new URLSearchParams();
    if (cwd) {
        params.set('cwd', cwd);
    }
    params.set('session_id', sessionId);
    url += '?' + params.toString();

    const webSocket = new WebSocket(url);
    entry.webSocket = webSocket;

    webSocket.onopen = () => {
        // Send initial resize
        if (entry.terminal && entry.fitAddon) {
            entry.fitAddon.fit();
            const { cols, rows } = entry.terminal;
            webSocket.send(JSON.stringify({ type: 'resize', cols, rows }));
        }
    };

    webSocket.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === 'output' && entry.terminal) {
                entry.terminal.write(msg.data);
            } else if (msg.type === 'exit') {
                entry.terminal?.write('\r\n[Process exited]\r\n');
            } else if (msg.type === 'error') {
                console.error('Terminal error:', msg.message);
                entry.terminal?.write(`\r\n[Error: ${msg.message}]\r\n`);
            }
        } catch (e) {
            console.error('Failed to parse terminal message:', e);
        }
    };

    webSocket.onclose = (event) => {
        // Clear the reference
        if (entry.webSocket === webSocket) {
            entry.webSocket = null;
        }

        // Unexpected close - try to reconnect if terminal is still open
        if (state.terminalOpen && event.code !== 1000) {
            setTimeout(() => {
                // Only reconnect if session still exists and terminal panel is open
                if (state.terminalOpen && sessionTerminals.has(sessionId)) {
                    entry.terminal?.write('\r\n[Reconnecting...]\r\n');
                    connectWebSocketForSession(sessionId);
                }
            }, 2000);
        }
    };

    webSocket.onerror = (error) => {
        console.error('Terminal WebSocket error:', error);
    };
}

/**
 * Show only the terminal container for the specified session.
 * @param {string} sessionId - The session to show terminal for
 */
function showTerminalContainer(sessionId) {
    // Toggle active class on all terminal containers
    for (const [id, entry] of sessionTerminals) {
        entry.containerEl.classList.toggle('active', id === sessionId);
    }

    // Fit the active terminal after layout settles
    const entry = sessionTerminals.get(sessionId);
    if (entry?.fitAddon) {
        setTimeout(() => {
            entry.fitAddon.fit();
            entry.terminal?.focus();
        }, 100);
    }
}

/**
 * Open terminal for the current session.
 */
async function openTerminal() {
    const sessionId = state.activeSessionId;
    if (!sessionId) return;

    // Create terminal for this session if it doesn't exist
    if (!sessionTerminals.has(sessionId)) {
        createTerminalForSession(sessionId);
    }

    // Show this session's terminal container
    showTerminalContainer(sessionId);

    // Apply persisted height
    const panel = dom.terminalPanel;
    if (panel && state.terminalHeight) {
        panel.style.height = `${state.terminalHeight}px`;
    }

    // Focus terminal after layout settles
    const entry = sessionTerminals.get(sessionId);
    setTimeout(() => entry?.terminal?.focus(), 100);
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
 * Close terminal panel (but keep PTYs running in background).
 */
function closeTerminal() {
    // Just hide all containers - don't close WebSockets
    // PTYs keep running so scrollback is preserved
    for (const entry of sessionTerminals.values()) {
        entry.containerEl.classList.remove('active');
    }
}

/**
 * Switch the terminal view to a different session.
 * Called from sessions.js when user switches sessions.
 * @param {string} sessionId - The session to switch to
 */
export function switchTerminalToSession(sessionId) {
    if (!terminalEnabled || !state.terminalOpen) return;

    // Create terminal on demand if it doesn't exist
    if (!sessionTerminals.has(sessionId)) {
        createTerminalForSession(sessionId);
    }

    showTerminalContainer(sessionId);
}

/**
 * Destroy the terminal for a session (cleanup when session is removed).
 * @param {string} sessionId - The session to cleanup
 */
export function destroySessionTerminal(sessionId) {
    const entry = sessionTerminals.get(sessionId);
    if (!entry) return;

    // Close WebSocket gracefully
    if (entry.webSocket) {
        entry.webSocket.close(1000, 'Session removed');
    }

    // Dispose xterm.js instance (frees memory, detaches from DOM)
    entry.terminal.dispose();

    // Remove container from DOM
    entry.containerEl.remove();

    // Remove from map
    sessionTerminals.delete(sessionId);
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
        const panel = dom.terminalPanel;
        startHeight = panel?.offsetHeight || 200;

        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
        document.body.style.cursor = 'ns-resize';
        document.body.style.userSelect = 'none';
    });

    function onMouseMove(e) {
        const delta = startY - e.clientY;
        const newHeight = Math.max(100, Math.min(window.innerHeight * 0.8, startHeight + delta));

        const panel = dom.terminalPanel;
        if (panel) {
            panel.style.height = `${newHeight}px`;
            state.terminalHeight = newHeight;
        }

        // Fit the active terminal
        const entry = getActiveEntry();
        if (entry?.fitAddon) {
            entry.fitAddon.fit();
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
    const theme = getTerminalTheme();
    for (const entry of sessionTerminals.values()) {
        entry.terminal.options.theme = theme;
    }
}

/**
 * Check if terminal is available/enabled.
 */
export function isTerminalEnabled() {
    return terminalEnabled;
}
