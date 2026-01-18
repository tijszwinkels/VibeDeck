// Sessions module - session management, sidebar ordering

import { dom, state, MAX_MESSAGES, MAX_TITLE_LENGTH, dateCategoryLabels, dateCategoryOrder } from './state.js';
import {
    escapeHtml, truncateTitle, isMobile, isNearBottom, copyToClipboard,
    getDateCategory, getSessionSortTimestamp, getSessionCategoryTimestamp,
    parseStatusFromTitle, getStatusClass, formatTokenCount, formatCost, formatModelName
} from './utils.js';
import {
    showTooltip, hideTooltip, positionTooltip, showDateCategoryTooltip,
    showMessageTooltip, hideMessageTooltip, positionMessageTooltip,
    updateSidebarState, updateUserNavButtons
} from './ui.js';
import { closePreviewPane, openPreviewPane } from './preview.js';
import { loadFileTree, setProjectRoot } from './filetree.js';

// Forward declaration for circular dependency - will be set by messaging.js
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

export function updateSidebarItemStatusClass(sidebarItem, title) {
    // Remove existing status classes
    sidebarItem.classList.remove('status-done', 'status-waiting', 'status-in-progress');
    // Add new status class if applicable
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

export function createSession(sessionId, name, projectName, firstMessage, startedAt, lastUpdatedAt, projectPath, tokenUsage, backend, summaryTitle, summaryShort, summaryExecutive) {
    if (state.sessions.has(sessionId)) return state.sessions.get(sessionId);

    // Create container with session header
    const container = document.createElement('div');
    container.className = 'session-container';
    container.dataset.session = sessionId;
    if (backend) container.dataset.backend = backend;

    // Use summary title if available, otherwise fall back to firstMessage or name
    const displayTitle = truncateTitle(summaryTitle || firstMessage || name, MAX_TITLE_LENGTH);

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

    sidebarItem.innerHTML = `
        <span class="unread-dot"></span>
        <span class="session-title">${escapeHtml(displayTitle)}</span>
        <span class="close-btn" title="Close">&times;</span>
    `;

    // Apply status color based on title suffix (e.g., "- Merged", "- Waiting for Input")
    updateSidebarItemStatusClass(sidebarItem, summaryTitle);

    sidebarItem.addEventListener('click', function(e) {
        if (e.target.classList.contains('close-btn')) {
            removeSession(sessionId);
        } else {
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
        summaryExecutive: summaryExecutive || null
    };
    state.sessions.set(sessionId, session);

    // Update project's last activity
    project.lastActivity = Math.max(project.lastActivity, session.lastActivity);

    // If this is the first session, activate it
    if (state.sessions.size === 1) {
        switchToSession(sessionId);
    }

    return session;
}

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

export function reorderSidebar() {
    // FLIP animation: capture positions before reorder
    const projectPositions = new Map();
    const sessionPositions = new Map();

    state.projects.forEach(function(project) {
        projectPositions.set(project.name, project.element.getBoundingClientRect());
    });
    state.sessions.forEach(function(session) {
        sessionPositions.set(session.id, session.sidebarItem.getBoundingClientRect());
    });

    // Sort projects by most recent session (based on sort mode)
    const sortedProjects = Array.from(state.projects.values()).sort(function(a, b) {
        const aMax = Array.from(a.sessions)
            .map(id => state.sessions.get(id))
            .filter(s => s)
            .reduce((max, s) => Math.max(max, getSessionSortTimestamp(s, state.sortBy)), 0);
        const bMax = Array.from(b.sessions)
            .map(id => state.sessions.get(id))
            .filter(s => s)
            .reduce((max, s) => Math.max(max, getSessionSortTimestamp(s, state.sortBy)), 0);
        return bMax - aMax;
    });

    sortedProjects.forEach(function(project) {
        dom.projectListContainer.appendChild(project.element);

        // Group sessions by date category
        const sessionsByCategory = {};
        dateCategoryOrder.forEach(function(cat) { sessionsByCategory[cat] = []; });

        Array.from(project.sessions)
            .map(id => state.sessions.get(id))
            .filter(s => s)
            .forEach(function(session) {
                const category = getDateCategory(getSessionCategoryTimestamp(session, state.sortBy));
                sessionsByCategory[category].push(session);
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

        // Collapse project if no sessions in "today"
        const todaySessions = sessionsByCategory['today'];
        if (todaySessions.length === 0 && !project.element.classList.contains('user-expanded')) {
            project.element.classList.add('collapsed');
        }
    });

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

    if (scrollToBottom) {
        requestAnimationFrame(function() {
            window.scrollTo(0, document.body.scrollHeight);
        });
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

    const wasNearBottom = isNearBottom();
    const isActiveSession = sessionId === state.activeSessionId;

    const temp = document.createElement('div');
    temp.innerHTML = html;
    const msg = temp.firstElementChild;

    if (msg) {
        removePlaceholderIfMatches(sessionId, msg);

        session.container.appendChild(msg);
        processNewElement(msg);
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
        if (content && btn && content.scrollHeight > 250) {
            wrapper.classList.add('truncated');
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

// Initialize sort select handler
export function initSortSelect(onReorder) {
    dom.sortSelect.value = state.sortBy;
    dom.sortSelect.addEventListener('change', function() {
        state.sortBy = dom.sortSelect.value;
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
