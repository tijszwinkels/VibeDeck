// Artifacts module - tracks files, diffs, URLs, and GUI commands from the stream
// Displays as a floating panel with colored tags, ordered by last updated

import { state } from './state.js';
import { openPreviewPane } from './preview.js';
import { showFlash } from './ui.js';
import { copyToClipboard } from './utils.js';

// DOM elements (initialized in initArtifacts)
let dom = {
    float: null,
    toggle: null,
    badge: null,
    body: null,
    list: null,
    expandBtn: null
};

// Artifact type labels
const TYPE_LABELS = {
    file: 'File',
    diff: 'Diff',
    url: 'URL',
    gui: 'GUI'
};

// Per-session artifact storage: sessionId -> Map<key, {type, value, label, timestamp, messageId}>
// Key is type:value to ensure uniqueness
const sessionArtifacts = new Map();

// Track if panel is expanded
let expanded = false;

/**
 * Get or create artifact storage for a session.
 */
function getSessionStore(sessionId) {
    if (!sessionArtifacts.has(sessionId)) {
        sessionArtifacts.set(sessionId, new Map());
    }
    return sessionArtifacts.get(sessionId);
}

/**
 * Add or update an artifact with its timestamp and source message.
 * @param {string} sessionId
 * @param {string} type
 * @param {string} value
 * @param {string} label
 * @param {number} [timestamp] - Optional timestamp, defaults to Date.now()
 * @param {string} [messageId] - Optional message element ID for scrolling
 */
function addArtifact(sessionId, type, value, label, timestamp, messageId) {
    const store = getSessionStore(sessionId);
    const key = `${type}:${value}`;

    store.set(key, {
        type,
        value,
        label: label || value,
        timestamp: timestamp || Date.now(),
        messageId: messageId || null
    });

    onArtifactAdded(sessionId);
}

/**
 * Add a file artifact (from Read, Write tools).
 */
function addFileArtifact(sessionId, filePath, timestamp, messageId) {
    const filename = filePath.split('/').pop() || filePath;
    addArtifact(sessionId, 'file', filePath, filename, timestamp, messageId);
}

/**
 * Add a diff artifact (from Edit tool).
 */
function addDiffArtifact(sessionId, filePath, timestamp, messageId) {
    const filename = filePath.split('/').pop() || filePath;
    addArtifact(sessionId, 'diff', filePath, filename, timestamp, messageId);
}

/**
 * Normalize a URL to ensure it has a protocol.
 * Prefers https:// but falls back to http:// for localhost/127.0.0.1.
 */
function normalizeUrl(url) {
    if (url.startsWith('http://') || url.startsWith('https://')) {
        return url;
    }
    // For localhost and 127.0.0.1, use http://
    if (url.startsWith('localhost') || url.startsWith('127.0.0.1')) {
        return 'http://' + url;
    }
    // For everything else, prefer https://
    return 'https://' + url;
}

/**
 * Add a URL artifact.
 */
function addUrlArtifact(sessionId, url, timestamp, messageId) {
    const normalizedUrl = normalizeUrl(url);
    const label = truncateUrl(normalizedUrl);
    addArtifact(sessionId, 'url', normalizedUrl, label, timestamp, messageId);
}

/**
 * Add a GUI command artifact (vibedeck block).
 */
function addGuiArtifact(sessionId, command, label, timestamp, messageId) {
    addArtifact(sessionId, 'gui', command, label || 'Command', timestamp, messageId);
}

/**
 * Called when any artifact is added.
 */
function onArtifactAdded(sessionId) {
    // Only update UI if this is the active session
    if (sessionId === state.activeSessionId) {
        updateBadge();
        if (expanded) {
            renderArtifactsList();
        }
    }
}

/**
 * Update the badge count display.
 */
function updateBadge() {
    if (!dom.badge) return;

    const sessionId = state.activeSessionId;
    if (!sessionId || !sessionArtifacts.has(sessionId)) {
        dom.badge.textContent = '';
        return;
    }

    const count = sessionArtifacts.get(sessionId).size;
    dom.badge.textContent = count > 0 ? count : '';
}

