// Permission modal module - handles permission grant/reject flow

import { dom, state } from './state.js';
import { showFlash } from './ui.js';
import { escapeHtml } from './utils.js';

/**
 * Show the permission modal for denied tool calls.
 * @param {Object} data - Permission denial data from SSE event
 * @param {string} data.session_id - The session ID
 * @param {Array} data.denials - Array of denial objects
 * @param {string} data.original_message - The message that triggered the denial
 */
export function showPermissionModal(data) {
    const { session_id, denials, original_message } = data;

    // Store context for grant/reject handlers
    state.pendingPermission = data;

    // Check if all denials are sandbox denials (no grantable permissions)
    const allSandbox = denials.every(d => d.is_sandbox_denial);
    const hasSandbox = denials.some(d => d.is_sandbox_denial);

    // Populate modal content
    dom.permissionDenialsList.innerHTML = '';

    denials.forEach((denial, index) => {
        const item = createDenialItem(denial, index);
        dom.permissionDenialsList.appendChild(item);
    });

    // Update button visibility based on denial types
    updateModalButtons(allSandbox);

    dom.permissionModal.showModal();
}

/**
 * Create a denial item with permission options.
 * @param {Object} denial - The denial object
 * @param {number} index - Index for radio button naming
 * @returns {HTMLElement} The denial item element
 */
function createDenialItem(denial, index) {
    const item = document.createElement('div');
    item.className = 'denial-item';

    const toolName = denial.tool_name;
    const toolInput = denial.tool_input;
    const isSandboxDenial = denial.is_sandbox_denial;
    const errorMessage = denial.error_message || '';

    const operationText = formatToolInput(toolInput);

    if (isSandboxDenial) {
        // Sandbox denial - can be fixed by allowing the directory
        item.classList.add('sandbox-denial');
        const dir = extractDirectory(toolInput);
        item.innerHTML = `
            <div class="denial-header">
                <span class="denial-tool">${escapeHtml(toolName)}</span>
                <span class="denial-operation">${escapeHtml(operationText)}</span>
            </div>
            <div class="sandbox-warning">
                <span class="warning-icon">&#9888;</span>
                <div class="warning-text">
                    <strong>Directory Access Blocked</strong>
                    <p>This directory is outside the allowed working directories for this session.</p>
                    <div class="sandbox-action">
                        <label class="permission-option sandbox-allow-option">
                            <input type="checkbox" name="sandbox-dir-${index}" value="${escapeHtml(dir)}" checked>
                            <span class="option-label">Allow access to:</span>
                            <code class="sandbox-dir-path">${escapeHtml(dir)}</code>
                        </label>
                    </div>
                </div>
            </div>
        `;
    } else {
        // Regular permission denial - can be granted
        const options = generatePermissionOptions(toolName, toolInput);

        item.innerHTML = `
            <div class="denial-header">
                <span class="denial-tool">${escapeHtml(toolName)}</span>
                <span class="denial-operation">${escapeHtml(operationText)}</span>
            </div>
            <div class="denial-options">
                ${options.map((opt, i) => `
                    <label class="permission-option">
                        <input type="radio" name="permission-${index}" value="${escapeHtml(opt.value)}"
                               ${i === 0 ? 'checked' : ''}>
                        <span class="option-label">${escapeHtml(opt.label)}</span>
                        <span class="option-example">${escapeHtml(opt.example)}</span>
                    </label>
                `).join('')}
            </div>
        `;
    }

    return item;
}

/**
 * Extract directory from tool input for sandbox denial hint.
 */
function extractDirectory(toolInput) {
    if (toolInput.file_path) {
        const lastSlash = toolInput.file_path.lastIndexOf('/');
        return lastSlash > 0 ? toolInput.file_path.substring(0, lastSlash) : toolInput.file_path;
    }
    if (toolInput.path) {
        const lastSlash = toolInput.path.lastIndexOf('/');
        return lastSlash > 0 ? toolInput.path.substring(0, lastSlash) : toolInput.path;
    }
    if (toolInput.command) {
        // Try to extract directory from command like "ls /home/user"
        const parts = toolInput.command.split(' ');
        for (const part of parts) {
            if (part.startsWith('/') || part.startsWith('~')) {
                return part;
            }
        }
    }
    return '/path/to/directory';
}

/**
 * Generate permission options for a tool denial.
 * @param {string} toolName - The tool name (e.g., "Bash", "Read")
 * @param {Object} toolInput - The tool's input parameters
 * @returns {Array} Array of option objects with label, value, and example
 */
