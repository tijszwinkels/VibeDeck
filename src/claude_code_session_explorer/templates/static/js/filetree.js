
import { dom, state } from './state.js';
import { openPreviewPane, closePreviewPane } from './preview.js';
import { isMobile, escapeHtml } from './utils.js';

// We store the current directory contents here
let currentTreeData = null;
let currentPath = null; 
let homeDir = null; 

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
    }
}

// Load contents of a specific path (or project root if path is null)
export async function loadFileTree(sessionId, path = null) {
    if (!sessionId) return;
    if (!dom.fileTreeContent) return;
    
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
        currentPath = currentTreeData.path;

        renderCurrentPath();

    } catch (err) {
        if (dom.fileTreeContent) {
            dom.fileTreeContent.innerHTML = `<div class="preview-status visible error">Error loading tree: ${escapeHtml(err.message)}</div>`;
        }
    }
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