/**
 * Format a timestamp as time (e.g., "10:45:23").
 */
function formatTime(timestamp) {
    const date = new Date(timestamp);
    return date.toLocaleTimeString(undefined, {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
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

    if (store.size === 0) {
        dom.list.innerHTML = '<div class="artifacts-empty">No artifacts yet</div>';
        return;
    }

    // Sort by timestamp DESC (most recent first)
    const sorted = Array.from(store.values()).sort((a, b) => b.timestamp - a.timestamp);

    let html = '';
    for (const artifact of sorted) {
        const attrValue = escapeAttr(artifact.value);
        const escapedLabel = escapeHtml(artifact.label);
        const typeLabel = TYPE_LABELS[artifact.type] || artifact.type;
        const timeStr = formatTime(artifact.timestamp);
        const msgIdAttr = artifact.messageId ? ` data-message-id="${escapeAttr(artifact.messageId)}"` : '';

        html += `<div class="artifact-tag type-${artifact.type}" data-type="${artifact.type}" data-value="${attrValue}"${msgIdAttr} title="${attrValue}">
            <span class="tag-type">${typeLabel}:</span>
            <span class="tag-value">${escapedLabel}</span>
            <span class="tag-timestamp">${timeStr}</span>
        </div>`;
    }

    dom.list.innerHTML = html;

    // Add click handlers
    dom.list.querySelectorAll('.artifact-tag').forEach(tag => {
        tag.addEventListener('click', handleArtifactClick);
    });
}

/**
 * Scroll to a message element by ID.
 */
function scrollToMessage(messageId) {
    if (!messageId) return;
    const messageEl = document.getElementById(messageId);
    if (messageEl) {
        messageEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        // Brief highlight effect
        messageEl.classList.add('artifact-highlight');
        setTimeout(() => messageEl.classList.remove('artifact-highlight'), 1500);
    }
}

/**
 * Handle click on an artifact tag.
 */
function handleArtifactClick(e) {
    const tag = e.currentTarget;
    const type = tag.dataset.type;
    const value = tag.dataset.value;
    const messageId = tag.dataset.messageId;

    // Scroll to the source message
    scrollToMessage(messageId);

    switch (type) {
        case 'file':
            openPreviewPane(value);
            break;
        case 'diff':
            openPreviewPane(value);
            break;
        case 'url':
            copyToClipboard(value, null);
            showFlash('URL copied to clipboard', 'success', 2000);
            break;
        case 'gui':
            executeGuiCommand(value);
            break;
    }
}

/**
 * Execute a GUI command.
 */
function executeGuiCommand(command) {
    // Dispatch a custom event that commands.js listens for
    window.dispatchEvent(new CustomEvent('vibedeck-command', {
        detail: { command }
    }));
}

/**
 * Toggle the artifacts panel expanded/collapsed.
 */
function toggleExpanded() {
    expanded = !expanded;

    if (expanded) {
        dom.float.classList.add('expanded');
        renderArtifactsList();
    } else {
        dom.float.classList.remove('expanded');
    }
}

/**
 * Called when the active session changes.
 */
export function onSessionChanged(sessionId) {
    updateBadge();
    if (expanded) {
        renderArtifactsList();
    }
}

/**
 * Update floating panel position when preview pane opens/closes.
 */
function updateFloatPosition() {
    if (!dom.float) return;

    if (state.previewPaneOpen) {
        dom.float.classList.add('preview-open');
    } else {
        dom.float.classList.remove('preview-open');
    }
}

/**
 * Truncate a URL for display.
 */
function truncateUrl(url) {
    try {
        const parsed = new URL(url);
        const path = parsed.pathname + parsed.search;
        if (path.length > 30) {
            return parsed.hostname + path.substring(0, 27) + '...';
        }
        return parsed.hostname + path;
    } catch {
        if (url.length > 40) {
            return url.substring(0, 37) + '...';
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
 * Escape text for use in HTML attributes (also escapes quotes).
 */
function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;');
}

/**
 * Initialize the artifacts module.
 */
export function initArtifacts() {
    dom.float = document.getElementById('artifacts-float');
    dom.toggle = document.getElementById('artifacts-toggle');
    dom.badge = document.getElementById('artifacts-badge');
    dom.body = document.getElementById('artifacts-body');
    dom.list = document.getElementById('artifacts-list');
    dom.expandBtn = document.getElementById('artifacts-expand-btn');

    if (!dom.float || !dom.toggle) return;

    // Toggle expanded on header click
    dom.toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleExpanded();
    });

    // Watch for preview pane state changes
    const observer = new MutationObserver(() => {
        updateFloatPosition();
    });

    const mainContent = document.getElementById('main-content');
    if (mainContent) {
        observer.observe(mainContent, { attributes: true, attributeFilter: ['class'] });
    }

    // Initial position update
    updateFloatPosition();
}

/**
 * Get the timestamp from a message element.
 * Looks for time[data-timestamp] in the element or its ancestors.
 */
function getMessageTimestamp(element) {
    // First check if element itself has a time element
    let timeEl = element.querySelector('time[data-timestamp]');
    if (!timeEl) {
        // Check if we're inside a message and look for its time element
        const message = element.closest('.message');
        if (message) {
            timeEl = message.querySelector('time[data-timestamp]');
        }
    }
    if (timeEl) {
        const tsStr = timeEl.getAttribute('data-timestamp');
        const parsed = new Date(tsStr).getTime();
        if (!isNaN(parsed)) {
            return parsed;
        }
    }
    return Date.now();
}

/**
 * Extract artifacts from a rendered HTML message element.
 * Called after a message is appended to the DOM.
 */
export function extractArtifactsFromElement(sessionId, element) {
    if (!sessionId) return;

    // Get the message timestamp and ID
    const timestamp = getMessageTimestamp(element);
    const messageEl = element.closest('.message') || element;
    const messageId = messageEl.id || null;

    // Extract file paths from file-tool-fullpath elements (Read, Write tools)
    element.querySelectorAll('.file-tool-fullpath[data-copy-path]').forEach(el => {
        const path = el.dataset.copyPath;
        if (path) {
            // Check if this is inside an edit-tool (then it's a diff, not just a file)
            const isEdit = el.closest('.edit-tool');
            if (isEdit) {
                addDiffArtifact(sessionId, path, timestamp, messageId);
            } else {
                addFileArtifact(sessionId, path, timestamp, messageId);
            }
        }
    });

    // Extract URLs from the text content
    const textContent = element.textContent || '';
    // Match URLs with protocol (http:// or https://)
    const urlWithProtocol = /https?:\/\/[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+/gi;
    // Match localhost:port or 127.0.0.1:port URLs without protocol
    const localhostPattern = /(?:localhost|127\.0\.0\.1):\d+[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*/gi;

    const urlsWithProtocol = textContent.match(urlWithProtocol) || [];
    const localhostUrls = textContent.match(localhostPattern) || [];
    const allUrls = [...urlsWithProtocol, ...localhostUrls];

    allUrls.forEach(url => {
        // Clean up trailing punctuation
        url = url.replace(/[)\].,;:!]+$/, '');
        addUrlArtifact(sessionId, url, timestamp, messageId);
    });

    // Extract vibedeck command blocks
    element.querySelectorAll('code.language-vibedeck').forEach(el => {
        const command = el.textContent || '';
        if (command.trim()) {
            // Try to extract a label from the command
            let label = 'Command';
            const openFileMatch = command.match(/openFile\s+path="([^"]+)"/);
            const openUrlMatch = command.match(/openUrl\s+url="([^"]+)"/);
            if (openFileMatch) {
                const filename = openFileMatch[1].split('/').pop();
                label = filename;
            } else if (openUrlMatch) {
                label = truncateUrl(openUrlMatch[1]);
            }
            addGuiArtifact(sessionId, command, label, timestamp, messageId);
        }
    });
}