function generatePermissionOptions(toolName, toolInput) {
    if (toolName === 'Bash') {
        const command = toolInput.command || '';
        const parts = command.split(' ');
        const firstWord = parts[0] || command;
        const firstTwoWords = parts.length >= 2 ? parts.slice(0, 2).join(' ') : command;

        const options = [
            {
                label: 'Allow this exact command',
                value: `Bash(${command})`,
                example: command
            }
        ];

        // Only add "with any arguments" if there are arguments
        if (parts.length >= 2) {
            options.push({
                label: 'Allow with any arguments',
                value: `Bash(${firstTwoWords}:*)`,
                example: `${firstTwoWords} ...`
            });
        }

        // Add broad option for the base command
        options.push({
            label: `Allow all ${firstWord} commands`,
            value: `Bash(${firstWord}:*)`,
            example: `${firstWord} ...`
        });

        return options;
    }

    if (toolName === 'Read' || toolName === 'Write' || toolName === 'Edit') {
        const filePath = toolInput.file_path || toolInput.path || '';
        const lastSlash = filePath.lastIndexOf('/');
        const directory = lastSlash > 0 ? filePath.substring(0, lastSlash) : '';

        const options = [
            {
                label: 'Allow this exact file',
                value: `${toolName}(${filePath})`,
                example: filePath
            }
        ];

        if (directory && directory !== '.') {
            options.push({
                label: 'Allow all files in directory',
                value: `${toolName}(${directory}/**)`,
                example: `${directory}/...`
            });
        }

        options.push({
            label: `Allow all ${toolName} operations`,
            value: toolName,
            example: 'Any file'
        });

        return options;
    }

    // Default: just allow the tool
    return [
        {
            label: `Allow ${toolName}`,
            value: toolName,
            example: 'All operations'
        }
    ];
}

/**
 * Format tool input for display.
 * @param {Object} input - The tool input
 * @returns {string} Formatted string
 */
function formatToolInput(input) {
    if (input.command) return input.command;
    if (input.file_path) return input.file_path;
    if (input.path) return input.path;
    return JSON.stringify(input);
}

/**
 * Update modal buttons based on whether denials are all sandbox denials.
 * @param {boolean} allSandbox - True if all denials are sandbox denials
 */
function updateModalButtons(allSandbox) {
    // Both permission denials and sandbox denials can now be granted
    // (permissions via settings.json, sandbox via --add-dir)
    dom.permissionGrantBtn.style.display = '';
    dom.permissionGrantBtn.textContent = allSandbox ? 'Allow' : 'Grant';
    dom.permissionRejectBtn.textContent = 'Cancel';
    dom.permissionRejectBtn.classList.add('modal-btn-reject');
    dom.permissionRejectBtn.classList.remove('modal-btn-dismiss');
}

/**
 * Show the permission modal for denied tool calls during new session creation.
 * @param {Object} data - Permission denial data from /sessions/new response
 * @param {string} data.cwd - Working directory for the new session
 * @param {Array} data.denials - Array of denial objects
 * @param {string} data.original_message - The message that triggered the denial
 * @param {string} data.backend - Backend name
 * @param {number|null} data.model_index - Model index if applicable
 */
export function showPermissionModalForNewSession(data) {
    const { denials } = data;

    // Store context for grant/reject handlers - mark as new session
    state.pendingPermission = { ...data, isNewSession: true };

    // Check if all denials are sandbox denials (no grantable permissions)
    const allSandbox = denials.every(d => d.is_sandbox_denial);

    // Populate modal content
    dom.permissionDenialsList.innerHTML = '';

    denials.forEach((denial, index) => {
        const item = createDenialItem(denial, index);
        dom.permissionDenialsList.appendChild(item);
    });

    // Update button visibility based on denial types
    updateModalButtons(allSandbox);

    dom.permissionModal.showModal();
}

/**
 * Handle grant button click for new sessions.
 */
async function handleGrantNewSession() {
    const { cwd, original_message, backend, model_index } = state.pendingPermission;

    // Collect selected permissions (for regular permission denials)
    const permissions = [];
    dom.permissionDenialsList.querySelectorAll('.denial-item:not(.sandbox-denial)').forEach((item, index) => {
        const selected = item.querySelector(`input[name="permission-${index}"]:checked`);
        if (selected) {
            permissions.push(selected.value);
        }
    });

    // Collect selected directories (for sandbox denials)
    const directories = [];
    dom.permissionDenialsList.querySelectorAll('.sandbox-denial').forEach((item, index) => {
        const checkbox = item.querySelector(`input[name="sandbox-dir-${index}"]:checked`);
        if (checkbox) {
            directories.push(checkbox.value);
        }
    });

    dom.permissionModal.close();
    state.pendingPermission = null;

    try {
        // First, allow any sandbox directories
        for (const dir of directories) {
            await fetch('/allow-directory', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ directory: dir })
            });
        }

        // Then grant permissions and/or retry
        const response = await fetch('/sessions/grant-permission-new', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                permissions: permissions,
                original_message: original_message,
                cwd: cwd,
                backend: backend,
                model_index: model_index
            })
        });

        const data = await response.json();

        if (response.ok) {
            if (data.status === 'permission_denied') {
                // Still getting permission denials - show modal again
                showPermissionModalForNewSession(data);
            } else {
                const msg = directories.length > 0 ? 'Directory allowed' : 'Permission granted';
                showFlash(msg + ', starting session...', 'success');

                // Mark the pending session as starting so session_added event can merge it
                // Find the pending session by cwd
                for (const [sessionId, session] of state.sessions) {
                    if (session.pending && session.cwd === cwd) {
                        session.starting = true;
                        session.startedAt = Date.now();
                        break;
                    }
                }
            }
        } else {
            showFlash('Failed to grant permission: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch (e) {
        showFlash('Failed to grant permission: ' + e.message, 'error');
    }
}

