// Sessions module - session management, sidebar ordering

import { dom, state, MAX_MESSAGES, MAX_TITLE_LENGTH, dateCategoryLabels, dateCategoryOrder, archivedCategory } from './state.js';
import {
    escapeHtml, truncateTitle, isMobile, isNearBottom, copyToClipboard,
    getDateCategory, getSessionSortTimestamp, getSessionCategoryTimestamp,
    parseStatusFromTitle, getStatusClass, formatTokenCount, formatCost, formatModelName,
    formatShortTimestamp
} from './utils.js';
import {
    showTooltip, hideTooltip, positionTooltip, showDateCategoryTooltip,
    showMessageTooltip, hideMessageTooltip, positionMessageTooltip,
    updateSidebarState, updateUserNavButtons
} from './ui.js';
import { closePreviewPane, openPreviewPane } from './preview.js';
import { loadFileTree, setProjectRoot } from './filetree.js';
import { showProjectContextMenu, showSessionContextMenu } from './sidebar-context-menu.js';
import { extractArtifactsFromElement, onSessionChanged } from './artifacts.js';
import { parseAndExecuteCommands } from './commands.js';

// Forward declarations for circular dependency - will be set by other modules
let updateInputBarUI = () => {};
export function setUpdateInputBarUI(fn) {
    updateInputBarUI = fn;
}

// Forward declaration for createPendingSession - will be set by messaging.js
let createPendingSession = () => {};
export function setCreatePendingSession(fn) {
    createPendingSession = fn;
}

// Find a real (non-pending) session ID to use for file tree API calls
// when the current session is pending (not yet created on backend)
function findFallbackSessionId() {
    for (const [sessionId, session] of state.sessions) {
        if (!session.pending) {
            return sessionId;
        }
    }
    return null;
}

// Map persisted status value to CSS class
function getStatusClassFromPersistedValue(status) {
    switch (status) {
        case 'done': return 'status-done';
        case 'waiting': return 'status-waiting';
        case 'in_progress': return 'status-in-progress';
        default: return '';
    }
}

// Apply status to a sidebar item, updating visual state
function applyStatusToSidebarItem(sidebarItem, statusClass) {
    sidebarItem.classList.remove('status-done', 'status-waiting', 'status-in-progress');
    if (statusClass) {
        sidebarItem.classList.add(statusClass);
    }
}

export function updateSidebarItemStatusClass(sidebarItem, title, sessionId = null) {
    // Remove existing status classes
    sidebarItem.classList.remove('status-done', 'status-waiting', 'status-in-progress');

    // Check for persisted status first (takes priority over title-parsed status)
    if (sessionId) {
        const persistedStatus = state.sessionStatuses.get(sessionId);
        if (persistedStatus) {
            const statusClass = getStatusClassFromPersistedValue(persistedStatus);
            if (statusClass) {
                sidebarItem.classList.add(statusClass);
            }
            return;  // Don't fall through to title parsing
        }
    }

    // Fall back to parsing status from title
    const status = parseStatusFromTitle(title);
    const statusClass = getStatusClass(status);
    if (statusClass) {
        sidebarItem.classList.add(statusClass);
    }
}

export function getOrCreateProject(projectName, projectPath) {
    if (state.projects.has(projectName)) {
        const project = state.projects.get(projectName);
        // Update path if we have a better one
        if (projectPath && !project.path) {
            project.path = projectPath;
        }
        return project;
    }

    const projectItem = document.createElement('div');
    projectItem.className = 'project-item';

    // Build date sections HTML
    let dateSectionsHtml = '';
    dateCategoryOrder.forEach(function(category) {
        const isOpen = category === 'today';
        dateSectionsHtml += `
            <div class="date-section${isOpen ? '' : ' collapsed'}" data-category="${category}">
                <div class="date-divider">
                    <span class="date-label">${dateCategoryLabels[category]}</span>
                </div>
                <div class="date-session-list"></div>
            </div>
        `;
    });

    projectItem.innerHTML = `
        <div class="project-header">
            <span class="project-icon">&#9660;</span>
            <span class="unread-dot"></span>
            <span class="project-name">${escapeHtml(projectName)}</span>
            <button class="project-new-btn" title="New session in ${escapeHtml(projectName)}">+</button>
        </div>
        <div class="session-list">${dateSectionsHtml}</div>
    `;

    const projectHeader = projectItem.querySelector('.project-header');
    const projectNewBtn = projectItem.querySelector('.project-new-btn');

    projectHeader.addEventListener('click', function(e) {
        // Don't toggle when clicking the new button
        if (e.target === projectNewBtn) return;
        projectItem.classList.toggle('collapsed');
        // Track if user manually expanded, so we don't auto-collapse
        if (!projectItem.classList.contains('collapsed')) {
            projectItem.classList.add('user-expanded');
        } else {
            projectItem.classList.remove('user-expanded');
        }
    });

    projectNewBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        createPendingSession(projectPath || null, projectName);
    });

    // Right-click context menu for project header
    projectHeader.addEventListener('contextmenu', function(e) {
        showProjectContextMenu(e, projectPath, projectName);
    });

    // Date section toggle handlers and tooltips
    projectItem.querySelectorAll('.date-divider').forEach(function(divider) {
        divider.addEventListener('click', function(e) {
            e.stopPropagation();
            const section = divider.parentElement;
            section.classList.toggle('collapsed');
        });
        // Add tooltip handlers for date sections
        divider.addEventListener('mouseenter', function(e) {
            const section = divider.parentElement;
            const category = section.dataset.category;
            showDateCategoryTooltip(projectName, category, e);
        });
        divider.addEventListener('mouseleave', hideTooltip);
        divider.addEventListener('mousemove', positionTooltip);
    });

    dom.projectListContainer.appendChild(projectItem);

    // Build dateSections map
    const dateSections = {};
    dateCategoryOrder.forEach(function(category) {
        const section = projectItem.querySelector(`.date-section[data-category="${category}"]`);
        dateSections[category] = {
            element: section,
            listElement: section.querySelector('.date-session-list')
        };
    });

    const project = {
        name: projectName,
        path: projectPath || null,
        sessions: new Set(),
        lastActivity: 0,
        element: projectItem,
        sessionListElement: projectItem.querySelector('.session-list'),
        dateSections: dateSections
    };
    state.projects.set(projectName, project);

    return project;
}

