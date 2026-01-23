// Messaging module - input bar, send/fork/interrupt functionality

import { dom, state } from './state.js';
import { isMobile, escapeHtml } from './utils.js';
import { showFlash, updateSidebarState } from './ui.js';
import { createPlaceholderMessage, switchToSession, setUpdateInputBarUI, setCreatePendingSession } from './sessions.js';

// Check if send is enabled
export async function checkSendEnabled() {
    try {
        const response = await fetch('/send-enabled');
        const data = await response.json();
        state.sendEnabled = data.enabled;
        if (state.sendEnabled) {
            dom.inputBar.classList.remove('hidden');
        } else {
            dom.inputBar.classList.add('hidden');
        }
    } catch (e) {
        console.error('Failed to check send-enabled:', e);
        state.sendEnabled = false;
    }
}

// Check if fork is enabled
export async function checkForkEnabled() {
    try {
        const response = await fetch('/fork-enabled');
        const data = await response.json();
        state.forkEnabled = data.enabled;
        updateForkButtonVisibility();
    } catch (e) {
        console.error('Failed to check fork-enabled:', e);
        state.forkEnabled = false;
    }
}

// Check default send backend
export async function checkDefaultSendBackend() {
    try {
        const response = await fetch('/default-send-backend');
        const data = await response.json();
        state.defaultSendBackend = data.backend;
    } catch (e) {
        console.error('Failed to check default-send-backend:', e);
        state.defaultSendBackend = null;
    }
}

function updateForkButtonVisibility() {
    // Only show fork button on desktop and when fork is enabled
    if (state.forkEnabled && !isMobile()) {
        dom.forkBtn.classList.remove('hidden');
    } else {
        dom.forkBtn.classList.add('hidden');
    }
}

export function updateInputBarUI() {
    const hasMessage = dom.messageInput.value.trim();

    // Update fork button visibility (desktop only, when enabled)
    updateForkButtonVisibility();

    if (!state.activeSessionId) {
        dom.sendBtn.disabled = true;
        dom.forkBtn.disabled = true;
        dom.inputStatus.textContent = '';
        dom.inputStatus.className = 'input-status';
        dom.sendBtn.classList.remove('hidden');
        dom.interruptBtn.classList.add('hidden');
        return;
    }

    const session = state.sessions.get(state.activeSessionId);
    const status = state.sessionStatus.get(state.activeSessionId) || { running: false, queued_messages: 0 };

    if (session && session.pending && session.starting) {
        dom.inputStatus.innerHTML = '<span class="spinner"></span> Starting session...';
        dom.inputStatus.className = 'input-status running';
        dom.sendBtn.classList.remove('hidden');
        dom.sendBtn.disabled = true;
        dom.forkBtn.disabled = true;
        dom.interruptBtn.classList.add('hidden');
        return;
    }

    if (status.running) {
        dom.inputStatus.className = 'input-status running';
        dom.sendBtn.classList.add('hidden');
        dom.interruptBtn.classList.remove('hidden');
        dom.forkBtn.disabled = !hasMessage;

        if (status.queued_messages > 0) {
            dom.inputStatus.innerHTML = '<span class="spinner"></span> ' +
                status.queued_messages + ' message' + (status.queued_messages > 1 ? 's' : '') + ' queued...';
        } else {
            dom.inputStatus.innerHTML = '<span class="spinner"></span> Claude is thinking...';
        }
    } else {
        dom.inputStatus.textContent = '';
        dom.inputStatus.className = 'input-status';
        dom.sendBtn.classList.remove('hidden');
        dom.interruptBtn.classList.add('hidden');
        dom.forkBtn.disabled = !hasMessage;
    }

    dom.sendBtn.disabled = !hasMessage;
}

// Register updateInputBarUI with sessions module
setUpdateInputBarUI(updateInputBarUI);

function autoResizeTextarea() {
    dom.messageInput.style.height = 'auto';
    dom.messageInput.style.height = Math.min(dom.messageInput.scrollHeight, 120) + 'px';
}

function getBackendForNewSession(pendingSession) {
    // Priority: pendingSession.selectedBackend -> defaultSendBackend -> current session's backend -> null
    if (pendingSession && pendingSession.selectedBackend) {
        return pendingSession.selectedBackend;
    }
    if (state.defaultSendBackend) return state.defaultSendBackend;
    if (state.activeSessionId) {
        const session = state.sessions.get(state.activeSessionId);
        if (session && session.backend) return session.backend;
    }
    return null;
}

