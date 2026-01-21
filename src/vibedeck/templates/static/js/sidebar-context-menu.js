// Sidebar context menu for projects and sessions

import { state } from './state.js';
import { copyToClipboard } from './utils.js';
import { loadFileTree, openRightPane } from './filetree.js';
import { archiveSession, unarchiveSession } from './sessions.js';

let sidebarContextMenu = null;
let currentMenuType = null; // 'project' or 'session'
let currentMenuData = null;

// Initialize the sidebar context menu
export function initSidebarContextMenu() {
    sidebarContextMenu = document.createElement('div');
    sidebarContextMenu.className = 'tree-context-menu sidebar-context-menu';
    sidebarContextMenu.innerHTML = '';
    sidebarContextMenu.style.display = 'none';
    document.body.appendChild(sidebarContextMenu);

    sidebarContextMenu.addEventListener('click', handleSidebarContextMenuClick);

    // Close menu when clicking elsewhere
    document.addEventListener('click', hideSidebarContextMenu);
    document.addEventListener('contextmenu', (e) => {
        // Close existing menu if right-clicking elsewhere
        if (!e.target.closest('.project-header') && !e.target.closest('.session-item')) {
            hideSidebarContextMenu();
        }
    });
}

// Show context menu for a project
export function showProjectContextMenu(e, projectPath, projectName) {
    e.preventDefault();
    e.stopPropagation();

    if (!sidebarContextMenu) return;

    currentMenuType = 'project';
    currentMenuData = { path: projectPath, name: projectName };

    // Build menu items for project
    let menuItems = '';

    if (projectPath) {
        menuItems += `<button class="context-menu-item" data-action="copy-path">Copy project directory</button>`;
        menuItems += `<button class="context-menu-item" data-action="open-file-browser">Open in file browser</button>`;
    } else {
        menuItems += `<button class="context-menu-item" disabled>No project path available</button>`;
    }

    sidebarContextMenu.innerHTML = menuItems;
    positionAndShowMenu(e);
}

// Show context menu for a session
export function showSessionContextMenu(e, sessionId) {
    e.preventDefault();
    e.stopPropagation();

    if (!sidebarContextMenu) return;

    const session = state.sessions.get(sessionId);
    if (!session) return;

    currentMenuType = 'session';
    currentMenuData = { sessionId, session };

    // Build menu items for session
    const isArchived = state.archivedSessionIds.has(sessionId);
    let menuItems = `<button class="context-menu-item" data-action="trigger-summary">Trigger summary</button>`;

    if (isArchived) {
        menuItems += `<button class="context-menu-item" data-action="unarchive">Unarchive session</button>`;
    } else {
        menuItems += `<button class="context-menu-item" data-action="archive">Archive session</button>`;
    }

    sidebarContextMenu.innerHTML = menuItems;
    positionAndShowMenu(e);
}

function positionAndShowMenu(e) {
    sidebarContextMenu.style.display = 'block';
    sidebarContextMenu.style.left = e.clientX + 'px';
    sidebarContextMenu.style.top = e.clientY + 'px';

    // Ensure menu stays within viewport
    const rect = sidebarContextMenu.getBoundingClientRect();
    if (rect.right > window.innerWidth) {
        sidebarContextMenu.style.left = (e.clientX - rect.width) + 'px';
    }
    if (rect.bottom > window.innerHeight) {
        sidebarContextMenu.style.top = (e.clientY - rect.height) + 'px';
    }
}

function hideSidebarContextMenu() {
    if (sidebarContextMenu) {
        sidebarContextMenu.style.display = 'none';
    }
    currentMenuType = null;
    currentMenuData = null;
}

function handleSidebarContextMenuClick(e) {
    const action = e.target.dataset.action;
    if (!action) return;

    if (currentMenuType === 'project') {
        handleProjectAction(action);
    } else if (currentMenuType === 'session') {
        handleSessionAction(action);
    }

    hideSidebarContextMenu();
}

function handleProjectAction(action) {
    if (!currentMenuData || !currentMenuData.path) return;

    if (action === 'copy-path') {
        // Use the existing copyToClipboard utility which handles HTTP fallback
        copyToClipboard(currentMenuData.path, null);
        showFlashMessage('Project path copied', 'success');
    } else if (action === 'open-file-browser') {
        openInFileBrowser(currentMenuData.path);
    }
}

async function handleSessionAction(action) {
    if (!currentMenuData || !currentMenuData.sessionId) return;

    if (action === 'trigger-summary') {
        await triggerSummary(currentMenuData.sessionId);
    } else if (action === 'archive') {
        archiveSession(currentMenuData.sessionId);
        showFlashMessage('Session archived', 'success');
    } else if (action === 'unarchive') {
        unarchiveSession(currentMenuData.sessionId);
        showFlashMessage('Session unarchived', 'success');
    }
}

function showFlashMessage(message, type) {
    const flash = document.getElementById('flash-message');
    if (flash) {
        flash.textContent = message;
        flash.className = 'flash-message visible ' + type;
        setTimeout(() => flash.classList.remove('visible'), 2000);
    }
}

function openInFileBrowser(path) {
    // Open the right pane (file tree) and load the specified path
    openRightPane();

    // Use the active session ID for the file tree API
    const sessionId = state.activeSessionId;
    if (sessionId) {
        loadFileTree(sessionId, path);
    }
}

async function triggerSummary(sessionId) {
    try {
        showFlashMessage('Triggering summary...', 'info');

        const response = await fetch(`/sessions/${sessionId}/summarize`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
        });

        const data = await response.json();

        if (response.ok) {
            showFlashMessage('Summary started', 'success');
        } else {
            showFlashMessage(data.detail || 'Failed to trigger summary', 'error');
        }
    } catch (err) {
        console.error('Error triggering summary:', err);
        showFlashMessage('Failed to trigger summary', 'error');
    }
}
