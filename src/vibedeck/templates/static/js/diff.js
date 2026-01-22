/**
 * Diff view module - handles displaying git diffs in the preview pane.
 *
 * Features:
 * - Shows list of changed files with +/- stats in the left sidebar
 * - Renders unified diff view in the right pane
 * - Supports uncommitted changes and diff vs main branch
 */

import { dom, state } from './state.js';
import { openRightPane } from './filetree.js';
import { escapeHtml } from './utils.js';
import { showFlash } from './ui.js';
import { stopFileWatch } from './filewatch.js';

// Track if we're in diff mode
let diffContextMenu = null;

/**
 * Initialize diff view functionality.
 */
export function initDiffView() {
    // Initialize context menu for session messages
    initDiffContextMenu();
}

/**
 * Initialize the right-click context menu for showing diffs.
 */
function initDiffContextMenu() {
    diffContextMenu = document.createElement('div');
    diffContextMenu.className = 'diff-context-menu';
    diffContextMenu.innerHTML = `
        <button class="context-menu-item" data-action="show-diff">Show diff</button>
    `;
    diffContextMenu.style.display = 'none';
    document.body.appendChild(diffContextMenu);

    diffContextMenu.addEventListener('click', handleDiffContextMenuClick);

    // Close menu when clicking elsewhere
    document.addEventListener('click', hideDiffContextMenu);

    // Listen for right-click on the sessions container
    const sessionsContainer = document.getElementById('sessions');
    if (sessionsContainer) {
        sessionsContainer.addEventListener('contextmenu', handleSessionsContextMenu);
    }
}

/**
 * Handle right-click on sessions container to show diff context menu.
 */
function handleSessionsContextMenu(e) {
    // Only show context menu if there's an active session
    if (!state.activeSessionId) return;

    // Check if clicked on a message or near content (not on links, buttons, etc.)
    const target = e.target;

    // Don't intercept context menu on interactive elements
    if (target.closest('a') || target.closest('button') || target.closest('input')) {
        return;
    }

    // Show our context menu
    showDiffContextMenu(e);
}

/**
 * Show the diff context menu at position.
 */
export function showDiffContextMenu(e) {
    e.preventDefault();
    if (!diffContextMenu) return;

    diffContextMenu.style.display = 'block';
    diffContextMenu.style.left = e.clientX + 'px';
    diffContextMenu.style.top = e.clientY + 'px';

    // Ensure menu stays within viewport
    const rect = diffContextMenu.getBoundingClientRect();
    if (rect.right > window.innerWidth) {
        diffContextMenu.style.left = (e.clientX - rect.width) + 'px';
    }
    if (rect.bottom > window.innerHeight) {
        diffContextMenu.style.top = (e.clientY - rect.height) + 'px';
    }
}

function hideDiffContextMenu() {
    if (diffContextMenu) {
        diffContextMenu.style.display = 'none';
    }
}

function handleDiffContextMenuClick(e) {
    const action = e.target.dataset.action;
    if (action === 'show-diff') {
        openDiffView();
    }
    hideDiffContextMenu();
}

/**
 * Open the diff view for the current session.
 * Replaces the file tree with a list of changed files.
 *
 * @param {string|null} selectFilePath - Full path to a file to pre-select
 */
export async function openDiffView(selectFilePath = null) {
    if (!state.activeSessionId) {
        showFlash('No active session', 'error');
        return;
    }

    // Stop any file watching
    stopFileWatch();

    // Mark diff mode active
    state.diffModeActive = true;
    state.diffSelectedFile = selectFilePath;

    // Extract cwd from file path if provided (for worktree support)
    // We need to find the git root from the file path
    let cwdParam = '';
    if (selectFilePath) {
        // The path could be a file path or a directory path
        // For files: extract the directory part
        // For directories: use as-is
        // The backend will resolve the git root from whatever we pass
        cwdParam = `?cwd=${encodeURIComponent(selectFilePath)}`;
    }

    // Open the right pane
    openRightPane();

    // Show loading state in the sidebar
    if (dom.fileTreeContent) {
        dom.fileTreeContent.innerHTML = '<div class="preview-status visible loading">Loading diff...</div>';
    }

    // Update header
    const treeHeader = document.querySelector('.tree-header-title');
    if (treeHeader) {
        treeHeader.textContent = 'Changes';
    }

    try {
        const response = await fetch(`/api/diff/session/${state.activeSessionId}/files${cwdParam}`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to load diff');
        }

        const data = await response.json();
        state.diffFiles = data.files || [];
        state.diffType = data.diff_type;
        state.diffMainBranch = data.main_branch;
        state.diffCurrentBranch = data.current_branch;
        state.diffCwd = data.cwd || null;

        renderDiffFileList();

        // If a file was specified, select it
        if (selectFilePath && state.diffFiles.length > 0) {
            const file = state.diffFiles.find(f => selectFilePath.endsWith(f.path));
            if (file) {
                await openFileDiff(file.path);
            } else if (state.diffFiles.length > 0) {
                // File not found in changes, select first file
                await openFileDiff(state.diffFiles[0].path);
            }
        } else if (state.diffFiles.length > 0) {
            // Select first file by default
            await openFileDiff(state.diffFiles[0].path);
        } else {
            // No changes - show message
            renderNoChanges();
        }

    } catch (err) {
        console.error('Error loading diff:', err);
        if (dom.fileTreeContent) {
            dom.fileTreeContent.innerHTML = `<div class="preview-status visible error">${escapeHtml(err.message)}</div>`;
        }
    }
}