export function createSession(sessionId, name, projectName, firstMessage, startedAt, lastUpdatedAt, projectPath, tokenUsage, backend, summaryTitle, summaryShort, summaryExecutive, summaryBranch) {
    if (state.sessions.has(sessionId)) return state.sessions.get(sessionId);

    // Create container with session header
    const container = document.createElement('div');
    container.className = 'session-container';
    container.dataset.session = sessionId;
    if (backend) container.dataset.backend = backend;

    // Use summary title if available, otherwise fall back to firstMessage or name
    const fullTitle = summaryTitle || firstMessage || name;
    const displayTitle = truncateTitle(fullTitle, MAX_TITLE_LENGTH);

    // Add session header (title only - auto-scroll is in floating controls)
    const sessionHeader = document.createElement('div');
    sessionHeader.className = 'session-header';
    sessionHeader.innerHTML = `<span class="session-title-display">${escapeHtml(displayTitle)}</span>`;
    container.appendChild(sessionHeader);

    dom.sessionsContainer.appendChild(container);

    // Create sidebar item
    const sidebarItem = document.createElement('div');
    sidebarItem.className = 'session-item';
    sidebarItem.dataset.session = sessionId;
    if (backend) sidebarItem.dataset.backend = backend;
    if (projectPath) sidebarItem.dataset.cwd = projectPath;

    // Determine which timestamp to show (opposite of sort order)
    // When sorting by created, show modified time; when by modified, show created time
    const displayTimestamp = state.sortBy === 'created' ? lastUpdatedAt : startedAt;
    const formattedTime = formatShortTimestamp(displayTimestamp);

    // Build branch+time display (right side of project row in session-mode)
    const branchDisplay = summaryBranch || '';
    const separator = '';
    const branchTimeHtml = (branchDisplay || formattedTime) ? `
        <span class="session-meta">
            <span class="session-branch">${escapeHtml(branchDisplay)}${separator}</span>
            <span class="session-meta-time">${escapeHtml(formattedTime)}</span>
        </span>
    ` : '';

    sidebarItem.innerHTML = `
        <span class="unread-dot"></span>
        <span class="session-title">${escapeHtml(fullTitle)}</span>
        <span class="session-project-row">
            <button class="new-in-folder-btn" title="New session in same folder">+</button>
            <span class="session-project">${escapeHtml(projectName || 'Unknown')}</span>
            ${branchTimeHtml}
        </span>
        <span class="close-btn" title="Close">&times;</span>
    `;

    // Get close button for use below
    const closeBtn = sidebarItem.querySelector('.close-btn');

    // Apply status color based on title suffix (e.g., "- Merged", "- Waiting for Input")
    updateSidebarItemStatusClass(sidebarItem, summaryTitle, sessionId);

    sidebarItem.addEventListener('click', function(e) {
        if (e.target.classList.contains('close-btn')) {
            const session = state.sessions.get(sessionId);
            if (session && session.archived) {
                // Check if archived via project or individually
                if (session.cwd && state.archivedProjectPaths.has(session.cwd)) {
                    // Archived via project - unarchive project but keep other sessions archived
                    unarchiveProjectButKeepOthersArchived(session.cwd, sessionId);
                } else {
                    // Individually archived - just unarchive this session
                    unarchiveSession(sessionId);
                }
            } else {
                archiveSession(sessionId);
            }
        } else if (e.target.classList.contains('new-in-folder-btn')) {
            // Create new session in same folder (same as project header "+" button)
            const cwd = sidebarItem.dataset.cwd;
            createPendingSession(cwd || null, projectName);
        } else {
            // Check if this session is in an archived project
            const session = state.sessions.get(sessionId);
            if (session && session.cwd && state.archivedProjectPaths.has(session.cwd)) {
                // Unarchive project but archive other sessions in it
                unarchiveProjectButKeepOthersArchived(session.cwd, sessionId);
            }
            switchToSession(sessionId, true);  // Scroll to bottom when opening
            // Close sidebar on mobile
            if (isMobile()) {
                state.sidebarOpen = false;
                updateSidebarState();
            }
        }
    });

    // Tooltip handlers
    sidebarItem.addEventListener('mouseenter', function(e) {
        showTooltip(sessionId, e);
    });
    sidebarItem.addEventListener('mouseleave', hideTooltip);
    sidebarItem.addEventListener('mousemove', function(e) {
        positionTooltip(e);
    });

    // Right-click context menu for session
    sidebarItem.addEventListener('contextmenu', function(e) {
        showSessionContextMenu(e, sessionId);
    });

    // Add to project in appropriate date section
    const project = getOrCreateProject(projectName || 'Unknown', projectPath);
    project.sessions.add(sessionId);

    // Determine date category and add to correct section (respecting sort mode)
    const categoryTimestamp = state.sortBy === 'created' ? startedAt : lastUpdatedAt;
    const dateCategory = getDateCategory(categoryTimestamp);
    const dateSection = project.dateSections[dateCategory];
    if (dateSection) {
        dateSection.listElement.appendChild(sidebarItem);
    }

    // Check if this session is archived (either individually or via project)
    const isArchived = state.archivedSessionIds.has(sessionId) ||
                       (projectPath && state.archivedProjectPaths.has(projectPath));
    if (isArchived) {
        sidebarItem.classList.add('archived');
        closeBtn.title = 'Unarchive';
    }

    const session = {
        id: sessionId,
        name: name,
        projectName: projectName || 'Unknown',
        displayTitle: displayTitle,
        firstMessage: firstMessage,
        container: container,
        sidebarItem: sidebarItem,
        messageCount: 0,
        lastActivity: lastUpdatedAt ? lastUpdatedAt * 1000 : Date.now(),
        startedAt: startedAt || null,
        lastUpdatedAt: lastUpdatedAt || null,
        cwd: projectPath || null,
        tokenUsage: tokenUsage || null,
        backend: backend || null,
        // Summary data (may be null if no summary file exists yet)
        summaryTitle: summaryTitle || null,
        summaryShort: summaryShort || null,
        summaryExecutive: summaryExecutive || null,
        summaryBranch: summaryBranch || null,
        archived: isArchived,
        // Lazy loading state
        loaded: false,
        loading: false,
        unreadCount: 0
    };
    state.sessions.set(sessionId, session);

    // Update project's last activity
    project.lastActivity = Math.max(project.lastActivity, session.lastActivity);

    // If this is the first session and it's not archived, activate it
    // Otherwise find first non-archived session
    if (state.sessions.size === 1 && !isArchived) {
        switchToSession(sessionId);
    } else if (state.sessions.size === 1 && isArchived) {
        // First session is archived, don't auto-activate
        state.activeSessionId = null;
    }

    return session;
}

