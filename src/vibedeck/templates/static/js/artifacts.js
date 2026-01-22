// Artifacts module - tracks files, diffs, URLs, and GUI commands from the stream

import { state } from './state.js';
import { openPreviewPane } from './preview.js';
import { openDiffView } from './diff.js';
import { showFlash } from './ui.js';
import { copyToClipboard } from './utils.js';

// DOM elements (initialized in initArtifacts)
let dom = {
    container: null,
    toggle: null,
    badge: null,
    panel: null,
    list: null,
    clearBtn: null
};

// Artifact types with their icons and labels
const ARTIFACT_TYPES = {
    file: { icon: '📄', label: 'File' },
    diff: { icon: '📝', label: 'Diff' },
    url: { icon: '🔗', label: 'URL' },
    gui: { icon: '🖥️', label: 'GUI' }
};

// Per-session artifact storage: sessionId -> { files: Set, diffs: Set, urls: Set, guis: [] }
const sessionArtifacts = new Map();

// Track if panel is open
let panelOpen = false;

// Track unseen count for badge
let unseenCount = 0;

/**
 * Get or create artifact storage for a session.
 */
function getSessionStore(sessionId) {
    if (!sessionArtifacts.has(sessionId)) {
        sessionArtifacts.set(sessionId, {
            files: new Set(),
            diffs: new Set(),
            urls: new Set(),
            guis: []  // Array because GUI commands might not be unique
        });
    }
    return sessionArtifacts.get(sessionId);
}

/**
 * Add a file artifact (from Read, Write tools).
 */
export function addFileArtifact(sessionId, filePath) {
    const store = getSessionStore(sessionId);
    if (!store.files.has(filePath)) {
        store.files.add(filePath);
        onArtifactAdded(sessionId);
    }
}

/**
 * Add a diff artifact (from Edit tool).
 */
export function addDiffArtifact(sessionId, filePath) {
    const store = getSessionStore(sessionId);
    if (!store.diffs.has(filePath)) {
        store.diffs.add(filePath);
        onArtifactAdded(sessionId);
    }
    // Also add as a file for viewing
    addFileArtifact(sessionId, filePath);
}

/**
 * Add a URL artifact.
 */
export function addUrlArtifact(sessionId, url) {
    const store = getSessionStore(sessionId);
    if (!store.urls.has(url)) {
        store.urls.add(url);
        onArtifactAdded(sessionId);
    }
}

/**
 * Add a GUI command artifact (vibedeck block).
 */
export function addGuiArtifact(sessionId, command, label) {
    const store = getSessionStore(sessionId);
    // Check if this exact command already exists
    const exists = store.guis.some(g => g.command === command);
    if (!exists) {
        store.guis.push({ command, label: label || 'GUI Command' });
        onArtifactAdded(sessionId);
    }
}

/**
 * Called when any artifact is added.
 */
function onArtifactAdded(sessionId) {
    // Only update badge if this is the active session
    if (sessionId === state.activeSessionId) {
        if (!panelOpen) {
            unseenCount++;
            updateBadge();
        }
        renderArtifactsList();
    }
}

/**
 * Update the badge count display.
 */
function updateBadge() {
    if (!dom.badge) return;
    if (unseenCount > 0) {
        dom.badge.textContent = unseenCount > 99 ? '99+' : unseenCount;
        dom.badge.style.display = '';
    } else {
        dom.badge.style.display = 'none';
    }
}

/**
 * Render the artifacts list for the current session.
 */