/**
 * Close diff view and return to file tree mode.
 */
export function closeDiffView() {
    state.diffModeActive = false;
    state.diffFiles = [];
    state.diffType = null;
    state.diffMainBranch = null;
    state.diffCurrentBranch = null;
    state.diffSelectedFile = null;
    state.diffCwd = null;

    // Reset header
    const treeHeader = document.querySelector('.tree-header-title');
    if (treeHeader) {
        treeHeader.textContent = 'Files';
    }

    // Clear content
    if (dom.fileTreeContent) {
        dom.fileTreeContent.innerHTML = '';
    }
    if (dom.previewContent) {
        dom.previewContent.innerHTML = `
            <div class="pending-session-placeholder" style="padding-top: 100px;">
                <p>Select a file to view</p>
            </div>
        `;
    }
}

/**
 * Render the list of changed files in the sidebar.
 */
function renderDiffFileList() {
    if (!dom.fileTreeContent) return;

    dom.fileTreeContent.innerHTML = '';

    // Header showing diff type
    const header = document.createElement('div');
    header.className = 'diff-header';

    if (state.diffType === 'uncommitted') {
        header.innerHTML = `<span class="diff-header-label">Uncommitted changes</span>`;
    } else if (state.diffType === 'vs_main' && state.diffMainBranch) {
        header.innerHTML = `
            <span class="diff-header-label">Changes vs ${escapeHtml(state.diffMainBranch)}</span>
            <span class="diff-header-branch">${escapeHtml(state.diffCurrentBranch || '')}</span>
        `;
    } else {
        header.innerHTML = `<span class="diff-header-label">No changes</span>`;
    }

    // Add close button to return to file tree
    const closeBtn = document.createElement('button');
    closeBtn.className = 'diff-close-btn';
    closeBtn.title = 'Close diff view';
    closeBtn.innerHTML = '&times;';
    closeBtn.addEventListener('click', () => {
        closeDiffView();
        // Reload file tree
        import('./filetree.js').then(m => m.loadFileTree(state.activeSessionId));
    });
    header.appendChild(closeBtn);

    dom.fileTreeContent.appendChild(header);

    // File list
    const ul = document.createElement('ul');
    ul.className = 'diff-file-list';

    for (const file of state.diffFiles) {
        const li = document.createElement('li');
        li.className = 'diff-file-item';
        if (state.diffSelectedFile === file.path) {
            li.classList.add('selected');
        }

        // Status indicator
        const statusClass = `diff-status-${file.status}`;

        // Stats
        const additions = file.additions > 0 ? `+${file.additions}` : '';
        const deletions = file.deletions > 0 ? `-${file.deletions}` : '';

        li.innerHTML = `
            <span class="diff-file-status ${statusClass}">${getStatusIcon(file.status)}</span>
            <span class="diff-file-name" title="${escapeHtml(file.path)}">${escapeHtml(file.path)}</span>
            <span class="diff-file-stats">
                ${additions ? `<span class="diff-additions">${additions}</span>` : ''}
                ${deletions ? `<span class="diff-deletions">${deletions}</span>` : ''}
            </span>
        `;

        li.addEventListener('click', () => openFileDiff(file.path));
        ul.appendChild(li);
    }

    dom.fileTreeContent.appendChild(ul);
}

/**
 * Get status icon for file status.
 */
function getStatusIcon(status) {
    switch (status) {
        case 'staged': return 'S';
        case 'modified': return 'M';
        case 'untracked': return 'U';
        case 'committed': return 'C';
        default: return '?';
    }
}

/**
 * Open a specific file's diff in the preview pane.
 */