// Remove session completely from UI (used when server reports session no longer exists)
export function removeSession(sessionId) {
    const session = state.sessions.get(sessionId);
    if (!session) return;

    // Remove from project
    const project = state.projects.get(session.projectName);
    if (project) {
        project.sessions.delete(sessionId);
        // Remove project if empty
        if (project.sessions.size === 0) {
            project.element.remove();
            state.projects.delete(session.projectName);
        }
    }

    // Also remove from archived if it was there
    state.archivedSessionIds.delete(sessionId);

    session.container.remove();
    session.sidebarItem.remove();
    state.sessions.delete(sessionId);

    if (state.activeSessionId === sessionId) {
        const firstSession = state.sessions.keys().next().value;
        if (firstSession) {
            switchToSession(firstSession);
        } else {
            state.activeSessionId = null;
        }
    }
}

export async function archiveSession(sessionId) {
    const session = state.sessions.get(sessionId);
    if (!session) return;

    // Add to archived set
    state.archivedSessionIds.add(sessionId);

    // Mark session as archived and reorder sidebar
    session.archived = true;
    session.sidebarItem.classList.add('archived');
    updateCloseBtnTitleForSession(sessionId);
    reorderSidebar();

    // If this was the active session, switch to another
    const wasActive = state.activeSessionId === sessionId;
    if (wasActive) {
        // Find first non-archived session
        for (const [id, s] of state.sessions) {
            if (!state.archivedSessionIds.has(id)) {
                switchToSession(id);
                break;
            }
        }
    }

    // Persist to server - rollback on failure
    try {
        const response = await fetch('/api/archived-sessions/archive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId })
        });
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }
    } catch (err) {
        console.error('Failed to archive session:', err);
        // Rollback state
        state.archivedSessionIds.delete(sessionId);
        session.archived = false;
        session.sidebarItem.classList.remove('archived');
        updateCloseBtnTitleForSession(sessionId);
        reorderSidebar();
        if (wasActive) {
            switchToSession(sessionId);
        }
    }
}

export async function unarchiveSession(sessionId) {
    const session = state.sessions.get(sessionId);
    if (!session) return;

    // Remove from archived set
    state.archivedSessionIds.delete(sessionId);

    // Mark session as not archived and reorder sidebar
    session.archived = false;
    session.sidebarItem.classList.remove('archived');
    updateCloseBtnTitleForSession(sessionId);
    reorderSidebar();

    // Persist to server - rollback on failure
    try {
        const response = await fetch('/api/archived-sessions/unarchive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId })
        });
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }
    } catch (err) {
        console.error('Failed to unarchive session:', err);
        // Rollback state
        state.archivedSessionIds.add(sessionId);
        session.archived = true;
        session.sidebarItem.classList.add('archived');
        updateCloseBtnTitleForSession(sessionId);
        reorderSidebar();
    }
}

// Load archived sessions from server on init
export async function loadArchivedSessions() {
    try {
        const response = await fetch('/api/archived-sessions');
        const data = await response.json();
        state.archivedSessionIds = new Set(data.archived || []);

        // Mark already-loaded sessions as archived
        for (const sessionId of state.archivedSessionIds) {
            const session = state.sessions.get(sessionId);
            if (session) {
                session.archived = true;
                session.sidebarItem.classList.add('archived');
                updateCloseBtnTitleForSession(sessionId);
            }
        }
    } catch (err) {
        console.error('Failed to load archived sessions:', err);
    }
}

// Load session statuses from server on init
export async function loadSessionStatuses() {
    try {
        const response = await fetch('/api/session-statuses');
        const data = await response.json();
        state.sessionStatuses = new Map(Object.entries(data.statuses || {}));

        // Apply statuses to already-loaded sessions
        for (const [sessionId, status] of state.sessionStatuses) {
            const session = state.sessions.get(sessionId);
            if (session) {
                const statusClass = getStatusClassFromPersistedValue(status);
                applyStatusToSidebarItem(session.sidebarItem, statusClass);
            }
        }
    } catch (err) {
        console.error('Failed to load session statuses:', err);
    }
}

// Set session status (with optimistic update and rollback)
export async function setSessionStatus(sessionId, status) {
    const session = state.sessions.get(sessionId);
    if (!session) return;

    // Store previous state for rollback
    const previousStatus = state.sessionStatuses.get(sessionId);

    // Optimistic update
    if (status === null) {
        state.sessionStatuses.delete(sessionId);
    } else {
        state.sessionStatuses.set(sessionId, status);
    }

    // Apply visual update
    const statusClass = status ? getStatusClassFromPersistedValue(status) : '';
    applyStatusToSidebarItem(session.sidebarItem, statusClass);

    // If clearing status, fall back to title-parsed status
    if (!status && session.summaryTitle) {
        updateSidebarItemStatusClass(session.sidebarItem, session.summaryTitle, sessionId);
    }

    // Persist to server
    try {
        const response = await fetch('/api/session-statuses/set', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId, status: status })
        });
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }
    } catch (err) {
        console.error('Failed to set session status:', err);
        // Rollback state
        if (previousStatus === undefined) {
            state.sessionStatuses.delete(sessionId);
        } else {
            state.sessionStatuses.set(sessionId, previousStatus);
        }
        // Rollback visual state
        const rollbackClass = previousStatus ? getStatusClassFromPersistedValue(previousStatus) : '';
        applyStatusToSidebarItem(session.sidebarItem, rollbackClass);
        if (!previousStatus && session.summaryTitle) {
            updateSidebarItemStatusClass(session.sidebarItem, session.summaryTitle, sessionId);
        }
    }
}