async function startPendingSession(pendingSession, message) {
    dom.inputStatus.innerHTML = '<span class="spinner"></span> Starting session...';
    dom.inputStatus.className = 'input-status running';

    // Mark the pending session as starting BEFORE the fetch call
    // This ensures session_added SSE events can find and merge with it
    // even if the server takes time to respond
    pendingSession.starting = true;
    pendingSession.startedAt = Date.now();

    try {
        const backend = getBackendForNewSession(pendingSession);
        const modelIndex = pendingSession.selectedModelIndex;

        const requestBody = {
            message: message,
            cwd: pendingSession.cwd || null,
            backend: backend
        };

        // Only include model_index if it's set (some backends don't support it)
        if (modelIndex !== null && modelIndex !== undefined) {
            requestBody.model_index = modelIndex;
        }

        const response = await fetch('/sessions/new', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        const data = await response.json();

        if (response.ok) {
            if (data.status === 'permission_denied') {
                // Handle permission denial for new session
                // Reset starting flag since we need user input first
                pendingSession.starting = false;
                // Import and show permission modal with special handling for new sessions
                const { showPermissionModalForNewSession } = await import('./permissions.js');
                showPermissionModalForNewSession(data);
                dom.inputStatus.textContent = '';
                dom.inputStatus.className = 'input-status';
            } else {
                dom.messageInput.value = '';
                autoResizeTextarea();
                // starting flag already set above
            }
        } else {
            // Reset starting flag on error
            pendingSession.starting = false;
            alert('Error: ' + (data.detail || 'Failed to start session'));
            dom.inputStatus.textContent = '';
            dom.inputStatus.className = 'input-status';
        }
    } catch (e) {
        // Reset starting flag on error
        pendingSession.starting = false;
        alert('Error: Failed to start session');
        console.error('New session error:', e);
        dom.inputStatus.textContent = '';
        dom.inputStatus.className = 'input-status';
    }

    updateInputBarUI();
}

async function sendMessage() {
    const message = dom.messageInput.value.trim();
    if (!message || !state.activeSessionId) return;

    const session = state.sessions.get(state.activeSessionId);
    if (!session) return;

    dom.sendBtn.disabled = true;

    if (session.pending) {
        await startPendingSession(session, message);
        return;
    }

    const placeholder = createPlaceholderMessage(state.activeSessionId, message);
    dom.messageInput.value = '';
    autoResizeTextarea();

    try {
        const response = await fetch('/sessions/' + state.activeSessionId + '/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message })
        });

        if (!response.ok) {
            const data = await response.json();
            alert('Error: ' + (data.detail || 'Failed to send message'));
            if (placeholder) {
                placeholder.element.remove();
                const idx = state.pendingMessages.indexOf(placeholder);
                if (idx > -1) state.pendingMessages.splice(idx, 1);
            }
        }
    } catch (e) {
        alert('Error: Failed to send message');
        console.error('Send error:', e);
        if (placeholder) {
            placeholder.element.remove();
            const idx = state.pendingMessages.indexOf(placeholder);
            if (idx > -1) state.pendingMessages.splice(idx, 1);
        }
    }

    updateInputBarUI();
}

async function interruptSession() {
    if (!state.activeSessionId) return;

    try {
        const response = await fetch('/sessions/' + state.activeSessionId + '/interrupt', {
            method: 'POST'
        });

        if (response.ok) {
            showFlash('Session interrupted', 'success');
        } else {
            const data = await response.json();
            console.error('Interrupt failed:', data.detail);
            // Show user-friendly message based on error
            if (response.status === 409) {
                showFlash('Process not running yet, try again', 'warning');
            } else {
                showFlash('Failed to stop: ' + (data.detail || 'Unknown error'), 'error');
            }
        }
    } catch (e) {
        console.error('Interrupt error:', e);
        showFlash('Failed to stop session', 'error');
    }
}

async function forkMessage() {
    // Fork: create a new session with the conversation history using --fork-session
    const message = dom.messageInput.value.trim();
    if (!message || !state.activeSessionId) return;

    const session = state.sessions.get(state.activeSessionId);
    if (!session) return;

    // Don't allow forking pending sessions
    if (session.pending) {
        alert('Cannot fork a pending session. Send a message first to create the session.');
        return;
    }

    dom.forkBtn.disabled = true;
    dom.inputStatus.innerHTML = '<span class="spinner"></span> Forking session...';
    dom.inputStatus.className = 'input-status running';

    try {
        const response = await fetch('/sessions/' + state.activeSessionId + '/fork', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message })
        });

        if (response.ok) {
            dom.messageInput.value = '';
            autoResizeTextarea();
            dom.inputStatus.textContent = '';
            dom.inputStatus.className = 'input-status';
            // The new forked session will appear via SSE and auto-switch will handle it
        } else {
            const data = await response.json();
            alert('Error: ' + (data.detail || 'Failed to fork session'));
            dom.inputStatus.textContent = '';
            dom.inputStatus.className = 'input-status';
        }
    } catch (e) {
        alert('Error: Failed to fork session');
        console.error('Fork error:', e);
        dom.inputStatus.textContent = '';
        dom.inputStatus.className = 'input-status';
    }

    updateInputBarUI();
}