export async function openFileDiff(filePath) {
    if (!state.activeSessionId) return;

    state.diffSelectedFile = filePath;

    // Update selection in file list
    document.querySelectorAll('.diff-file-item').forEach(item => {
        item.classList.remove('selected');
        if (item.querySelector('.diff-file-name')?.title === filePath) {
            item.classList.add('selected');
        }
    });

    // Update header
    const filename = filePath.split('/').pop();
    if (dom.previewFilename) dom.previewFilename.textContent = filename;
    if (dom.previewPath) dom.previewPath.textContent = filePath;

    // Hide file-specific controls
    if (dom.previewViewToggle) dom.previewViewToggle.style.display = 'none';
    if (dom.previewFollowToggle) dom.previewFollowToggle.style.display = 'none';
    if (dom.previewCopyBtn) dom.previewCopyBtn.style.display = 'none';

    // Show loading
    if (dom.previewContent) {
        dom.previewContent.innerHTML = '<div class="preview-status visible loading">Loading diff...</div>';
    }

    try {
        // Build URL with optional cwd parameter
        let url = `/api/diff/session/${state.activeSessionId}/file?path=${encodeURIComponent(filePath)}`;
        if (state.diffCwd) {
            url += `&cwd=${encodeURIComponent(state.diffCwd)}`;
        }

        const response = await fetch(url);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to load file diff');
        }

        const data = await response.json();
        renderFileDiff(data);

    } catch (err) {
        console.error('Error loading file diff:', err);
        if (dom.previewContent) {
            dom.previewContent.innerHTML = `<div class="preview-status visible error">${escapeHtml(err.message)}</div>`;
        }
    }
}

/**
 * Render the diff content in the preview pane.
 */
function renderFileDiff(data) {
    if (!dom.previewContent) return;

    if (!data.diff) {
        dom.previewContent.innerHTML = `
            <div class="diff-empty">
                <p>No changes in this file</p>
            </div>
        `;
        return;
    }

    // Parse and render the unified diff
    const diffHtml = parseDiffToHtml(data.diff);

    dom.previewContent.innerHTML = `
        <div class="diff-view">
            <div class="diff-status-bar">
                <span class="diff-status-label">${escapeHtml(data.status)}</span>
            </div>
            <div class="diff-content">
                ${diffHtml}
            </div>
        </div>
    `;
}

/**
 * Parse a unified diff string into HTML.
 */
function parseDiffToHtml(diffText) {
    const lines = diffText.split('\n');
    const chunks = [];
    let currentChunk = null;
    let inHeader = true;
    let headerLines = [];

    for (const line of lines) {
        // Check for diff header (file info)
        if (line.startsWith('diff --git') || line.startsWith('---') || line.startsWith('+++')) {
            if (inHeader) {
                headerLines.push(line);
            }
            continue;
        }

        // Check for hunk header
        if (line.startsWith('@@')) {
            inHeader = false;
            if (currentChunk) {
                chunks.push(currentChunk);
            }
            currentChunk = {
                header: line,
                lines: []
            };
            continue;
        }

        // Check for section separator (staged vs unstaged)
        if (line.startsWith('===')) {
            if (currentChunk) {
                chunks.push(currentChunk);
                currentChunk = null;
            }
            chunks.push({ separator: line });
            continue;
        }

        if (currentChunk) {
            currentChunk.lines.push(line);
        }
    }

    if (currentChunk) {
        chunks.push(currentChunk);
    }

    // Build HTML
    let html = '';

    for (const chunk of chunks) {
        if (chunk.separator) {
            html += `<div class="diff-separator">${escapeHtml(chunk.separator)}</div>`;
            continue;
        }

        html += '<div class="diff-hunk">';
        html += `<div class="diff-hunk-header">${escapeHtml(chunk.header)}</div>`;
        html += '<div class="diff-hunk-content">';

        for (const line of chunk.lines) {
            let lineClass = 'diff-line';
            let prefix = ' ';

            if (line.startsWith('+')) {
                lineClass += ' diff-line-add';
                prefix = '+';
            } else if (line.startsWith('-')) {
                lineClass += ' diff-line-del';
                prefix = '-';
            } else if (line.startsWith(' ')) {
                lineClass += ' diff-line-ctx';
            }

            // Remove the prefix from the line content
            const content = line.length > 0 ? line.substring(1) : '';

            html += `<div class="${lineClass}"><span class="diff-line-prefix">${prefix}</span><span class="diff-line-content">${escapeHtml(content)}</span></div>`;
        }

        html += '</div></div>';
    }

    return html;
}

/**
 * Render "no changes" message in preview pane.
 */
function renderNoChanges() {
    if (dom.previewContent) {
        let message = 'No changes detected';
        if (state.diffCurrentBranch && state.diffMainBranch && state.diffCurrentBranch === state.diffMainBranch) {
            message = `On ${state.diffMainBranch} branch with no uncommitted changes`;
        }

        dom.previewContent.innerHTML = `
            <div class="diff-empty">
                <p>${escapeHtml(message)}</p>
            </div>
        `;
    }
}

