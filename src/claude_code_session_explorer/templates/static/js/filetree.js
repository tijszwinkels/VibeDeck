
import { dom, state } from './state.js';
import { openPreviewPane, closePreviewPane } from './preview.js';
import { isMobile } from './utils.js';

// We store the full tree data here so we can navigate client-side
let fullTreeData = null;
let currentPath = null; // The path of the directory we are currently viewing
let homeDir = null; // Home directory path from server

export function initFileTree() {
    // Toggle button in status bar
    if (dom.rightSidebarToggle) {
        dom.rightSidebarToggle.addEventListener('click', toggleRightSidebar);
    }
    
    // Collapse Tree Button (in tree header)
    if (dom.treeCollapseBtn) {
        dom.treeCollapseBtn.addEventListener('click', () => {
             dom.previewPane.classList.add('tree-collapsed');
        });
    }
    
    // Expand Tree Button (in preview header, visible when collapsed)
    if (dom.treeExpandBtn) {
        dom.treeExpandBtn.addEventListener('click', () => {
             dom.previewPane.classList.remove('tree-collapsed');
        });
    }
    
    // Resize handle for tree split
    const resizeHandle = document.getElementById('tree-resize-handle');
    if (resizeHandle) {
        resizeHandle.addEventListener('mousedown', function(e) {
            if (isMobile()) return;
            state.isTreeResizing = true;
            state.treeStartX = e.clientX;
            // Get current width (computed style or inline)
            const sidebar = document.querySelector('.file-tree-sidebar');
            state.treeStartWidth = sidebar.getBoundingClientRect().width;
            
            resizeHandle.classList.add('dragging');
            document.body.style.cursor = 'ew-resize';
            document.body.style.userSelect = 'none';
            e.preventDefault();
        });
    }
    
    // Global mouse events for dragging (shared with preview pane resize, but separate logic)
    document.addEventListener('mousemove', function(e) {
        if (!state.isTreeResizing) return;
        
        // Calculate new width
        // For tree sidebar, resizing moves the RIGHT edge.
        // It's inside a right-aligned pane, but flex direction is row (left-to-right).
        // So moving mouse RIGHT increases width.
        const delta = e.clientX - state.treeStartX;
        let newWidth = state.treeStartWidth + delta;
        
        // Clamp width: min 150px, max 60% of preview pane
        const maxTreeWidth = state.previewPaneWidth * 0.6;
        newWidth = Math.max(150, Math.min(maxTreeWidth, newWidth));
        
        state.treeSidebarWidth = newWidth;
        const sidebar = document.querySelector('.file-tree-sidebar');
        if (sidebar) {
            sidebar.style.width = newWidth + 'px';
        }
    });
    
    document.addEventListener('mouseup', function() {
        if (state.isTreeResizing) {
            state.isTreeResizing = false;
            const resizeHandle = document.getElementById('tree-resize-handle');
            if (resizeHandle) resizeHandle.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            // Could save to localStorage here
        }
    });
}

export async function loadFileTree(sessionId) {
    if (!sessionId) return;
    if (!dom.fileTreeContent) return;
    
    // Show loading state if we don't have data yet
    if (!fullTreeData) {
        dom.fileTreeContent.innerHTML = '<div class="preview-status visible loading">Loading tree...</div>';
    }
    
    try {
        const response = await fetch(`/sessions/${sessionId}/tree`);
        const data = await response.json();
        
        if (data.error || !data.tree) {
             dom.fileTreeContent.innerHTML = `<div class="preview-status visible warning">${data.error || 'No tree data'}</div>`;
             return;
        }
        
        fullTreeData = data.tree;
        homeDir = data.home;
        
        // Reset path to root when loading new session
        currentPath = fullTreeData.path; 
        
        // Always render tree if we have data
        renderCurrentPath();
        
    } catch (err) {
        if (dom.fileTreeContent) {
            dom.fileTreeContent.innerHTML = `<div class="preview-status visible error">Error loading tree: ${err.message}</div>`;
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

function findNodeByPath(root, path) {
    if (root.path === path) return root;
    if (!root.children) return null;
    
    for (const child of root.children) {
        if (child.path === path) return child;
        if (child.type === 'directory') {
            const found = findNodeByPath(child, path);
            if (found) return found;
        }
    }
    return null;
}

function findParentNode(root, path) {
     if (!root.children) return null;
     
     for (const child of root.children) {
         if (child.path === path) return root;
         if (child.type === 'directory') {
             const found = findParentNode(child, path);
             if (found) return found;
         }
     }
     return null;
}

function renderCurrentPath() {
    if (!fullTreeData || !dom.fileTreeContent) return;
    
    const node = findNodeByPath(fullTreeData, currentPath);
    if (!node) {
        // Fallback to root
        currentPath = fullTreeData.path;
        renderCurrentPath();
        return;
    }
    
    dom.fileTreeContent.innerHTML = '';
    
    // Render Header (Current Path)
    const header = document.createElement('div');
    header.className = 'tree-current-path';
    
    // Use formatted path with ~ notation
    header.textContent = formatPath(node.path);
    header.title = node.path;
    dom.fileTreeContent.appendChild(header);
    
    const rootUl = document.createElement('ul');
    rootUl.className = 'tree-root';
    
    // Add ".." if not at root
    if (node.path !== fullTreeData.path) {
        const parentLi = document.createElement('li');
        parentLi.className = 'tree-item';
        
        const parentDiv = document.createElement('div');
        parentDiv.className = 'tree-summary';
        parentDiv.innerHTML = `<span class="tree-icon tree-icon-folder"></span> ..`;
        parentDiv.addEventListener('click', () => {
            const parent = findParentNode(fullTreeData, currentPath);
            if (parent) {
                currentPath = parent.path;
                renderCurrentPath();
            }
        });
        
        parentLi.appendChild(parentDiv);
        rootUl.appendChild(parentLi);
    }
    
    if (node.children) {
        const sortedChildren = sortChildren(node.children);
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
    
    if (item.type === 'directory') {
        div.innerHTML = `<span class="tree-icon tree-icon-folder"></span> ${item.name}`;
        div.addEventListener('click', () => {
            // Navigate into directory
            currentPath = item.path;
            renderCurrentPath();
        });
    } else {
        div.innerHTML = `<span class="tree-icon tree-icon-file"></span> ${item.name}`;
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

function toggleRightSidebar() {
    if (state.previewPaneOpen) {
        // If open, close it
        closePreviewPane(false);
    } else {
        // Open
        openRightPane();
    }
}

export function openRightPane() {
    dom.previewPane.classList.add('open');
    dom.mainContent.classList.add('preview-open');
    dom.inputBar.classList.add('preview-open');
    dom.floatingControls.classList.add('preview-open');
    state.previewPaneOpen = true;
    
    // Make sure we have a tree render
    if (!dom.fileTreeContent.innerHTML) {
         renderCurrentPath();
    }
}

export function syncTreeToFile(filePath) {
    if (!fullTreeData) return;
    
    const parent = findParentNode(fullTreeData, filePath);
    if (parent) {
        currentPath = parent.path;
        renderCurrentPath();
        
        // Highlight the file
        setTimeout(() => {
            const fileEl = dom.fileTreeContent.querySelector(`.tree-summary[data-path="${filePath}"]`);
            if (fileEl) {
                document.querySelectorAll('.tree-summary.selected').forEach(el => el.classList.remove('selected'));
                fileEl.classList.add('selected');
                fileEl.scrollIntoView({ block: 'nearest' });
            }
        }, 0);
    }
}

// These are no longer needed as we always show both
export function showTreeView() {}
export function showPreviewView(filename) {}
