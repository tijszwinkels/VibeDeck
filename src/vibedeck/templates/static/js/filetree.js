
import { dom, state } from './state.js';
import { openPreviewPane, closePreviewPane } from './preview.js';
import { isMobile, escapeHtml } from './utils.js';
import { openDiffView } from './diff.js';

// We store the current directory contents here
let currentTreeData = null;
let currentPath = null;
let homeDir = null;
let projectRoot = null; // The project directory for the active session
let currentSessionId = null; // The current session ID for file tree operations

// Git status for the footer
let gitStatus = null; // { diffType, fileCount, currentBranch, mainBranch }

// Tracking triple click
let lastClickTime = 0;
let clickCount = 0;
let lastClickedPath = null;
let pendingNavigationTimeout = null;

export function initFileTree() {
    // Toggle button in status bar
    if (dom.rightSidebarToggle) {
        dom.rightSidebarToggle.addEventListener('click', toggleRightSidebar);
    }

    // Collapse Tree Button
    if (dom.treeCollapseBtn) {
        dom.treeCollapseBtn.addEventListener('click', () => {
             dom.previewPane.classList.add('tree-collapsed');
        });
    }

    // Expand Tree Button
    if (dom.treeExpandBtn) {
        dom.treeExpandBtn.addEventListener('click', () => {
             dom.previewPane.classList.remove('tree-collapsed');
        });
    }

    // Resize handle
    const resizeHandle = document.getElementById('tree-resize-handle');
    if (resizeHandle) {
        resizeHandle.addEventListener('mousedown', startTreeResize);
    }

    document.addEventListener('mousemove', onTreeResize);
    document.addEventListener('mouseup', endTreeResize);

    // Initialize context menu
    initContextMenu();

    // Initialize drag-and-drop upload for file tree
    initFileTreeDropZone();
}

// Context menu for file/folder right-click actions
let contextMenu = null;

function initContextMenu() {
    // Create the context menu element
    contextMenu = document.createElement('div');
    contextMenu.className = 'tree-context-menu';
    contextMenu.innerHTML = `
        <button class="context-menu-item" data-action="copy-relative">Copy relative path</button>
        <button class="context-menu-item" data-action="copy-full">Copy full path</button>
        <hr class="context-menu-divider">
        <button class="context-menu-item" data-action="download">Download</button>
        <hr class="context-menu-divider">
        <button class="context-menu-item context-menu-item-danger" data-action="delete">Delete</button>
    `;
    contextMenu.style.display = 'none';
    document.body.appendChild(contextMenu);

    // Handle menu item clicks
    contextMenu.addEventListener('click', handleContextMenuClick);

    // Close menu when clicking elsewhere
    document.addEventListener('click', hideContextMenu);
    document.addEventListener('contextmenu', (e) => {
        // Close existing menu if right-clicking elsewhere
        if (!e.target.closest('.tree-summary')) {
            hideContextMenu();
        }
    });
}

function showContextMenu(e, itemPath, itemType = 'file') {
    e.preventDefault();

    if (!contextMenu) return;

    // Store the path and type for the menu action
    contextMenu.dataset.itemPath = itemPath;
    contextMenu.dataset.itemType = itemType;

    // Show/hide download button based on type (only for files)
    const downloadBtn = contextMenu.querySelector('[data-action="download"]');
    if (downloadBtn) {
        downloadBtn.style.display = itemType === 'directory' ? 'none' : 'block';
    }

    // Position the menu at cursor
    contextMenu.style.display = 'block';
    contextMenu.style.left = e.clientX + 'px';
    contextMenu.style.top = e.clientY + 'px';

    // Ensure menu stays within viewport
    const rect = contextMenu.getBoundingClientRect();
    if (rect.right > window.innerWidth) {
        contextMenu.style.left = (e.clientX - rect.width) + 'px';
    }
    if (rect.bottom > window.innerHeight) {
        contextMenu.style.top = (e.clientY - rect.height) + 'px';
    }
}

function hideContextMenu() {
    if (contextMenu) {
        contextMenu.style.display = 'none';
    }
}

function handleContextMenuClick(e) {
    const action = e.target.dataset.action;
    const itemPath = contextMenu.dataset.itemPath;
    const itemType = contextMenu.dataset.itemType;

    if (!action || !itemPath) return;

    if (action === 'copy-relative') {
        copyRelativePath(itemPath);
    } else if (action === 'copy-full') {
        copyFullPath(itemPath);
    } else if (action === 'download') {
        downloadFile(itemPath);
    } else if (action === 'delete') {
        deleteFile(itemPath);
    }

    hideContextMenu();
}

