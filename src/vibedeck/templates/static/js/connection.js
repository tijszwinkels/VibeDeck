// Connection module - SSE event handling

import { dom, state, MAX_TITLE_LENGTH } from './state.js';
import { truncateTitle } from './utils.js';
import { updateStatus } from './ui.js';
import { checkSendEnabled, checkForkEnabled, checkDefaultSendBackend, updateInputBarUI } from './messaging.js';
import {
    createSession, removeSession, reorderSidebar, switchToSession,
    appendMessage, updateSessionWaitingState, updateSidebarItemStatusClass
} from './sessions.js';
import { showPermissionModal } from './permissions.js';
import { parseAndExecuteCommands, initCommandButtons } from './commands.js';

// Connect to SSE endpoint
export function connect() {
    updateStatus('reconnecting', 'Connecting...');
    state.catchupComplete = false;

    checkSendEnabled();
    checkForkEnabled();
    checkDefaultSendBackend();

    state.eventSource = new EventSource('/events');

    state.eventSource.addEventListener('sessions', function(e) {
        const data = JSON.parse(e.data);

        // Track which sessions the server knows about
        const serverSessionIds = new Set(data.sessions.map(function(s) { return s.id; }));

        // Remove sessions that are no longer tracked by the server
        // (but keep pending sessions as they're client-side only)
        const sessionsToRemove = [];
        state.sessions.forEach(function(session, sessionId) {
            if (!session.pending && !serverSessionIds.has(sessionId)) {
                sessionsToRemove.push(sessionId);
            }
        });
        sessionsToRemove.forEach(function(sessionId) {
            removeSession(sessionId);
        });

        // Clear unread/waiting badges for existing sessions on reconnect
        // (they'll be re-evaluated based on new messages after catchup)
        state.sessions.forEach(function(session) {
            session.sidebarItem.classList.remove('unread', 'waiting');
        });
        // Also clear project-level badges
        state.projects.forEach(function(project) {
            project.element.classList.remove('unread', 'waiting');
        });

        // Create or update sessions from server
        data.sessions.forEach(function(session) {
            createSession(
                session.id,
                session.name,
                session.projectName,
                session.firstMessage,
                session.startedAt,
                session.lastUpdatedAt,
                session.projectPath,
                session.tokenUsage,
                session.backend,
                session.summaryTitle,
                session.summaryShort,
                session.summaryExecutive,
                session.summaryBranch
            );
        });
        reorderSidebar();
        const count = data.sessions.length;
        const countText = count >= 100 ? 'last 100 sessions' : count + ' session' + (count !== 1 ? 's' : '');
        updateStatus('', 'Connected (' + countText + ')');
    });

    state.eventSource.addEventListener('catchup_complete', function(e) {
        state.catchupComplete = true;
        if (state.activeSessionId) {
            window.scrollTo(0, document.body.scrollHeight);
        }
    });

    state.eventSource.addEventListener('session_added', function(e) {
        const data = JSON.parse(e.data);

        let mergedPendingId = null;
        const MERGE_WINDOW_MS = 30000;
        for (const [sessionId, session] of state.sessions) {
            if (session.pending && session.starting &&
                (Date.now() - session.startedAt) < MERGE_WINDOW_MS) {
                mergedPendingId = sessionId;
                break;  // Stop after first match
            }
        }

        if (mergedPendingId) {
            const pendingSession = state.sessions.get(mergedPendingId);
            const wasPendingActive = (state.activeSessionId === mergedPendingId);
            const pendingCwd = pendingSession?.cwd;  // Preserve cwd before removal

            removeSession(mergedPendingId);

            const session = createSession(
                data.id,
                data.name,
                data.projectName,
                data.firstMessage,
                data.startedAt,
                data.lastUpdatedAt,
                data.projectPath || pendingCwd,  // Fallback to pending session's cwd
                data.tokenUsage,
                data.backend,
                data.summaryTitle,
                data.summaryShort,
                data.summaryExecutive,
                data.summaryBranch
            );

            if (wasPendingActive && session) {
                switchToSession(data.id, true);
            }
        } else {
            const session = createSession(
                data.id,
                data.name,
                data.projectName,
                data.firstMessage,
                data.startedAt,
                data.lastUpdatedAt,
                data.projectPath,
                data.tokenUsage,
                data.backend,
                data.summaryTitle,
                data.summaryShort,
                data.summaryExecutive,
                data.summaryBranch
            );
            if (state.autoSwitch && session) {
                switchToSession(data.id, true);
            }
        }
    });

    state.eventSource.addEventListener('session_removed', function(e) {
        const data = JSON.parse(e.data);
        removeSession(data.id);
    });

    state.eventSource.addEventListener('session_summary_updated', function(e) {
        const data = JSON.parse(e.data);
        const session = state.sessions.get(data.session_id);
        if (session) {
            // Update session summary data
            session.summaryTitle = data.summaryTitle;
            session.summaryShort = data.summaryShort;
            session.summaryExecutive = data.summaryExecutive;

            // Update display title if we have a summary title
            if (data.summaryTitle) {
                const newDisplayTitle = truncateTitle(data.summaryTitle, MAX_TITLE_LENGTH);
                session.displayTitle = newDisplayTitle;

                // Update sidebar item title
                const titleSpan = session.sidebarItem.querySelector('.session-title');
                if (titleSpan) {
                    titleSpan.textContent = newDisplayTitle;
                }

                // Update status color based on title suffix
                updateSidebarItemStatusClass(session.sidebarItem, data.summaryTitle);

                // Update session header title
                const headerTitleSpan = session.container.querySelector('.session-title-display');
                if (headerTitleSpan) {
                    headerTitleSpan.textContent = newDisplayTitle;
                }

                // Update title bar if this is the active session
                if (state.activeSessionId === data.session_id) {
                    dom.sessionTitleBar.textContent = newDisplayTitle;
                }
            }
        }
    });

    state.eventSource.addEventListener('message', function(e) {
        const data = JSON.parse(e.data);
        if (data.type === 'html' && data.session_id) {
            if (!state.sessions.has(data.session_id)) {
                createSession(data.session_id, data.session_id.substring(0, 8), 'Unknown', null, null, null, null, null, null);
            }

            // Parse VibeDeck commands and add execute buttons
            // Only auto-execute if catchup is complete (live streaming)
            const processedHtml = parseAndExecuteCommands(data.content, data.session_id, state.catchupComplete);

            if (processedHtml.trim()) {
                appendMessage(data.session_id, processedHtml);
            }
        }
    });

    state.eventSource.addEventListener('ping', function(e) {
        // Keep-alive
    });

    state.eventSource.addEventListener('session_status', function(e) {
        const data = JSON.parse(e.data);
        state.sessionStatus.set(data.session_id, {
            running: data.running,
            queued_messages: data.queued_messages,
            waiting_for_input: data.waiting_for_input
        });
        if (data.session_id === state.activeSessionId) {
            updateInputBarUI();
        }
        // Update waiting state on sidebar
        updateSessionWaitingState(data.session_id);
    });

    state.eventSource.addEventListener('permission_denied', function(e) {
        const data = JSON.parse(e.data);
        // Only show modal if this is for the active session
        if (data.session_id === state.activeSessionId) {
            showPermissionModal(data);
        } else {
            console.log('Permission denied for inactive session:', data.session_id);
        }
    });

    state.eventSource.addEventListener('reinitialize', function(e) {
        console.warn('Server requested reinitialize:', JSON.parse(e.data));
        state.eventSource.close();
        setTimeout(connect, 1000);
    });

    state.eventSource.onopen = function() {
        updateStatus('', 'Connected');
        // Reset catchupComplete on EVERY connection (including auto-reconnects)
        // This ensures catchup messages aren't treated as new messages
        // when EventSource auto-reconnects without calling connect()
        state.catchupComplete = false;
    };

    state.eventSource.onerror = function(e) {
        if (state.eventSource.readyState === EventSource.CLOSED) {
            updateStatus('disconnected', 'Disconnected');
        } else {
            updateStatus('reconnecting', 'Reconnecting...');
        }
    };
}

// Initialize visibility change handler
export function initVisibilityHandler() {
    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'visible' && state.eventSource.readyState === EventSource.CLOSED) {
            connect();
        }
    });
}