// Create a pending session
export function createPendingSession(cwd, projectName, backend, modelIndex, modelName) {
    // Import dynamically to avoid circular dependency issues at load time
    import('./sessions.js').then(({ createSession, switchToSession }) => {
        state.pendingSessionCounter++;
        const pendingId = 'pending-' + state.pendingSessionCounter;

        // Use provided cwd/projectName, or fall back to active session's project
        if (!cwd && state.activeSessionId) {
            const activeSession = state.sessions.get(state.activeSessionId);
            if (activeSession && activeSession.cwd) {
                cwd = activeSession.cwd;
                projectName = projectName || activeSession.projectName;
            }
        }

        const displayProjectName = projectName || 'New Session';
        const session = createSession(pendingId, 'New Session', displayProjectName, null, null, null, cwd, null, null);
        session.pending = true;
        session.cwd = cwd;
        session.selectedBackend = backend || null;
        session.selectedModelIndex = modelIndex;  // Integer index or null

        session.sidebarItem.classList.add('pending');
        const backendInfo = backend ? ' using <strong>' + escapeHtml(backend) + '</strong>' : '';
        const modelInfo = modelName ? ' (' + escapeHtml(modelName) + ')' : '';
        session.container.innerHTML = '<div class="pending-session-placeholder">' +
            '<p>Type a message below to start this session' +
            (cwd ? ' in <strong>' + escapeHtml(displayProjectName) + '</strong>' : '') +
            backendInfo + modelInfo +
            '</p></div>';

        switchToSession(pendingId);
        dom.messageInput.focus();

        // Close sidebar on mobile after selecting
        if (isMobile()) {
            state.sidebarOpen = false;
            updateSidebarState();
        }
    });
}

// Register createPendingSession with sessions module
setCreatePendingSession(createPendingSession);

// Initialize messaging event listeners
export function initMessaging() {
    dom.messageInput.addEventListener('input', function() {
        autoResizeTextarea();
        updateInputBarUI();
    });

    dom.messageInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (!dom.sendBtn.disabled && !dom.sendBtn.classList.contains('hidden')) {
                sendMessage();
            }
        }
    });

    // Handle file path drops from file browser
    dom.messageInput.addEventListener('dragover', function(e) {
        // Accept file paths from internal drag
        if (e.dataTransfer.types.includes('application/x-file-path') ||
            e.dataTransfer.types.includes('text/plain')) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
            dom.messageInput.classList.add('drop-target');
        }
    });

    dom.messageInput.addEventListener('dragleave', function(e) {
        dom.messageInput.classList.remove('drop-target');
    });

    dom.messageInput.addEventListener('drop', function(e) {
        dom.messageInput.classList.remove('drop-target');

        // Check for internal file path drag
        const filePath = e.dataTransfer.getData('application/x-file-path');
        if (filePath) {
            e.preventDefault();
            insertTextAtCursor(dom.messageInput, filePath);
            autoResizeTextarea();
            updateInputBarUI();
        }
    });

    dom.sendBtn.addEventListener('click', sendMessage);
    dom.forkBtn.addEventListener('click', forkMessage);
    dom.interruptBtn.addEventListener('click', interruptSession);
}

function insertTextAtCursor(textarea, text) {
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const before = textarea.value.substring(0, start);
    const after = textarea.value.substring(end);

    // Add space before if needed and not at start
    const needsSpaceBefore = start > 0 && before.charAt(before.length - 1) !== ' ' && before.charAt(before.length - 1) !== '\n';
    // Add space after if needed and not at end
    const needsSpaceAfter = after.length > 0 && after.charAt(0) !== ' ' && after.charAt(0) !== '\n';

    const textToInsert = (needsSpaceBefore ? ' ' : '') + text + (needsSpaceAfter ? ' ' : '');

    textarea.value = before + textToInsert + after;
    textarea.selectionStart = textarea.selectionEnd = start + textToInsert.length;
    textarea.focus();
}