function downloadFile(filePath) {
    // Create a hidden anchor element to trigger download
    const downloadUrl = `/api/file/download?path=${encodeURIComponent(filePath)}`;
    const a = document.createElement('a');
    a.href = downloadUrl;
    a.download = filePath.split('/').pop(); // Suggest filename
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

function initFileTreeDropZone() {
    if (!dom.fileTreeContent) return;

    // Prevent default drag behaviors on the file tree content
    dom.fileTreeContent.addEventListener('dragover', handleDragOver);
    dom.fileTreeContent.addEventListener('dragenter', handleDragEnter);
    dom.fileTreeContent.addEventListener('dragleave', handleDragLeave);
    dom.fileTreeContent.addEventListener('drop', handleFileDrop);
}

function handleDragOver(e) {
    // Check if this is an external file drag (not internal file path drag)
    if (e.dataTransfer.types.includes('Files')) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
    }
}

function handleDragEnter(e) {
    if (e.dataTransfer.types.includes('Files')) {
        e.preventDefault();
        dom.fileTreeContent.classList.add('drag-over');
    }
}

function handleDragLeave(e) {
    // Only remove class if we're actually leaving the container
    if (!dom.fileTreeContent.contains(e.relatedTarget)) {
        dom.fileTreeContent.classList.remove('drag-over');
    }
}

async function handleFileDrop(e) {
    e.preventDefault();
    dom.fileTreeContent.classList.remove('drag-over');

    // Ignore internal file path drags
    if (e.dataTransfer.getData('application/x-file-path')) {
        return;
    }

    const files = e.dataTransfer.files;
    if (!files || files.length === 0) return;

    // Get target directory - use current path
    const targetDir = currentPath;
    if (!targetDir) {
        showFlashMessage('No target directory selected', 'error');
        return;
    }

    // Upload each file
    let successCount = 0;
    let errorCount = 0;

    for (const file of files) {
        try {
            const result = await uploadFile(file, targetDir);
            if (result.success) {
                successCount++;
            } else {
                errorCount++;
                console.error(`Upload failed for ${file.name}:`, result.error);
            }
        } catch (err) {
            errorCount++;
            console.error(`Upload failed for ${file.name}:`, err);
        }
    }

    // Show result
    if (successCount > 0 && errorCount === 0) {
        showFlashMessage(`Uploaded ${successCount} file${successCount > 1 ? 's' : ''}`, 'success');
    } else if (errorCount > 0 && successCount > 0) {
        showFlashMessage(`Uploaded ${successCount}, failed ${errorCount}`, 'warning');
    } else if (errorCount > 0) {
        showFlashMessage(`Upload failed for ${errorCount} file${errorCount > 1 ? 's' : ''}`, 'error');
    }

    // Refresh the file tree to show new files
    if (successCount > 0 && currentSessionId) {
        loadFileTree(currentSessionId, currentPath);
    }
}

async function uploadFile(file, targetDir) {
    const url = `/api/file/upload?directory=${encodeURIComponent(targetDir)}&filename=${encodeURIComponent(file.name)}`;

    const response = await fetch(url, {
        method: 'POST',
        body: file,
    });

    return await response.json();
}

function copyRelativePath(fullPath) {
    let relativePath = fullPath;

    // Make path relative to project root if available
    if (projectRoot && fullPath.startsWith(projectRoot)) {
        relativePath = fullPath.substring(projectRoot.length);
        // Remove leading slash
        if (relativePath.startsWith('/')) {
            relativePath = relativePath.substring(1);
        }
        // If empty (same as project root), use '.'
        if (!relativePath) {
            relativePath = '.';
        }
    }

    copyToClipboard(relativePath, 'Relative path copied');
}

function copyFullPath(fullPath) {
    copyToClipboard(fullPath, 'Full path copied');
}

async function deleteFile(filePath) {
    // Get filename for display
    const fileName = filePath.split('/').pop();

    // Confirm deletion
    if (!confirm(`Delete "${fileName}"?\n\nThis cannot be undone.`)) {
        return;
    }

    try {
        const response = await fetch('/api/file/delete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ path: filePath }),
        });

        const data = await response.json();

        if (response.ok && data.success) {
            showFlashMessage(`Deleted ${fileName}`, 'success');
            // Refresh the file tree - navigate to parent directory of deleted file
            const parentDir = filePath.substring(0, filePath.lastIndexOf('/'));
            if (currentSessionId) {
                loadFileTree(currentSessionId, parentDir);
            }
        } else {
            showFlashMessage(data.error || 'Failed to delete file', 'error');
        }
    } catch (err) {
        console.error('Delete failed:', err);
        showFlashMessage('Failed to delete file', 'error');
    }
}