// Load archived projects from server on init
export async function loadArchivedProjects() {
    try {
        const response = await fetch('/api/archived-projects');
        const data = await response.json();
        state.archivedProjectPaths = new Set(data.archived_projects || []);

        // Mark sessions from archived projects as archived
        for (const [sessionId, session] of state.sessions) {
            if (isSessionInArchivedProject(session)) {
                session.archived = true;
                session.sidebarItem.classList.add('archived');
                updateCloseBtnTitleForSession(sessionId);
            }
        }
    } catch (err) {
        console.error('Failed to load archived projects:', err);
    }
}

// Check if a session belongs to an archived project
export function isSessionInArchivedProject(session) {
    if (!session.cwd) return false;
    return state.archivedProjectPaths.has(session.cwd);
}

// Archive a project (with optimistic update and rollback)
export async function archiveProject(projectPath) {
    if (!projectPath) return;

    // Store previous state for rollback
    const wasArchived = state.archivedProjectPaths.has(projectPath);

    // Optimistic update
    state.archivedProjectPaths.add(projectPath);

    // Mark all sessions in this project as archived
    const affectedSessions = [];
    for (const [sessionId, session] of state.sessions) {
        if (session.cwd === projectPath && !state.archivedSessionIds.has(sessionId)) {
            affectedSessions.push({ sessionId, wasArchived: session.archived });
            session.archived = true;
            session.sidebarItem.classList.add('archived');
            updateCloseBtnTitleForSession(sessionId);
        }
    }
    reorderSidebar();

    // Persist to server
    try {
        const response = await fetch('/api/archived-projects/archive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_path: projectPath })
        });
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }
    } catch (err) {
        console.error('Failed to archive project:', err);
        // Rollback state
        if (!wasArchived) {
            state.archivedProjectPaths.delete(projectPath);
        }
        // Rollback session states
        for (const { sessionId, wasArchived } of affectedSessions) {
            const session = state.sessions.get(sessionId);
            if (session) {
                session.archived = wasArchived;
                if (!wasArchived) {
                    session.sidebarItem.classList.remove('archived');
                }
                updateCloseBtnTitleForSession(sessionId);
            }
        }
        reorderSidebar();
    }
}

// Unarchive a project (with optimistic update and rollback)
export async function unarchiveProject(projectPath) {
    if (!projectPath) return;

    // Store previous state for rollback
    const wasArchived = state.archivedProjectPaths.has(projectPath);
    if (!wasArchived) return;

    // Optimistic update
    state.archivedProjectPaths.delete(projectPath);

    // Unarchive sessions in this project (unless individually archived)
    const affectedSessions = [];
    for (const [sessionId, session] of state.sessions) {
        if (session.cwd === projectPath && !state.archivedSessionIds.has(sessionId)) {
            affectedSessions.push({ sessionId, wasArchived: session.archived });
            session.archived = false;
            session.sidebarItem.classList.remove('archived');
            updateCloseBtnTitleForSession(sessionId);
        }
    }
    reorderSidebar();

    // Persist to server
    try {
        const response = await fetch('/api/archived-projects/unarchive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_path: projectPath })
        });
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }
    } catch (err) {
        console.error('Failed to unarchive project:', err);
        // Rollback state
        state.archivedProjectPaths.add(projectPath);
        // Rollback session states
        for (const { sessionId, wasArchived } of affectedSessions) {
            const session = state.sessions.get(sessionId);
            if (session) {
                session.archived = wasArchived;
                if (wasArchived) {
                    session.sidebarItem.classList.add('archived');
                }
                updateCloseBtnTitleForSession(sessionId);
            }
        }
        reorderSidebar();
    }
}

// Unarchive a project but keep other sessions in it archived individually
// This is used when clicking on a session from an archived project
export async function unarchiveProjectButKeepOthersArchived(projectPath, clickedSessionId) {
    if (!projectPath) return;

    // Only proceed if project is actually archived
    if (!state.archivedProjectPaths.has(projectPath)) return;

    // Find all sessions in this project
    const sessionsInProject = [];
    for (const [sessionId, session] of state.sessions) {
        if (session.cwd === projectPath) {
            sessionsInProject.push(sessionId);
        }
    }

    // Archive all other sessions in the project individually (except the clicked one)
    const sessionsToArchive = sessionsInProject.filter(id => id !== clickedSessionId);

    // First, archive all other sessions individually
    for (const sessionId of sessionsToArchive) {
        if (!state.archivedSessionIds.has(sessionId)) {
            state.archivedSessionIds.add(sessionId);
            const session = state.sessions.get(sessionId);
            if (session) {
                session.archived = true;
                session.sidebarItem.classList.add('archived');
                updateCloseBtnTitleForSession(sessionId);
            }
        }
    }

    // Persist the session archives to server
    for (const sessionId of sessionsToArchive) {
        try {
            await fetch('/api/archived-sessions/archive', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sessionId })
            });
        } catch (err) {
            console.error('Failed to archive session:', sessionId, err);
        }
    }

    // Now unarchive the project
    state.archivedProjectPaths.delete(projectPath);

    // Unarchive the clicked session
    state.archivedSessionIds.delete(clickedSessionId);
    const clickedSession = state.sessions.get(clickedSessionId);
    if (clickedSession) {
        clickedSession.archived = false;
        clickedSession.sidebarItem.classList.remove('archived');
        updateCloseBtnTitleForSession(clickedSessionId);
    }

    reorderSidebar();

    // Persist project unarchive to server
    try {
        const response = await fetch('/api/archived-projects/unarchive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_path: projectPath })
        });
        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }
    } catch (err) {
        console.error('Failed to unarchive project:', err);
        // Note: We don't fully rollback here as the session archives are already persisted
        // This is a best-effort operation
    }
}

// Helper function to update close button title for a session
function updateCloseBtnTitleForSession(sessionId) {
    const session = state.sessions.get(sessionId);
    if (!session) return;

    const closeBtn = session.sidebarItem.querySelector('.close-btn');
    if (closeBtn) {
        closeBtn.title = session.archived ? 'Unarchive' : 'Close';
    }
}