function renderArtifactsList() {
    if (!dom.list) return;

    const sessionId = state.activeSessionId;
    if (!sessionId || !sessionArtifacts.has(sessionId)) {
        dom.list.innerHTML = '<div class="artifacts-empty">No artifacts yet</div>';
        return;
    }

    const store = sessionArtifacts.get(sessionId);
    const hasArtifacts = store.files.size > 0 || store.diffs.size > 0 ||
                         store.urls.size > 0 || store.guis.length > 0;

    if (!hasArtifacts) {
        dom.list.innerHTML = '<div class="artifacts-empty">No artifacts yet</div>';
        return;
    }

    let html = '';

    // Render diffs first (most actionable)
    if (store.diffs.size > 0) {
        html += renderSection('diff', Array.from(store.diffs));
    }

    // Render files (excluding those already in diffs)
    const filesOnly = Array.from(store.files).filter(f => !store.diffs.has(f));
    if (filesOnly.length > 0) {
        html += renderSection('file', filesOnly);
    }

    // Render URLs
    if (store.urls.size > 0) {
        html += renderSection('url', Array.from(store.urls));
    }

    // Render GUI commands
    if (store.guis.length > 0) {
        html += renderGuiSection(store.guis);
    }

    dom.list.innerHTML = html;

    // Add click handlers
    dom.list.querySelectorAll('.artifact-item').forEach(item => {
        item.addEventListener('click', handleArtifactClick);
    });
}

/**
 * Render a section of artifacts.
 */
function renderSection(type, items) {
    const { icon, label } = ARTIFACT_TYPES[type];
    let html = `<div class="artifacts-section">
        <div class="artifacts-section-header">
            <span class="artifacts-section-icon">${icon}</span>
            <span class="artifacts-section-label">${label}s (${items.length})</span>
        </div>`;

    items.forEach(item => {
        const displayName = type === 'url' ? truncateUrl(item) : getFileName(item);
        const escaped = escapeHtml(item);
        const escapedDisplay = escapeHtml(displayName);
        html += `<div class="artifact-item" data-type="${type}" data-value="${escaped}" title="${escaped}">
            <span class="artifact-name">${escapedDisplay}</span>
        </div>`;
    });

    html += '</div>';
    return html;
}

/**
 * Render GUI commands section.
 */
function renderGuiSection(guis) {
    const { icon, label } = ARTIFACT_TYPES.gui;
    let html = `<div class="artifacts-section">
        <div class="artifacts-section-header">
            <span class="artifacts-section-icon">${icon}</span>
            <span class="artifacts-section-label">${label} (${guis.length})</span>
        </div>`;

    guis.forEach((gui, index) => {
        const escaped = escapeHtml(gui.command);
        const escapedLabel = escapeHtml(gui.label);
        html += `<div class="artifact-item" data-type="gui" data-index="${index}" title="${escapedLabel}">
            <span class="artifact-name">${escapedLabel}</span>
        </div>`;
    });

    html += '</div>';
    return html;
}

/**
 * Handle click on an artifact item.
 */
function handleArtifactClick(e) {
    const item = e.currentTarget;
    const type = item.dataset.type;
    const value = item.dataset.value;

    switch (type) {
        case 'file':
            openPreviewPane(value);
            closePanel();
            break;
        case 'diff':
            openDiffView(value);
            closePanel();
            break;
        case 'url':
            copyToClipboard(value, null);
            showFlash('URL copied to clipboard', 'success', 2000);
            break;
        case 'gui':
            executeGuiCommand(parseInt(item.dataset.index));
            closePanel();
            break;
    }
}

/**
 * Execute a GUI command by index.
 */
function executeGuiCommand(index) {
    const sessionId = state.activeSessionId;
    if (!sessionId) return;

    const store = sessionArtifacts.get(sessionId);
    if (!store || index >= store.guis.length) return;

    const gui = store.guis[index];
    // Dispatch a custom event that commands.js can listen for
    window.dispatchEvent(new CustomEvent('vibedeck-command', {
        detail: { command: gui.command }
    }));
}

/**
 * Toggle the artifacts panel.
 */
function togglePanel() {
    if (panelOpen) {
        closePanel();
    } else {
        openPanel();
    }
}

/**
 * Open the artifacts panel.
 */
function openPanel() {
    panelOpen = true;
    unseenCount = 0;
    updateBadge();
    dom.panel.style.display = '';
    dom.container.classList.add('open');
    renderArtifactsList();
}

