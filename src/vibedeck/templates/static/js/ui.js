// UI module - sidebar, theme, tooltips, flash messages, scroll controls

import { dom, state, dateCategoryLabels } from './state.js';
import { isMobile, escapeHtml, formatTimestamp, formatTokenCount, formatCost, formatModelName, getDateCategory } from './utils.js';

// Sidebar state management
export function updateSidebarState() {
    if (state.sidebarOpen) {
        dom.sidebar.classList.remove('closed');
        dom.mainContent.classList.remove('sidebar-closed');
        dom.inputBar.classList.remove('sidebar-closed');
        if (isMobile()) {
            dom.sidebarOverlay.classList.add('visible');
        }
    } else {
        dom.sidebar.classList.add('closed');
        dom.mainContent.classList.add('sidebar-closed');
        dom.inputBar.classList.add('sidebar-closed');
        dom.sidebarOverlay.classList.remove('visible');
    }
    localStorage.setItem('sidebarOpen', state.sidebarOpen);
}

export function toggleSidebar() {
    state.sidebarOpen = !state.sidebarOpen;
    updateSidebarState();
}

// Initialize sidebar
export function initSidebar() {
    dom.hamburgerBtn.addEventListener('click', toggleSidebar);
    dom.sidebarOverlay.addEventListener('click', function() {
        state.sidebarOpen = false;
        updateSidebarState();
    });

    // Handle window resize
    window.addEventListener('resize', function() {
        if (!isMobile()) {
            dom.sidebarOverlay.classList.remove('visible');
        } else if (state.sidebarOpen) {
            dom.sidebarOverlay.classList.add('visible');
        }
    });

    // Initialize sidebar state
    updateSidebarState();

    // Initialize sidebar width from localStorage
    document.documentElement.style.setProperty('--sidebar-width', state.sidebarWidth + 'px');

    // Sidebar resize handle
    dom.sidebarResizeHandle.addEventListener('mousedown', function(e) {
        if (isMobile()) return;
        state.isSidebarResizing = true;
        state.sidebarStartX = e.clientX;
        state.sidebarStartWidth = state.sidebarWidth;
        dom.sidebarResizeHandle.classList.add('dragging');
        document.body.style.cursor = 'ew-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });

    document.addEventListener('mousemove', function(e) {
        if (!state.isSidebarResizing) return;

        const delta = e.clientX - state.sidebarStartX;
        let newWidth = state.sidebarStartWidth + delta;

        // Clamp width: min 200px, max 500px
        newWidth = Math.max(200, Math.min(500, newWidth));

        state.sidebarWidth = newWidth;
        document.documentElement.style.setProperty('--sidebar-width', newWidth + 'px');
    });

    document.addEventListener('mouseup', function() {
        if (state.isSidebarResizing) {
            state.isSidebarResizing = false;
            dom.sidebarResizeHandle.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            localStorage.setItem('sidebarWidth', state.sidebarWidth);
        }
    });
}

// Theme management
export function initTheme() {
    const savedTheme = localStorage.getItem('theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = savedTheme || (prefersDark ? 'dark' : 'light');
    setTheme(theme);
}

function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
    dom.themeToggle.innerHTML = theme === 'dark' ? '&#9788;' : '&#9790;';
    dom.themeToggle.title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    setTheme(current === 'dark' ? 'light' : 'dark');
}

export function initThemeToggle() {
    dom.themeToggle.addEventListener('click', toggleTheme);
    initTheme();
}

// Auto-switch UI
export function updateAutoSwitchUI() {
    state.autoSwitch = dom.autoSwitchCheckbox.checked;
    if (state.autoSwitch) {
        dom.autoSwitchLabel.classList.add('active');
    } else {
        dom.autoSwitchLabel.classList.remove('active');
    }
    localStorage.setItem('autoSwitch', state.autoSwitch);
}

export function initAutoSwitch() {
    dom.autoSwitchCheckbox.checked = state.autoSwitch;
    dom.autoSwitchCheckbox.addEventListener('change', updateAutoSwitchUI);
    updateAutoSwitchUI();
}

// Status colors UI
export function updateStatusColorsUI() {
    state.statusColors = dom.statusColorsCheckbox.checked;
    if (state.statusColors) {
        dom.sidebar.classList.add('status-colors-enabled');
    } else {
        dom.sidebar.classList.remove('status-colors-enabled');
    }
    localStorage.setItem('statusColors', state.statusColors);
}

export function initStatusColors() {
    if (state.titleColorsEnabled) {
        dom.statusColorsCheckbox.checked = state.statusColors;
        dom.statusColorsCheckbox.addEventListener('change', updateStatusColorsUI);
        updateStatusColorsUI();
    } else {
        // Hide the checkbox when feature is not enabled
        document.getElementById('status-colors-label').style.display = 'none';
    }
}

// Hide tools UI
export function updateHideToolsUI() {
    state.hideTools = dom.hideToolsCheckbox.checked;
    if (state.hideTools) {
        document.body.classList.add('hide-tools');
    } else {
        document.body.classList.remove('hide-tools');
    }
    localStorage.setItem('hideTools', state.hideTools);
}

export function initHideTools() {
    dom.hideToolsCheckbox.checked = state.hideTools;
    dom.hideToolsCheckbox.addEventListener('change', updateHideToolsUI);
    updateHideToolsUI();
}

// Auto-scroll UI (floating controls)
export function updateAutoScrollUI() {
    state.autoScroll = dom.autoScrollCheckbox.checked;
    if (state.autoScroll) {
        dom.autoScrollFloat.classList.add('active');
    } else {
        dom.autoScrollFloat.classList.remove('active');
    }
    localStorage.setItem('autoScroll', state.autoScroll);
}

export function initAutoScroll() {
    dom.autoScrollCheckbox.checked = state.autoScroll;
    dom.autoScrollCheckbox.addEventListener('change', updateAutoScrollUI);
    updateAutoScrollUI();
}

// Scroll buttons
export function initScrollButtons() {
    dom.scrollTopBtn.addEventListener('click', function() {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    dom.scrollBottomBtn.addEventListener('click', function() {
        window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
    });
}

// User message navigation
export function getUserMessages() {
    // Get user messages in the active session (class="message user" but NOT "tool-reply")
    const session = state.sessions.get(state.activeSessionId);
    if (!session || !session.container) return [];
    return Array.from(session.container.querySelectorAll('.message.user:not(.tool-reply)'));
}

function findCurrentUserMessageIndex() {
    const userMessages = getUserMessages();
    if (userMessages.length === 0) return -1;

    const scrollTop = window.scrollY;
    const viewportMiddle = scrollTop + window.innerHeight / 3;

    // Find the user message closest to (or just above) the viewport middle
    for (let i = userMessages.length - 1; i >= 0; i--) {
        const rect = userMessages[i].getBoundingClientRect();
        const msgTop = rect.top + scrollTop;
        if (msgTop <= viewportMiddle) {
            return i;
        }
    }
    return -1;
}

export function updateUserNavButtons() {
    const userMessages = getUserMessages();
    const currentIndex = findCurrentUserMessageIndex();
    dom.prevUserBtn.disabled = currentIndex <= 0;
    dom.nextUserBtn.disabled = currentIndex >= userMessages.length - 1 || userMessages.length === 0;
}

export function initUserNavigation() {
    dom.prevUserBtn.addEventListener('click', function() {
        const userMessages = getUserMessages();
        const currentIndex = findCurrentUserMessageIndex();
        if (currentIndex > 0) {
            userMessages[currentIndex - 1].scrollIntoView({ behavior: 'smooth', block: 'start' });
            // Disable auto-scroll when manually navigating
            state.autoScroll = false;
            dom.autoScrollCheckbox.checked = false;
            dom.autoScrollFloat.classList.remove('active');
            localStorage.setItem('autoScroll', 'false');
        }
    });

    dom.nextUserBtn.addEventListener('click', function() {
        const userMessages = getUserMessages();
        const currentIndex = findCurrentUserMessageIndex();
        if (currentIndex < userMessages.length - 1) {
            userMessages[currentIndex + 1].scrollIntoView({ behavior: 'smooth', block: 'start' });
            // Disable auto-scroll when manually navigating
            state.autoScroll = false;
            dom.autoScrollCheckbox.checked = false;
            dom.autoScrollFloat.classList.remove('active');
            localStorage.setItem('autoScroll', 'false');
        }
    });

    // Update button states on scroll
    window.addEventListener('scroll', function() {
        updateUserNavButtons();
    });
}

// Search/filter functionality
export function filterSidebar(query) {
    const lowerQuery = query.toLowerCase().trim();

    state.projects.forEach(function(project) {
        let projectHasMatch = false;

        project.sessions.forEach(function(sessionId) {
            const session = state.sessions.get(sessionId);
            if (!session) return;

            if (!lowerQuery) {
                session.sidebarItem.style.display = '';
                projectHasMatch = true;
                return;
            }

            const titleMatch = (session.displayTitle || '').toLowerCase().includes(lowerQuery);
            const projectMatch = project.name.toLowerCase().includes(lowerQuery);
            const contentMatch = session.container.textContent.toLowerCase().includes(lowerQuery);

            if (titleMatch || projectMatch || contentMatch) {
                session.sidebarItem.style.display = '';
                projectHasMatch = true;
            } else {
                session.sidebarItem.style.display = 'none';
            }
        });

        if (project.element) {
            project.element.style.display = projectHasMatch ? '' : 'none';
        }
    });
}

export function initSearch() {
    dom.searchInput.addEventListener('input', function() {
        filterSidebar(this.value);
    });
}

// Status bar
export function updateStatus(status, text) {
    dom.statusBar.className = 'status-bar ' + status;
    dom.statusText.textContent = text;
}

// Flash message
export function showFlash(message, type = 'info', duration = 3000) {
    // Clear any existing timeout
    if (state.flashTimeout) {
        clearTimeout(state.flashTimeout);
    }
    // Set content and type
    dom.flashMessage.textContent = message;
    dom.flashMessage.className = 'flash-message ' + type;
    // Show
    requestAnimationFrame(() => {
        dom.flashMessage.classList.add('visible');
    });
    // Auto-hide
    state.flashTimeout = setTimeout(() => {
        dom.flashMessage.classList.remove('visible');
    }, duration);
}

// Session tooltip
export function showTooltip(sessionId, e) {
    const session = state.sessions.get(sessionId);
    if (!session) return;

    if (state.tooltipTimeout) clearTimeout(state.tooltipTimeout);

    state.tooltipTimeout = setTimeout(function() {
        const started = formatTimestamp(session.startedAt);
        const updated = formatTimestamp(session.lastUpdatedAt);
        const fullMessage = session.firstMessage || 'No message';

        // Title at the top (if available from summary)
        let titleHtml = '';
        if (session.summaryTitle) {
            titleHtml = `<div class="tooltip-title">${escapeHtml(session.summaryTitle)}</div>`;
        }

        // Branch (if available from summary)
        let branchHtml = '';
        if (session.summaryBranch) {
            branchHtml = `
                <div class="tooltip-label">Branch</div>
                <div class="tooltip-value">${escapeHtml(session.summaryBranch)}</div>
            `;
        }

        let backendHtml = '';
        if (session.backend) {
            backendHtml = `
                <div class="tooltip-label">Backend</div>
                <div class="tooltip-value">${escapeHtml(session.backend)}</div>
            `;
        }

        // Summary section (shown if summary data available)
        let summaryHtml = '';
        if (session.summaryShort || session.summaryExecutive) {
            // Prefer executive summary, fall back to short summary
            const summaryText = session.summaryExecutive || session.summaryShort;
            summaryHtml = `
                <div class="tooltip-label">Summary</div>
                <div class="tooltip-value tooltip-summary">${escapeHtml(summaryText)}</div>
            `;
        }

        let usageHtml = '';
        if (session.tokenUsage && session.tokenUsage.message_count > 0) {
            const u = session.tokenUsage;
            let modelsHtml = '';
            if (u.models && u.models.length > 0) {
                const modelNames = u.models.map(formatModelName).join(', ');
                modelsHtml = `
                    <div class="tooltip-label">Models</div>
                    <div class="tooltip-value tooltip-models">${escapeHtml(modelNames)}</div>
                `;
            }
            usageHtml = `
                ${modelsHtml}
                <div class="tooltip-label">Token Usage</div>
                <div class="tooltip-value tooltip-usage">
                    <span class="usage-item">In: ${formatTokenCount(u.input_tokens)}</span>
                    <span class="usage-item">Out (est): ${formatTokenCount(u.output_tokens)}</span>
                    <span class="usage-item">Cache&uarr;: ${formatTokenCount(u.cache_creation_tokens)}</span>
                    <span class="usage-item">Cache&darr;: ${formatTokenCount(u.cache_read_tokens)}</span>
                </div>
                <div class="tooltip-label">Estimated Cost</div>
                <div class="tooltip-value tooltip-cost">${formatCost(u.cost)}</div>
            `;
        }

        dom.sessionTooltip.innerHTML = `
            ${titleHtml}
            ${branchHtml}
            ${backendHtml}
            ${summaryHtml}
            <div class="tooltip-label">Created</div>
            <div class="tooltip-value">${escapeHtml(started)}</div>
            <div class="tooltip-label">Last updated</div>
            <div class="tooltip-value">${escapeHtml(updated)}</div>
            ${usageHtml}
            <div class="tooltip-label">First message</div>
            <div class="tooltip-value tooltip-message">${escapeHtml(fullMessage)}</div>
        `;
        dom.sessionTooltip.style.display = 'block';
        positionTooltip(e);
    }, 300);
}

export function showDateCategoryTooltip(projectName, category, e) {
    const project = state.projects.get(projectName);
    if (!project) return;

    if (state.tooltipTimeout) clearTimeout(state.tooltipTimeout);

    state.tooltipTimeout = setTimeout(function() {
        // Aggregate stats for sessions in this date category
        let totalInput = 0, totalOutput = 0, totalCacheCreate = 0, totalCacheRead = 0, totalCost = 0;
        let sessionCount = 0;

        project.sessions.forEach(function(sessionId) {
            const session = state.sessions.get(sessionId);
            if (!session) return;
            const sessionCategory = getDateCategory(session.lastUpdatedAt);
            if (sessionCategory !== category) return;

            sessionCount++;
            if (session.tokenUsage) {
                totalInput += session.tokenUsage.input_tokens || 0;
                totalOutput += session.tokenUsage.output_tokens || 0;
                totalCacheCreate += session.tokenUsage.cache_creation_tokens || 0;
                totalCacheRead += session.tokenUsage.cache_read_tokens || 0;
                totalCost += session.tokenUsage.cost || 0;
            }
        });

        if (sessionCount === 0) {
            hideTooltip();
            return;
        }

        const categoryLabel = dateCategoryLabels[category] || category;

        dom.sessionTooltip.innerHTML = `
            <div class="tooltip-label">${escapeHtml(categoryLabel)} - ${escapeHtml(projectName)}</div>
            <div class="tooltip-value">${sessionCount} session${sessionCount !== 1 ? 's' : ''}</div>
            <div class="tooltip-label">Token Usage</div>
            <div class="tooltip-value tooltip-usage">
                <span class="usage-item">In: ${formatTokenCount(totalInput)}</span>
                <span class="usage-item">Out (est): ${formatTokenCount(totalOutput)}</span>
                <span class="usage-item">Cache&uarr;: ${formatTokenCount(totalCacheCreate)}</span>
                <span class="usage-item">Cache&darr;: ${formatTokenCount(totalCacheRead)}</span>
            </div>
            <div class="tooltip-label">Estimated Cost</div>
            <div class="tooltip-value tooltip-cost">${formatCost(totalCost)}</div>
        `;
        dom.sessionTooltip.style.display = 'block';
        positionTooltip(e);
    }, 300);
}

export function positionTooltip(e) {
    const tooltip = dom.sessionTooltip;
    const padding = 10;

    let left = e.clientX + padding;
    let top = e.clientY + padding;

    // Adjust if tooltip goes off-screen
    const rect = tooltip.getBoundingClientRect();
    if (left + rect.width > window.innerWidth) {
        left = e.clientX - rect.width - padding;
    }
    if (top + rect.height > window.innerHeight) {
        top = e.clientY - rect.height - padding;
    }

    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
}

export function hideTooltip() {
    if (state.tooltipTimeout) {
        clearTimeout(state.tooltipTimeout);
        state.tooltipTimeout = null;
    }
    dom.sessionTooltip.style.display = 'none';
}

// Message tooltip (for token usage)
export function showMessageTooltip(msgEl, e) {
    if (state.messageTooltipTimeout) clearTimeout(state.messageTooltipTimeout);

    state.messageTooltipTimeout = setTimeout(function() {
        const input = parseInt(msgEl.dataset.usageInput) || 0;
        const output = parseInt(msgEl.dataset.usageOutput) || 0;
        const cacheCreate = parseInt(msgEl.dataset.usageCacheCreate) || 0;
        const cacheRead = parseInt(msgEl.dataset.usageCacheRead) || 0;
        const cost = parseFloat(msgEl.dataset.usageCost) || 0;
        const model = msgEl.dataset.model || '';

        let modelHtml = '';
        if (model) {
            modelHtml = `<div class="usage-row model-row"><span class="usage-label">Model:</span>${escapeHtml(formatModelName(model))}</div>`;
        }

        dom.messageTooltip.innerHTML = `
            ${modelHtml}
            <div class="usage-row">
                <span class="usage-item"><span class="usage-label">In:</span>${formatTokenCount(input)}</span>
                <span class="usage-item"><span class="usage-label">Out (est):</span>${formatTokenCount(output)}</span>
                <span class="usage-item"><span class="usage-label">Cache&uarr;:</span>${formatTokenCount(cacheCreate)}</span>
                <span class="usage-item"><span class="usage-label">Cache&darr;:</span>${formatTokenCount(cacheRead)}</span>
                <span class="usage-item"><span class="usage-label">Cost (est):</span>${formatCost(cost)}</span>
            </div>
        `;
        // Position first with visibility hidden to measure, then show
        dom.messageTooltip.style.visibility = 'hidden';
        dom.messageTooltip.style.display = 'block';
        positionMessageTooltip(e);
        dom.messageTooltip.style.visibility = 'visible';
    }, 200);
}

export function positionMessageTooltip(e) {
    const padding = 10;
    let left = e.clientX + padding;
    let top = e.clientY + padding;

    // Get dimensions after rendering
    const rect = dom.messageTooltip.getBoundingClientRect();
    if (rect.width > 0 && left + rect.width > window.innerWidth) {
        left = e.clientX - rect.width - padding;
    }
    if (rect.height > 0 && top + rect.height > window.innerHeight) {
        top = e.clientY - rect.height - padding;
    }

    dom.messageTooltip.style.left = left + 'px';
    dom.messageTooltip.style.top = top + 'px';
}

export function hideMessageTooltip() {
    if (state.messageTooltipTimeout) {
        clearTimeout(state.messageTooltipTimeout);
        state.messageTooltipTimeout = null;
    }
    dom.messageTooltip.style.display = 'none';
}