// Create top-level date sections for session mode
function createDateSections() {
    if (state.dateSections) return state.dateSections;

    state.dateSections = new Map();

    // Include archived category at the end
    const allCategories = [...dateCategoryOrder, archivedCategory];

    allCategories.forEach(function(category) {
        const section = document.createElement('div');
        // Archived section is always collapsed by default
        const isCollapsed = category !== 'today';
        section.className = 'date-section' + (isCollapsed ? ' collapsed' : '');
        if (category === archivedCategory) {
            section.className += ' archived-section';
        }
        section.dataset.category = category;
        section.innerHTML = `
            <div class="date-divider">
                <span class="date-label">${dateCategoryLabels[category]}</span>
            </div>
            <div class="date-session-list"></div>
        `;

        // Toggle handler
        section.querySelector('.date-divider').addEventListener('click', function(e) {
            e.stopPropagation();
            section.classList.toggle('collapsed');
        });

        // Tooltip handler
        section.querySelector('.date-divider').addEventListener('mouseenter', function(e) {
            showDateCategoryTooltip(null, category, e);
        });
        section.querySelector('.date-divider').addEventListener('mouseleave', hideTooltip);
        section.querySelector('.date-divider').addEventListener('mousemove', positionTooltip);

        state.dateSections.set(category, {
            element: section,
            listElement: section.querySelector('.date-session-list')
        });
    });

    return state.dateSections;
}

export function reorderSidebar() {
    if (state.groupBy === 'session') {
        reorderSidebarSessionMode();
    } else {
        reorderSidebarProjectMode();
    }
}

function reorderSidebarSessionMode() {
    const dateSections = createDateSections();

    // Ensure session-mode class and date sections are in DOM
    dom.projectListContainer.classList.add('session-mode');
    dateSections.forEach(function(section) {
        if (!section.element.parentElement) {
            dom.projectListContainer.appendChild(section.element);
        }
        section.element.style.display = '';
    });

    // Hide project items
    state.projects.forEach(function(project) {
        project.element.style.display = 'none';
    });

    // FLIP animation: capture positions before reorder
    const sessionPositions = new Map();
    state.sessions.forEach(function(session) {
        sessionPositions.set(session.id, session.sidebarItem.getBoundingClientRect());
    });

    // Group sessions by date category (with archived as separate category)
    const sessionsByCategory = {};
    dateCategoryOrder.forEach(function(cat) { sessionsByCategory[cat] = []; });
    sessionsByCategory[archivedCategory] = [];

    state.sessions.forEach(function(session) {
        // Archived sessions go to archived category regardless of date
        // Sessions are archived if individually archived OR in an archived project
        const isArchived = state.archivedSessionIds.has(session.id) ||
                           (session.cwd && state.archivedProjectPaths.has(session.cwd));
        if (isArchived) {
            sessionsByCategory[archivedCategory].push(session);
        } else {
            const category = getDateCategory(getSessionCategoryTimestamp(session, state.sortBy));
            sessionsByCategory[category].push(session);
        }
        session.sidebarItem.classList.add('session-mode');
    });

    // Sort and place sessions within each date category (including archived)
    const allCategories = [...dateCategoryOrder, archivedCategory];
    allCategories.forEach(function(category) {
        const dateSection = dateSections.get(category);
        const categorySessions = sessionsByCategory[category]
            .sort((a, b) => getSessionSortTimestamp(b, state.sortBy) - getSessionSortTimestamp(a, state.sortBy));

        if (categorySessions.length === 0) {
            dateSection.element.classList.add('empty');
        } else {
            dateSection.element.classList.remove('empty');
            categorySessions.forEach(function(session) {
                dateSection.listElement.appendChild(session.sidebarItem);
            });
        }
    });

    // FLIP animation: animate from old to new positions
    state.sessions.forEach(function(session) {
        const oldRect = sessionPositions.get(session.id);
        const newRect = session.sidebarItem.getBoundingClientRect();
        if (oldRect && Math.abs(oldRect.top - newRect.top) > 1) {
            const deltaY = oldRect.top - newRect.top;
            session.sidebarItem.style.transform = `translateY(${deltaY}px)`;
            session.sidebarItem.style.transition = 'none';
            requestAnimationFrame(function() {
                session.sidebarItem.style.transition = 'transform 0.2s ease-out';
                session.sidebarItem.style.transform = '';
            });
        }
    });
}