/**
 * Close the artifacts panel.
 */
function closePanel() {
    panelOpen = false;
    dom.panel.style.display = 'none';
    dom.container.classList.remove('open');
}

/**
 * Clear all artifacts for the current session.
 */
function clearArtifacts() {
    const sessionId = state.activeSessionId;
    if (sessionId) {
        sessionArtifacts.delete(sessionId);
        renderArtifactsList();
    }
}

/**
 * Called when the active session changes.
 */
export function onSessionChanged(sessionId) {
    unseenCount = 0;
    updateBadge();
    if (panelOpen) {
        renderArtifactsList();
    }
}

/**
 * Extract the filename from a path.
 */
function getFileName(path) {
    return path.split('/').pop() || path;
}

/**
 * Truncate a URL for display.
 */
function truncateUrl(url) {
    try {
        const parsed = new URL(url);
        const path = parsed.pathname + parsed.search;
        if (path.length > 40) {
            return parsed.hostname + path.substring(0, 37) + '...';
        }
        return parsed.hostname + path;
    } catch {
        if (url.length > 50) {
            return url.substring(0, 47) + '...';
        }
        return url;
    }
}

/**
 * Escape HTML for safe display.
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Initialize the artifacts module.
 */
export function initArtifacts() {
    dom.container = document.getElementById('artifacts-container');
    dom.toggle = document.getElementById('artifacts-toggle');
    dom.badge = document.getElementById('artifacts-badge');
    dom.panel = document.getElementById('artifacts-panel');
    dom.list = document.getElementById('artifacts-list');
    dom.clearBtn = document.getElementById('artifacts-clear-btn');

    if (!dom.toggle) return;

    // Toggle panel on button click
    dom.toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        togglePanel();
    });

    // Clear button
    if (dom.clearBtn) {
        dom.clearBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            clearArtifacts();
        });
    }

    // Close panel when clicking outside
    document.addEventListener('click', (e) => {
        if (panelOpen && !dom.container.contains(e.target)) {
            closePanel();
        }
    });

    // Close panel on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && panelOpen) {
            closePanel();
        }
    });
}

/**
 * Extract artifacts from a rendered HTML message element.
 * Called after a message is appended to the DOM.
 */
export function extractArtifactsFromElement(sessionId, element) {
    if (!sessionId) return;

    // Extract file paths from file-tool-fullpath elements (Read, Write tools)
    element.querySelectorAll('.file-tool-fullpath[data-copy-path]').forEach(el => {
        const path = el.dataset.copyPath;
        if (path) {
            addFileArtifact(sessionId, path);
        }
    });

    // Extract diffs from edit-tool blocks
    element.querySelectorAll('.edit-tool .file-tool-fullpath[data-copy-path]').forEach(el => {
        const path = el.dataset.copyPath;
        if (path) {
            addDiffArtifact(sessionId, path);
        }
    });

    // Extract URLs from the text content
    const textContent = element.textContent || '';
    const urlPattern = /https?:\/\/[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+/gi;
    const urls = textContent.match(urlPattern) || [];
    urls.forEach(url => {
        // Clean up trailing punctuation
        url = url.replace(/[)\].,;:!]+$/, '');
        addUrlArtifact(sessionId, url);
    });

    // Extract vibedeck command blocks
    element.querySelectorAll('code.language-vibedeck').forEach(el => {
        const command = el.textContent || '';
        if (command.trim()) {
            // Try to extract a label from the command
            let label = 'GUI Command';
            const openFileMatch = command.match(/openFile\s+path="([^"]+)"/);
            const openUrlMatch = command.match(/openUrl\s+url="([^"]+)"/);
            if (openFileMatch) {
                label = 'Open: ' + getFileName(openFileMatch[1]);
            } else if (openUrlMatch) {
                label = 'Open URL: ' + truncateUrl(openUrlMatch[1]);
            }
            addGuiArtifact(sessionId, command, label);
        }
    });
}