function copyToClipboard(text, successMessage) {
    // Try modern clipboard API first, fall back to execCommand for non-HTTPS
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            showFlashMessage(successMessage, 'success');
        }).catch(() => {
            fallbackCopy(text, successMessage);
        });
    } else {
        fallbackCopy(text, successMessage);
    }
}

function fallbackCopy(text, successMessage) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    try {
        document.execCommand('copy');
        showFlashMessage(successMessage, 'success');
    } catch (err) {
        console.error('Failed to copy:', err);
        showFlashMessage('Failed to copy path', 'error');
    }
    document.body.removeChild(textarea);
}

function showFlashMessage(message, type) {
    const flash = document.getElementById('flash-message');
    if (flash) {
        flash.textContent = message;
        flash.className = 'flash-message visible ' + type;
        setTimeout(() => flash.classList.remove('visible'), 2000);
    }
}

function startTreeResize(e) {
    if (isMobile()) return;
    state.isTreeResizing = true;
    state.treeStartX = e.clientX;
    const sidebar = document.querySelector('.file-tree-sidebar');
    state.treeStartWidth = sidebar.getBoundingClientRect().width;
    e.target.classList.add('dragging');
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
}

function onTreeResize(e) {
    if (!state.isTreeResizing) return;
    const delta = e.clientX - state.treeStartX;
    let newWidth = state.treeStartWidth + delta;
    const maxTreeWidth = state.previewPaneWidth * 0.6;
    newWidth = Math.max(150, Math.min(maxTreeWidth, newWidth));
    state.treeSidebarWidth = newWidth;
    const sidebar = document.querySelector('.file-tree-sidebar');
    if (sidebar) sidebar.style.width = newWidth + 'px';
}

function endTreeResize() {
    if (state.isTreeResizing) {
        state.isTreeResizing = false;
        const resizeHandle = document.getElementById('tree-resize-handle');
        if (resizeHandle) resizeHandle.classList.remove('dragging');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        localStorage.setItem('treeSidebarWidth', state.treeSidebarWidth);
    }
}

// Load contents of a specific path (or project root if path is null)
export async function loadFileTree(sessionId, path = null) {
    if (!sessionId) return;
    if (!dom.fileTreeContent) return;

    // Store session ID for later use (e.g., after file deletion)
    currentSessionId = sessionId;

    // Show loading state
    dom.fileTreeContent.innerHTML = '<div class="preview-status visible loading">Loading...</div>';

    try {
        const url = path
            ? `/sessions/${sessionId}/tree?path=${encodeURIComponent(path)}`
            : `/sessions/${sessionId}/tree`;

        const response = await fetch(url);
        const data = await response.json();

        if (data.error) {
             dom.fileTreeContent.innerHTML = `<div class="preview-status visible warning">${escapeHtml(data.error)}</div>`;
             return;
        }

        currentTreeData = data.tree;
        homeDir = data.home;

        if (!currentTreeData) {
            dom.fileTreeContent.innerHTML = '<div class="preview-status visible warning">No tree data available</div>';
            return;
        }

        currentPath = currentTreeData.path;

        // Store project root from the response (first load without path = project root)
        if (data.projectRoot) {
            projectRoot = data.projectRoot;
        } else if (!path && currentTreeData.path) {
            // First call without explicit path - this is the project root
            projectRoot = currentTreeData.path;
        }

        // Fetch git status in the background (don't block rendering)
        fetchGitStatus(sessionId);

        renderCurrentPath();

    } catch (err) {
        if (dom.fileTreeContent) {
            dom.fileTreeContent.innerHTML = `<div class="preview-status visible error">Error loading tree: ${escapeHtml(err.message)}</div>`;
        }
    }
}