function reorderSidebarProjectMode() {
    // Ensure date sections exist (needed for archived section)
    const dateSections = createDateSections();

    // Remove session-mode classes
    dom.projectListContainer.classList.remove('session-mode');
    state.sessions.forEach(function(session) {
        session.sidebarItem.classList.remove('session-mode');
    });

    // Hide top-level date sections (but keep archived visible at the end)
    dateSections.forEach(function(section, category) {
        if (category === archivedCategory) {
            // Archived section will be shown if it has items (handled below)
            if (!section.element.parentElement) {
                dom.projectListContainer.appendChild(section.element);
            }
        } else {
            section.element.style.display = 'none';
        }
    });

    // Show project items
    state.projects.forEach(function(project) {
        project.element.style.display = '';
    });

    // FLIP animation: capture positions before reorder
    const projectPositions = new Map();
    const sessionPositions = new Map();

    state.projects.forEach(function(project) {
        projectPositions.set(project.name, project.element.getBoundingClientRect());
    });
    state.sessions.forEach(function(session) {
        sessionPositions.set(session.id, session.sidebarItem.getBoundingClientRect());
    });

    // Collect archived sessions separately
    const archivedSessions = [];

    // Helper to check if a session is archived (individually or via project)
    function isSessionArchived(s) {
        return state.archivedSessionIds.has(s.id) ||
               (s.cwd && state.archivedProjectPaths.has(s.cwd));
    }

    // Sort projects by most recent non-archived session (based on sort mode)
    const sortedProjects = Array.from(state.projects.values()).sort(function(a, b) {
        const aMax = Array.from(a.sessions)
            .map(id => state.sessions.get(id))
            .filter(s => s && !isSessionArchived(s))
            .reduce((max, s) => Math.max(max, getSessionSortTimestamp(s, state.sortBy)), 0);
        const bMax = Array.from(b.sessions)
            .map(id => state.sessions.get(id))
            .filter(s => s && !isSessionArchived(s))
            .reduce((max, s) => Math.max(max, getSessionSortTimestamp(s, state.sortBy)), 0);
        return bMax - aMax;
    });

    sortedProjects.forEach(function(project) {
        dom.projectListContainer.appendChild(project.element);

        // Group sessions by date category (excluding archived)
        const sessionsByCategory = {};
        dateCategoryOrder.forEach(function(cat) { sessionsByCategory[cat] = []; });

        Array.from(project.sessions)
            .map(id => state.sessions.get(id))
            .filter(s => s)
            .forEach(function(session) {
                if (isSessionArchived(session)) {
                    archivedSessions.push(session);
                } else {
                    const category = getDateCategory(getSessionCategoryTimestamp(session, state.sortBy));
                    sessionsByCategory[category].push(session);
                }
            });

        // Sort and place sessions within each date category
        dateCategoryOrder.forEach(function(category) {
            const dateSection = project.dateSections[category];
            const categorySessions = sessionsByCategory[category]
                .sort((a, b) => getSessionSortTimestamp(b, state.sortBy) - getSessionSortTimestamp(a, state.sortBy));

            // Show/hide date section based on whether it has sessions
            if (categorySessions.length === 0) {
                dateSection.element.classList.add('empty');
            } else {
                dateSection.element.classList.remove('empty');
                categorySessions.forEach(function(session) {
                    dateSection.listElement.appendChild(session.sidebarItem);
                });
            }
        });

        // Collapse project if no non-archived sessions in "today"
        const todaySessions = sessionsByCategory['today'];
        if (todaySessions.length === 0 && !project.element.classList.contains('user-expanded')) {
            project.element.classList.add('collapsed');
        }
    });

    // Place archived sessions in the archived section
    const archivedSection = dateSections.get(archivedCategory);
    if (archivedSection) {
        // Sort archived sessions by timestamp
        const sortedArchived = archivedSessions
            .sort((a, b) => getSessionSortTimestamp(b, state.sortBy) - getSessionSortTimestamp(a, state.sortBy));

        if (sortedArchived.length === 0) {
            archivedSection.element.classList.add('empty');
            archivedSection.element.style.display = 'none';
        } else {
            archivedSection.element.classList.remove('empty');
            archivedSection.element.style.display = '';
            sortedArchived.forEach(function(session) {
                archivedSection.listElement.appendChild(session.sidebarItem);
            });
        }
    }

    // FLIP animation: animate from old to new positions
    state.projects.forEach(function(project) {
        const oldRect = projectPositions.get(project.name);
        const newRect = project.element.getBoundingClientRect();
        if (oldRect && Math.abs(oldRect.top - newRect.top) > 1) {
            const deltaY = oldRect.top - newRect.top;
            project.element.style.transform = `translateY(${deltaY}px)`;
            project.element.style.transition = 'none';
            requestAnimationFrame(function() {
                project.element.style.transition = 'transform 0.2s ease-out';
                project.element.style.transform = '';
            });
        }
    });

    state.sessions.forEach(function(session) {
        const oldRect = sessionPositions.get(session.id);
        const newRect = session.sidebarItem.getBoundingClientRect();
        if (oldRect && Math.abs(oldRect.top - newRect.top) > 1) {
            const deltaY = oldRect.top - newRect.top;
            session.sidebarItem.style.transform = `translateY(${deltaY}px)`;
            session.sidebarItem.style.transition = 'none';
            requestAnimationFrame(function() {
                session.sidebarItem.style.transition = 'transform 0.2s ease-out';
                session.sidebarItem.style.transform = '';
            });
        }
    });
}

// Load messages for a session on-demand (lazy loading)
export async function loadSessionMessages(sessionId) {
    const session = state.sessions.get(sessionId);
    if (!session || session.loaded || session.loading || session.pending) return;

    session.loading = true;
    session.container.classList.add('loading');

    try {
        const response = await fetch(`/sessions/${sessionId}/messages`);
        if (!response.ok) {
            throw new Error(`Failed to load messages: ${response.status}`);
        }

        const data = await response.json();

        // Insert messages after the session header
        if (data.html) {
            const temp = document.createElement('div');
            // Process vibedeck commands to add execute buttons (isLiveStreaming=false to skip auto-execute)
            temp.innerHTML = parseAndExecuteCommands(data.html, sessionId, false);

            // Process each message element
            while (temp.firstElementChild) {
                const msg = temp.firstElementChild;
                session.container.appendChild(msg);
                processNewElement(msg);
                extractArtifactsFromElement(sessionId, msg);
            }
        }

        session.loaded = true;
        session.messageCount = data.message_count || 0;

        // Clear unread count since we've loaded the messages
        session.unreadCount = 0;

    } catch (error) {
        console.error('Failed to load session messages:', error);
        // Show error in container
        const errorDiv = document.createElement('div');
        errorDiv.className = 'load-error';
        errorDiv.innerHTML = `<p>Failed to load messages. <button class="retry-btn">Retry</button></p>`;
        errorDiv.querySelector('.retry-btn').addEventListener('click', function() {
            // Clear container before retry to prevent duplicates
            session.container.innerHTML = '';
            loadSessionMessages(sessionId);
        });
        session.container.appendChild(errorDiv);
    } finally {
        session.loading = false;
        session.container.classList.remove('loading');
    }
}

