// Main application entry point

import { initDom, dom, state } from './state.js';
import { copyToClipboard } from './utils.js';
import {
    initSidebar, initThemeToggle, initAutoSwitch, initStatusColors, initHideTools,
    initAutoScroll, initScrollButtons, initUserNavigation, initSearch
} from './ui.js';
import { initPreviewPane } from './preview.js';
import { initFileTree } from './filetree.js';
import { initGroupBySelect, initOrderBySelect, initCopyButtons, reorderSidebar, loadArchivedSessions } from './sessions.js';
import { initMessaging } from './messaging.js';
import { initModal } from './modal.js';
import { connect, initVisibilityHandler } from './connection.js';
import { initCommandButtons } from './commands.js';
import { initSidebarContextMenu } from './sidebar-context-menu.js';
import { initPermissions } from './permissions.js';
import { initDiffView } from './diff.js';

// Initialize the application
function init() {
    // Initialize DOM element references
    initDom();

    // Initialize UI components
    initSidebar();
    initThemeToggle();
    initAutoSwitch();
    initStatusColors();
    initHideTools();
    initAutoScroll();
    initScrollButtons();
    initUserNavigation();
    initSearch();

    // Initialize preview pane
    initPreviewPane();
    initFileTree();

    // Initialize session management
    initGroupBySelect(reorderSidebar);
    initOrderBySelect(reorderSidebar);
    initCopyButtons();
    initSidebarContextMenu();

    // Initialize messaging (input bar)
    initMessaging();

    // Initialize modal
    initModal();

    // Initialize permissions modal
    initPermissions();

    // Initialize diff view
    initDiffView();

    // Initialize global click handler for copy buttons (event delegation)
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.copy-btn');
        if (!btn) return;

        e.stopPropagation();
        e.preventDefault();

        // Check if this is a file path copy button
        const fullpath = btn.closest('.file-tool-fullpath[data-copy-path]');
        if (fullpath) {
            const path = fullpath.dataset.copyPath;
            if (path) {
                copyToClipboard(path, btn);
            }
            return;
        }

        // Otherwise it's a code block copy button
        const wrapper = btn.closest('.copy-wrapper');
        if (wrapper) {
            const pre = wrapper.querySelector('pre');
            if (pre) {
                copyToClipboard(pre.textContent, btn);
            } else {
                // Fallback: copy wrapper text content (excluding button)
                const clone = wrapper.cloneNode(true);
                const btnClone = clone.querySelector('.copy-btn');
                if (btnClone) btnClone.remove();
                copyToClipboard(clone.textContent.trim(), btn);
            }
        }
    });

    // Load archived sessions from server before connecting
    loadArchivedSessions().then(() => {
        // Connect to SSE and start receiving events
        connect();
    });

    // Initialize visibility change handler for reconnection
    initVisibilityHandler();

    // Initialize VibeDeck command button handlers
    initCommandButtons();
}

// Run initialization when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