// Fetch git status for the current project
async function fetchGitStatus(sessionId) {
    try {
        const response = await fetch(`/api/diff/session/${sessionId}/files`);
        const data = await response.json();

        // Only show footer if there are changes to display
        if (data.diff_type === 'no_git') {
            gitStatus = null;
        } else if (data.diff_type === 'uncommitted' && data.files && data.files.length > 0) {
            gitStatus = {
                diffType: 'uncommitted',
                fileCount: data.files.length,
                currentBranch: data.current_branch,
                mainBranch: data.main_branch
            };
        } else if (data.diff_type === 'vs_main' && data.files && data.files.length > 0) {
            gitStatus = {
                diffType: 'vs_main',
                fileCount: data.files.length,
                currentBranch: data.current_branch,
                mainBranch: data.main_branch
            };
        } else {
            // No changes (on main with no uncommitted, or no branch changes)
            gitStatus = null;
        }

        // Re-render to include the footer
        renderGitFooter();
    } catch (err) {
        // Silently fail - git status is optional
        gitStatus = null;
    }
}

// Set the project root (called when session changes)
export function setProjectRoot(path) {
    projectRoot = path;
}

function formatPath(path) {
    if (!path) return '';
    if (homeDir && path.startsWith(homeDir)) {
        return '~' + path.substring(homeDir.length);
    }
    return path;
}

function renderCurrentPath() {
    if (!currentTreeData || !dom.fileTreeContent) return;
    
    dom.fileTreeContent.innerHTML = '';
    
    // Render Header (Current Path)
    const header = document.createElement('div');
    header.className = 'tree-current-path';
    header.textContent = formatPath(currentPath);
    header.title = currentPath;
    dom.fileTreeContent.appendChild(header);
    
    const rootUl = document.createElement('ul');
    rootUl.className = 'tree-root';
    
    // Add ".." if not at root of filesystem (or simple check: path is not /)
    // Actually, we can assume we can go up unless path is /
    if (currentPath !== '/' && currentPath !== 'C:\\') {
        const parentLi = document.createElement('li');
        parentLi.className = 'tree-item';
        
        const parentDiv = document.createElement('div');
        parentDiv.className = 'tree-summary';
        parentDiv.innerHTML = `<span class="tree-icon tree-icon-folder"></span> ..`;
        parentDiv.addEventListener('click', () => {
            // Navigate Up
            // Simple string manipulation to find parent
            // Handles both / and \ (if Windows, though backend normalizes to / usually)
            // Python Path.resolve() usually gives system separators.
            // Let's assume standard posix or assume backend handles separator
            // Actually better to just take parent dir string
            let parentPath = currentPath.substring(0, currentPath.lastIndexOf('/'));
            if (!parentPath && currentPath.startsWith('/')) parentPath = '/'; // Root
            if (!parentPath) parentPath = currentPath.substring(0, currentPath.lastIndexOf('\\')); // Windows fallback
            
            if (parentPath && parentPath !== currentPath) {
                loadFileTree(state.activeSessionId, parentPath);
            }
        });
        
        parentLi.appendChild(parentDiv);
        rootUl.appendChild(parentLi);
    }
    
    if (currentTreeData.children) {
        const sortedChildren = sortChildren(currentTreeData.children);
        sortedChildren.forEach(child => {
            rootUl.appendChild(createBrowserItem(child));
        });
    }
    
    dom.fileTreeContent.appendChild(rootUl);
}

function sortChildren(children) {
    return children.sort((a, b) => {
        if (a.type === b.type) return a.name.localeCompare(b.name);
        return a.type === 'directory' ? -1 : 1;
    });
}

function createBrowserItem(item) {
    const li = document.createElement('li');
    li.className = 'tree-item';

    const div = document.createElement('div');
    div.className = 'tree-summary';
    div.dataset.path = item.path;

    // Create icon span
    const icon = document.createElement('span');
    icon.className = item.type === 'directory' ? 'tree-icon tree-icon-folder' : 'tree-icon tree-icon-file';
    div.appendChild(icon);

    // Add file/folder name as text node (prevents XSS from malicious filenames)
    div.appendChild(document.createTextNode(' ' + item.name));

    // Right-click context menu for all items
    div.addEventListener('contextmenu', (e) => {
        showContextMenu(e, item.path, item.type);
    });

    // Make files draggable for inserting path into chat
    if (item.type !== 'directory') {
        div.setAttribute('draggable', 'true');
        div.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('text/plain', item.path);
            e.dataTransfer.setData('application/x-file-path', item.path);
            e.dataTransfer.effectAllowed = 'copy';
        });
    }

    if (item.type === 'directory') {
        // Single Click -> Navigate
        // Triple Click -> New Session
        div.addEventListener('click', (e) => {
            handleFolderClick(e, item.path);
        });
    } else {
        div.addEventListener('click', (e) => {
            // Highlight selection
            document.querySelectorAll('.tree-summary.selected').forEach(el => el.classList.remove('selected'));
            div.classList.add('selected');

            // Open preview
            openPreviewPane(item.path);
        });
    }

    li.appendChild(div);
    return li;
}