/**
 * Handle reject button click for new sessions.
 */
function handleRejectNewSession() {
    dom.permissionModal.close();
    state.pendingPermission = null;
    showFlash('Permission rejected - session not started', 'warning');
}

/**
 * Handle grant button click.
 */
async function handleGrant() {
    if (!state.pendingPermission) return;

    // Check if this is a new session permission denial
    if (state.pendingPermission.isNewSession) {
        return handleGrantNewSession();
    }

    const { session_id, original_message, denials } = state.pendingPermission;

    // Collect selected permissions (for regular permission denials)
    const permissions = [];
    dom.permissionDenialsList.querySelectorAll('.denial-item:not(.sandbox-denial)').forEach((item, index) => {
        const selected = item.querySelector(`input[name="permission-${index}"]:checked`);
        if (selected) {
            permissions.push(selected.value);
        }
    });

    // Collect selected directories (for sandbox denials)
    const directories = [];
    dom.permissionDenialsList.querySelectorAll('.sandbox-denial').forEach((item, index) => {
        const checkbox = item.querySelector(`input[name="sandbox-dir-${index}"]:checked`);
        if (checkbox) {
            directories.push(checkbox.value);
        }
    });

    dom.permissionModal.close();
    state.pendingPermission = null;

    try {
        // First, allow any sandbox directories
        for (const dir of directories) {
            await fetch('/allow-directory', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ directory: dir })
            });
        }

        // Then grant permissions and retry
        if (permissions.length > 0) {
            const response = await fetch(`/sessions/${encodeURIComponent(session_id)}/grant-permission`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    permissions: permissions,
                    original_message: original_message
                })
            });

            if (response.ok) {
                showFlash('Permission granted, retrying...', 'success');
            } else {
                const data = await response.json();
                showFlash('Failed to grant permission: ' + (data.detail || 'Unknown error'), 'error');
            }
        } else if (directories.length > 0) {
            // Only sandbox denials - allow directory and retry via that endpoint
            const response = await fetch('/allow-directory', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    directory: directories[0],  // Already allowed above, this is for retry
                    session_id: session_id,
                    original_message: original_message
                })
            });

            if (response.ok) {
                showFlash('Directory allowed, retrying...', 'success');
            } else {
                const data = await response.json();
                showFlash('Failed to allow directory: ' + (data.detail || 'Unknown error'), 'error');
            }
        }
    } catch (e) {
        showFlash('Failed to grant permission: ' + e.message, 'error');
    }
}

/**
 * Handle reject button click.
 */
async function handleReject() {
    if (!state.pendingPermission) {
        dom.permissionModal.close();
        return;
    }

    // Check if this is a new session permission denial
    if (state.pendingPermission.isNewSession) {
        return handleRejectNewSession();
    }

    const { session_id, denials } = state.pendingPermission;

    dom.permissionModal.close();
    state.pendingPermission = null;

    // Format rejection message
    const denialDescriptions = denials.map(d =>
        `${d.tool_name}: ${formatToolInput(d.tool_input)}`
    ).join(', ');

    const rejectionMessage = `User rejected permission for: ${denialDescriptions}`;

    // Send rejection as a message
    try {
        await fetch(`/sessions/${encodeURIComponent(session_id)}/send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: rejectionMessage })
        });
        showFlash('Permission rejected', 'warning');
    } catch (e) {
        console.error('Failed to send rejection:', e);
        showFlash('Failed to send rejection: ' + e.message, 'error');
    }
}

/**
 * Initialize permission modal event listeners.
 */
export function initPermissions() {
    // Check if permission modal elements exist
    if (!dom.permissionModal || !dom.permissionGrantBtn || !dom.permissionRejectBtn) {
        console.log('Permission modal elements not found, skipping initialization');
        return;
    }

    dom.permissionGrantBtn.addEventListener('click', handleGrant);
    dom.permissionRejectBtn.addEventListener('click', handleReject);

    // Close on backdrop click (treat as reject)
    dom.permissionModal.addEventListener('click', function(e) {
        if (e.target === dom.permissionModal) {
            handleReject();
        }
    });

    // Don't close on Escape - require explicit action
    dom.permissionModal.addEventListener('cancel', function(e) {
        e.preventDefault();
        // Could show a tooltip or flash message here
    });
}