export function switchToSession(sessionId, scrollToBottom = false) {
    const session = state.sessions.get(sessionId);
    if (!session) return;

    // Close current preview without clearing session association
    if (state.previewPaneOpen) {
        closePreviewPane(false);
    }

    // Update containers
    state.sessions.forEach(function(s) {
        s.container.classList.remove('active');
        s.sidebarItem.classList.remove('active');
    });
    session.container.classList.add('active');
    session.sidebarItem.classList.add('active');
    state.activeSessionId = sessionId;

    // Load messages if not already loaded (lazy loading)
    if (!session.loaded && !session.loading && !session.pending) {
        loadSessionMessages(sessionId).then(() => {
            if (scrollToBottom) {
                // Double rAF ensures layout is complete after DOM changes
                requestAnimationFrame(function() {
                    requestAnimationFrame(function() {
                        window.scrollTo(0, document.body.scrollHeight);
                    });
                });
            }
        });
    } else if (scrollToBottom) {
        // Double rAF ensures layout is complete after display:block transition
        requestAnimationFrame(function() {
            requestAnimationFrame(function() {
                window.scrollTo(0, document.body.scrollHeight);
            });
        });
    }

    // Notify artifacts panel of session change
    onSessionChanged(sessionId);

    // Clear unread/waiting state
    session.sidebarItem.classList.remove('unread', 'waiting');
    updateProjectUnreadState(session.projectName);

    // Update header title
    dom.sessionTitleBar.textContent = session.displayTitle || session.name;

    // Update project title and session ID
    dom.projectTitle.textContent = session.projectName || 'Unknown Project';
    state.currentProjectPath = session.cwd;
    state.currentSessionId = session.pending ? null : sessionId;

    if (state.currentProjectPath) {
        dom.copyPathBtn.style.display = '';
    } else {
        dom.copyPathBtn.style.display = 'none';
    }

    if (state.currentSessionId && !session.pending) {
        dom.sessionIdValue.textContent = state.currentSessionId;
        dom.sessionIdBar.style.display = '';
    } else {
        dom.sessionIdBar.style.display = 'none';
    }

    updateInputBarUI();
    updateUserNavButtons();

    // Restore preview for this session if one was open
    const savedPreviewPath = state.sessionPreviewPaths.get(sessionId);
    if (savedPreviewPath) {
        openPreviewPane(savedPreviewPath);
    }

    // Set project root for relative path calculations in file tree
    if (session.cwd) {
        setProjectRoot(session.cwd);
    }

    // Load file tree for the session
    // For pending sessions or sessions without backend project_path, use cwd directly
    // This ensures the file tree opens in the project directory even for new sessions
    if (session.pending && session.cwd) {
        // For pending sessions, use the cwd directly as the path
        // Since there's no real session on the backend yet, we need to use a real session ID
        // to make the API call work, but pass the cwd as the explicit path
        const fallbackSessionId = findFallbackSessionId();
        if (fallbackSessionId) {
            loadFileTree(fallbackSessionId, session.cwd);
        }
    } else {
        loadFileTree(sessionId, session.cwd || null);
    }
}

export function updateSessionWaitingState(sessionId) {
    const session = state.sessions.get(sessionId);
    if (!session) return;

    const status = state.sessionStatus.get(sessionId);
    const isWaiting = status && status.waiting_for_input;
    const isRunning = status && status.running;
    const hasUnread = session.sidebarItem.classList.contains('unread') ||
                      session.sidebarItem.classList.contains('waiting');

    // Update running spinner
    if (isRunning) {
        session.sidebarItem.classList.add('running');
    } else {
        session.sidebarItem.classList.remove('running');
    }

    // Update session item: waiting takes precedence over unread
    if (hasUnread && isWaiting) {
        session.sidebarItem.classList.remove('unread');
        session.sidebarItem.classList.add('waiting');
    } else if (hasUnread && !isWaiting) {
        session.sidebarItem.classList.remove('waiting');
        session.sidebarItem.classList.add('unread');
    }

    // Update project state
    updateProjectUnreadState(session.projectName);
}

export function updateProjectUnreadState(projectName) {
    const project = state.projects.get(projectName);
    if (!project) return;

    // Check if any session in this project has unread/waiting messages
    let hasUnread = false;
    let hasWaiting = false;
    project.sessions.forEach(function(sessionId) {
        const session = state.sessions.get(sessionId);
        if (session) {
            if (session.sidebarItem.classList.contains('waiting')) {
                hasWaiting = true;
            } else if (session.sidebarItem.classList.contains('unread')) {
                hasUnread = true;
            }
        }
    });

    // waiting takes precedence over unread for projects
    project.element.classList.remove('unread', 'waiting');
    if (hasWaiting) {
        project.element.classList.add('waiting');
    } else if (hasUnread) {
        project.element.classList.add('unread');
    }
}

export function appendMessage(sessionId, html) {
    const session = state.sessions.get(sessionId);
    if (!session) return;

    const isActiveSession = sessionId === state.activeSessionId;

    // If session not loaded yet, just track unread and update sidebar
    // (message will be fetched when session is viewed)
    if (!session.loaded && !isActiveSession) {
        session.unreadCount = (session.unreadCount || 0) + 1;
        session.lastActivity = Date.now();
        session.lastUpdatedAt = Date.now() / 1000;

        // Update project's last activity
        const project = state.projects.get(session.projectName);
        if (project) {
            project.lastActivity = Math.max(project.lastActivity, session.lastActivity);
        }

        // Mark as unread/waiting
        const status = state.sessionStatus.get(sessionId);
        if (status && status.waiting_for_input) {
            session.sidebarItem.classList.add('waiting');
        } else {
            session.sidebarItem.classList.add('unread');
        }
        updateProjectUnreadState(session.projectName);

        // Handle auto-switch
        if (state.catchupComplete && state.autoSwitch) {
            if (state.autoSwitchDebounce) clearTimeout(state.autoSwitchDebounce);
            state.autoSwitchDebounce = setTimeout(function() {
                switchToSession(sessionId, true);
            }, 100);
        }

        reorderSidebar();
        return;
    }

    const wasNearBottom = isNearBottom();

    const temp = document.createElement('div');
    temp.innerHTML = html;
    const msg = temp.firstElementChild;

    if (msg) {
        removePlaceholderIfMatches(sessionId, msg);

        session.container.appendChild(msg);
        processNewElement(msg);
        extractArtifactsFromElement(sessionId, msg);
        session.messageCount++;

        if (state.catchupComplete) {
            session.lastActivity = Date.now();
            session.lastUpdatedAt = Date.now() / 1000;

            // Update project's last activity
            const project = state.projects.get(session.projectName);
            if (project) {
                project.lastActivity = Math.max(project.lastActivity, session.lastActivity);
            }

            // Mark as unread/waiting if not the active session
            if (!isActiveSession) {
                const status = state.sessionStatus.get(sessionId);
                if (status && status.waiting_for_input) {
                    session.sidebarItem.classList.add('waiting');
                } else {
                    session.sidebarItem.classList.add('unread');
                }
                updateProjectUnreadState(session.projectName);
            }

            reorderSidebar();
        }

        while (session.container.children.length > MAX_MESSAGES + 1) {  // +1 for header
            session.container.removeChild(session.container.children[1]);  // Skip header
        }

        // Handle auto-switch (only after initial catchup)
        if (state.catchupComplete && state.autoSwitch && !isActiveSession) {
            if (state.autoSwitchDebounce) clearTimeout(state.autoSwitchDebounce);
            state.autoSwitchDebounce = setTimeout(function() {
                switchToSession(sessionId, true);
            }, 100);
        }

        // Auto-scroll within session
        if (isActiveSession && (state.autoScroll || wasNearBottom)) {
            msg.scrollIntoView({ behavior: 'smooth', block: 'end' });
        }

        // Update navigation buttons when a new message arrives
        if (isActiveSession) {
            updateUserNavButtons();
        }
    }
}