function handleFolderClick(e, path) {
    const now = Date.now();

    // Clear any pending navigation from previous clicks
    if (pendingNavigationTimeout) {
        clearTimeout(pendingNavigationTimeout);
        pendingNavigationTimeout = null;
    }

    if (path === lastClickedPath && (now - lastClickTime) < 500) {
        clickCount++;
    } else {
        clickCount = 1;
        lastClickedPath = path;
    }
    lastClickTime = now;

    if (clickCount === 3) {
        // Triple Click Detected! Start new session in this folder.
        startNewSessionInFolder(path);
        clickCount = 0;
        return;
    }

    // Delay navigation slightly to allow for triple-click detection.
    // Without this delay, the folder would navigate away before we can detect the third click.
    pendingNavigationTimeout = setTimeout(() => {
        pendingNavigationTimeout = null;
        loadFileTree(state.activeSessionId, path);
    }, 300);
}

function startNewSessionInFolder(path) {
    // Open the new session modal with this path pre-filled
    if (dom.newSessionModal) {
        dom.modalCwd.value = path;
        dom.newSessionModal.showModal();
        
        // Optional: Flash message
        const flash = document.getElementById('flash-message');
        if (flash) {
            flash.textContent = `Starting session in ${formatPath(path)}`;
            flash.className = 'flash-message visible info';
            setTimeout(() => flash.classList.remove('visible'), 2000);
        }
    }
}

function toggleRightSidebar() {
    if (state.previewPaneOpen) {
        closePreviewPane(false);
    } else {
        openRightPane();
    }
}

export function openRightPane() {
    dom.previewPane.classList.add('open');
    dom.mainContent.classList.add('preview-open');
    dom.inputBar.classList.add('preview-open');
    dom.floatingControls.classList.add('preview-open');
    state.previewPaneOpen = true;

    // Apply persisted tree sidebar width
    const treeSidebar = document.querySelector('.file-tree-sidebar');
    if (treeSidebar) {
        treeSidebar.style.width = state.treeSidebarWidth + 'px';
    }

    if (!dom.fileTreeContent.innerHTML) {
         // Initial load (default to project root if nothing loaded)
         loadFileTree(state.activeSessionId);
    }
}

export function syncTreeToFile(filePath) {
    // Since we are lazy loading, we can't easily sync to a deep file if we are at root.
    // We would need to "walk down" the tree fetching each level.
    // That's complex.
    // Simple approach: Just load the directory of the file directly!

    // Get parent directory
    // Assuming forward slashes from server or normalized
    let parentPath = filePath.substring(0, filePath.lastIndexOf('/'));

    // Load that directory
    if (parentPath) {
        loadFileTree(state.activeSessionId, parentPath);
        // Note: highlighting the specific file might happen after load
        // We can add a "then" to highlight
    }
}

// Render or update the git footer in the file tree
function renderGitFooter() {
    if (!dom.fileTreeContent) return;

    // Remove existing footer if any
    const existingFooter = dom.fileTreeContent.querySelector('.git-changes-footer');
    if (existingFooter) {
        existingFooter.remove();
    }

    // Don't show footer if no git status or no changes
    if (!gitStatus) return;

    const footer = document.createElement('div');
    footer.className = 'git-changes-footer';

    const button = document.createElement('button');
    button.className = 'git-changes-btn';

    if (gitStatus.diffType === 'uncommitted') {
        button.innerHTML = `<span class="git-icon">●</span> Uncommitted changes <span class="git-count">${gitStatus.fileCount}</span>`;
        button.title = `${gitStatus.fileCount} file${gitStatus.fileCount !== 1 ? 's' : ''} with uncommitted changes`;
    } else if (gitStatus.diffType === 'vs_main') {
        const branchName = gitStatus.currentBranch || 'branch';
        button.innerHTML = `<span class="git-icon">⎇</span> ${escapeHtml(branchName)} vs ${escapeHtml(gitStatus.mainBranch || 'main')} <span class="git-count">${gitStatus.fileCount}</span>`;
        button.title = `${gitStatus.fileCount} file${gitStatus.fileCount !== 1 ? 's' : ''} changed vs ${gitStatus.mainBranch || 'main'}`;
    }

    button.addEventListener('click', () => {
        openDiffView();
    });

    footer.appendChild(button);
    dom.fileTreeContent.appendChild(footer);
}