export function processNewElement(element) {
    element.querySelectorAll('time[data-timestamp]').forEach(function(el) {
        const timestamp = el.getAttribute('data-timestamp');
        const date = new Date(timestamp);
        const now = new Date();
        const isToday = date.toDateString() === now.toDateString();
        const timeStr = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        if (isToday) { el.textContent = timeStr; }
        else { el.textContent = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + timeStr; }
    });

    element.querySelectorAll('pre.json').forEach(function(el) {
        let text = el.textContent;
        text = text.replace(/"([^"]+)":/g, '<span style="color: #ce93d8">"$1"</span>:');
        text = text.replace(/: "([^"]*)"/g, ': <span style="color: #81d4fa">"$1"</span>');
        text = text.replace(/: (\d+)/g, ': <span style="color: #ffcc80">$1</span>');
        text = text.replace(/: (true|false|null)/g, ': <span style="color: #f48fb1">$1</span>');
        el.innerHTML = text;
    });

    element.querySelectorAll('.truncatable').forEach(function(wrapper) {
        const content = wrapper.querySelector('.truncatable-content');
        const btn = wrapper.querySelector('.expand-btn');
        if (content && btn) {
            // Always set up the click handler
            btn.addEventListener('click', function() {
                if (wrapper.classList.contains('truncated')) {
                    wrapper.classList.remove('truncated');
                    wrapper.classList.add('expanded');
                    btn.textContent = 'Show less';
                } else {
                    wrapper.classList.remove('expanded');
                    wrapper.classList.add('truncated');
                    btn.textContent = 'Show more';
                }
            });
            // Use requestAnimationFrame to check scrollHeight after layout
            requestAnimationFrame(function() {
                if (content.scrollHeight > 250) {
                    wrapper.classList.add('truncated');
                }
            });
        }
    });

    // Add tooltip handlers for assistant messages with usage data
    // Check both the element itself and its descendants
    const assistantMessages = [];
    if (element.matches && element.matches('.message.assistant[data-usage-input]')) {
        assistantMessages.push(element);
    }
    element.querySelectorAll('.message.assistant[data-usage-input]').forEach(function(msg) {
        assistantMessages.push(msg);
    });
    assistantMessages.forEach(function(msg) {
        const header = msg.querySelector('.message-header');
        if (header) {
            header.addEventListener('mouseenter', function(e) {
                showMessageTooltip(msg, e);
            });
            header.addEventListener('mouseleave', hideMessageTooltip);
            header.addEventListener('mousemove', function(e) {
                positionMessageTooltip(e);
            });
        }
    });
}

export function createPlaceholderMessage(sessionId, messageText) {
    const session = state.sessions.get(sessionId);
    if (!session) return null;

    const timestamp = new Date().toISOString();
    const msgId = 'placeholder-' + Date.now();

    const html = '<div class="message user placeholder-message" id="' + msgId + '">' +
        '<div class="message-header">' +
        '<span class="role-label">User</span>' +
        '<time datetime="' + timestamp + '">Sending...</time>' +
        '</div>' +
        '<div class="message-content">' +
        '<div class="user-content"><p>' + escapeHtml(messageText) + '</p></div>' +
        '</div>' +
        '</div>';

    const temp = document.createElement('div');
    temp.innerHTML = html;
    const element = temp.firstElementChild;

    session.container.appendChild(element);

    const placeholder = { sessionId: sessionId, messageText: messageText, element: element };
    state.pendingMessages.push(placeholder);

    element.scrollIntoView({ behavior: 'smooth', block: 'end' });

    return placeholder;
}

function removePlaceholderIfMatches(sessionId, messageElement) {
    const incomingContent = messageElement.querySelector('.user-content');
    if (!incomingContent) return;

    const incomingText = incomingContent.textContent.trim();

    for (let i = 0; i < state.pendingMessages.length; i++) {
        const pending = state.pendingMessages[i];
        if (pending.sessionId === sessionId && pending.messageText.trim() === incomingText) {
            pending.element.remove();
            state.pendingMessages.splice(i, 1);
            return true;
        }
    }
    return false;
}

// Initialize group by select handler
export function initGroupBySelect(onReorder) {
    dom.groupBySelect.value = state.groupBy;
    dom.groupBySelect.addEventListener('change', function() {
        state.groupBy = dom.groupBySelect.value;
        localStorage.setItem('groupBy', state.groupBy);
        onReorder();
    });
}

// Initialize order by select handler
export function initOrderBySelect(onReorder) {
    dom.orderBySelect.value = state.sortBy;
    dom.orderBySelect.addEventListener('change', function() {
        state.sortBy = dom.orderBySelect.value;
        localStorage.setItem('sortBy', state.sortBy);
        onReorder();
    });
}

// Initialize copy button handlers
export function initCopyButtons() {
    dom.copyPathBtn.addEventListener('click', function() {
        if (state.currentProjectPath) {
            copyToClipboard(state.currentProjectPath, dom.copyPathBtn);
        }
    });

    dom.copySessionBtn.addEventListener('click', function() {
        if (state.currentSessionId) {
            copyToClipboard(state.currentSessionId, dom.copySessionBtn);
        }
    });
}
