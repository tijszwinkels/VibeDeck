
import { dom, state } from './state.js';
import { isMobile, copyToClipboard } from './utils.js';
import { openRightPane, syncTreeToFile, loadFileTree } from './filetree.js';
import { showFlash } from './ui.js';
import { startFileWatch, stopFileWatch } from './filewatch.js';

// Image file extensions that can be displayed in the preview pane
const IMAGE_EXTENSIONS = new Set([
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp'
]);

// Audio file extensions that can be played in the preview pane
const AUDIO_EXTENSIONS = new Set([
    'mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac', 'wma', 'webm'
]);

/**
 * Check if a filename is an image file based on extension.
 */
function isImageFile(filename) {
    const ext = filename.includes('.') ? filename.split('.').pop().toLowerCase() : '';
    return IMAGE_EXTENSIONS.has(ext);
}

/**
 * Check if a filename is an audio file based on extension.
 */
function isAudioFile(filename) {
    const ext = filename.includes('.') ? filename.split('.').pop().toLowerCase() : '';
    return AUDIO_EXTENSIONS.has(ext);
}

// Initialize preview pane width
export function initPreviewPane() {
    document.documentElement.style.setProperty('--preview-pane-width', state.previewPaneWidth + 'px');

    // Close button
    dom.previewCloseBtn.addEventListener('click', closePreviewPane);

    // Copy path button
    if (dom.previewCopyBtn) {
        dom.previewCopyBtn.addEventListener('click', function() {
            if (state.previewFilePath) {
                copyToClipboard(state.previewFilePath, null);
                const originalText = dom.previewCopyBtn.textContent;
                dom.previewCopyBtn.textContent = 'Copied!';
                setTimeout(() => dom.previewCopyBtn.textContent = originalText, 1500);
            }
        });
    }

    // View toggle for markdown files
    if (dom.previewViewCheckbox) {
        dom.previewViewCheckbox.addEventListener('change', function() {
            updateViewToggleLabel();
            if (state.previewFileData) {
                renderPreviewContent(state.previewFileData, dom.previewViewCheckbox.checked);
            }
        });
    }

    // Follow checkbox for auto-scroll on new content
    // Toggling also restarts the SSE connection with the new follow mode
    if (dom.previewFollowCheckbox) {
        dom.previewFollowCheckbox.checked = state.previewFollow;
        dom.previewFollowCheckbox.addEventListener('change', function() {
            state.previewFollow = dom.previewFollowCheckbox.checked;
            localStorage.setItem('previewFollow', state.previewFollow);

            // Restart file watch with new follow mode if a file is open
            if (state.previewFilePath) {
                openPreviewPane(state.previewFilePath);
            }
        });
    }

    // Auto-uncheck Follow when user scrolls up in preview content
    if (dom.previewContent) {
        dom.previewContent.addEventListener('scroll', function() {
            if (!state.previewFollow) return;

            // Check if user scrolled up (not at bottom)
            const el = dom.previewContent;
            const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;

            if (!isAtBottom && dom.previewFollowCheckbox) {
                state.previewFollow = false;
                dom.previewFollowCheckbox.checked = false;
                localStorage.setItem('previewFollow', 'false');
            }
        });
    }

    // Escape key to close preview pane
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && state.previewPaneOpen) {
            closePreviewPane();
        }
    });

    // Click on file paths to open preview, or URLs to copy them (except copy button)
    // This handles both explicit file-tool-fullpath elements and any text that looks like a file path or URL
    document.addEventListener('click', async function(e) {
        // Skip if clicking the copy button
        if (e.target.closest('.copy-btn')) return;

        // Skip if clicking links (let them navigate normally)
        if (e.target.closest('a')) return;

        // Skip if clicking expand button
        if (e.target.closest('.expand-btn')) return;

        // Skip if clicking within diff view elements (let diff.js handle those)
        if (e.target.closest('.diff-file-list') || e.target.closest('.diff-header') || e.target.closest('.diff-view')) return;

        // Skip if user has selected text (they're trying to copy)
        const selection = window.getSelection();
        if (selection && selection.toString().length > 0) return;

        // Check if clicking on an edit-tool block - open file in preview
        const editTool = e.target.closest('.edit-tool');
        if (editTool) {
            e.preventDefault();
            e.stopPropagation();
            const fullpath = editTool.querySelector('.file-tool-fullpath[data-copy-path]');
            if (fullpath) {
                const path = fullpath.dataset.copyPath;
                openPreviewPane(path);
            }
            return;
        }

        // First, check for explicit file-tool-fullpath elements (highest priority)
        const fullpath = e.target.closest('.file-tool-fullpath[data-copy-path]');
        if (fullpath) {
            e.preventDefault();
            e.stopPropagation();
            const path = fullpath.dataset.copyPath;
            openPreviewPane(path);
            return;
        }

        // Try to detect URL from clicked text
        const url = extractUrlFromClick(e);
        if (url) {
            copyToClipboard(url, null);
            showFlash('URL copied to clipboard', 'success', 2000);
            return;
        }

        // Otherwise, try to detect file path from clicked text
        const filePath = extractFilePathFromClick(e);
        if (filePath) {
            // Check path type and handle accordingly (silently fail if not found)
            const pathType = await getPathType(filePath);
            if (pathType === 'file') {
                openPreviewPane(filePath);
            } else if (pathType === 'directory' && state.activeSessionId) {
                // Navigate file tree to this directory
                openRightPane();
                loadFileTree(state.activeSessionId, filePath);
            }
        }
    });

    // Preview pane resize handle
    dom.previewResizeHandle.addEventListener('mousedown', function(e) {
        if (isMobile()) return;
        state.isPreviewResizing = true;
        state.previewStartX = e.clientX;
        state.previewStartWidth = state.previewPaneWidth;
        dom.previewResizeHandle.classList.add('dragging');
        document.body.style.cursor = 'ew-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });

    document.addEventListener('mousemove', function(e) {
        if (!state.isPreviewResizing) return;

        const delta = state.previewStartX - e.clientX;  // Note: reversed because resizing from left edge
        let newWidth = state.previewStartWidth + delta;

        // Clamp width: min 500px (split view), max 90% of viewport
        const maxWidth = window.innerWidth * 0.9;
        newWidth = Math.max(500, Math.min(maxWidth, newWidth));

        state.previewPaneWidth = newWidth;
        document.documentElement.style.setProperty('--preview-pane-width', newWidth + 'px');
    });

    document.addEventListener('mouseup', function() {
        if (state.isPreviewResizing) {
            state.isPreviewResizing = false;
            dom.previewResizeHandle.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            localStorage.setItem('previewPaneWidth', state.previewPaneWidth);
        }
    });
}

export async function openPreviewPane(filePath) {
    // Stop any existing file watch
    stopFileWatch();

    // Clear URL mode state (we're now viewing a file)
    state.previewUrlMode = false;
    state.previewUrl = null;

    // Check if this is a new file or just a restart (e.g., from toggling Follow)
    const isNewFile = state.previewFilePath !== filePath;

    state.previewFilePath = filePath;
    state.previewFileData = null;
    const filename = filePath.split('/').pop();

    // Auto-enable Follow for log-like files (.log, .jsonl) - only on first open
    if (isNewFile) {
        const ext = filename.includes('.') ? filename.split('.').pop().toLowerCase() : '';
        if (ext === 'log' || ext === 'jsonl') {
            state.previewFollow = true;
            if (dom.previewFollowCheckbox) {
                dom.previewFollowCheckbox.checked = true;
            }
        }
    }

    // Track this preview for the current session
    if (state.activeSessionId) {
        state.sessionPreviewPaths.set(state.activeSessionId, filePath);
    }

    if (dom.previewFilename) dom.previewFilename.textContent = filename;
    if (dom.previewPath) dom.previewPath.textContent = filePath;
    if (dom.previewContent) dom.previewContent.innerHTML = '';
    if (dom.previewViewToggle) dom.previewViewToggle.style.display = 'none';  // Hide toggle until we know if it's markdown
    if (dom.previewFollowToggle) dom.previewFollowToggle.style.display = '';  // Show Follow toggle
    if (dom.previewCopyBtn) dom.previewCopyBtn.style.display = 'block';

    showPreviewStatus('loading', 'Loading...');

    // Open the pane (ensures parent classes are set)
    openRightPane();

    // Sync tree to this file
    syncTreeToFile(filePath);

    // Handle image files differently - no file watching, just display
    if (isImageFile(filename)) {
        if (dom.previewFollowToggle) dom.previewFollowToggle.style.display = 'none';  // Hide Follow toggle for images
        renderImagePreview(filePath);
        hidePreviewStatus();
        return;
    }

    // Handle audio files differently - no file watching, just play
    if (isAudioFile(filename)) {
        if (dom.previewFollowToggle) dom.previewFollowToggle.style.display = 'none';  // Hide Follow toggle for audio
        renderAudioPreview(filePath);
        hidePreviewStatus();
        return;
    }

    // Handle HTML files in sandboxed iframe for preview
    if (isHtmlFile(filename)) {
        if (dom.previewFollowToggle) dom.previewFollowToggle.style.display = 'none';  // Hide Follow toggle for HTML
        if (dom.previewViewToggle) dom.previewViewToggle.style.display = 'none';  // Hide view toggle for HTML
        renderHtmlPreview(filePath);
        hidePreviewStatus();
        return;
    }

    // For follow=false, fetch initial content via /api/file (gets markdown rendering)
    // For follow=true, SSE initial event provides content (no markdown needed for logs)
    if (!state.previewFollow) {
        try {
            const response = await fetch(`/api/file?path=${encodeURIComponent(filePath)}`);
            if (!response.ok) {
                showPreviewStatus('error', `Failed to load: ${response.statusText}`);
                return;
            }
            const data = await response.json();

            state.previewFileData = data;

            // Show view toggle if this is a markdown file with rendered HTML
            if (dom.previewViewToggle) {
                dom.previewViewToggle.style.display = data.rendered_html ? '' : 'none';
            }

            renderPreviewContent(data, dom.previewViewCheckbox ? dom.previewViewCheckbox.checked : true);

            if (data.truncated) {
                showPreviewStatus('warning', 'File truncated (showing first 1MB)');
            } else {
                hidePreviewStatus();
            }
        } catch (err) {
            showPreviewStatus('error', `Failed to load: ${err.message}`);
            return;
        }
    }

    // Start watching the file via SSE
    // follow=true: SSE provides initial content + append/replace events
    // follow=false: SSE only notifies of changes, we refetch via /api/file
    startFileWatch(filePath, {
        onInitial: function(data) {
            // Only used when follow=true (log tailing mode)
            if (!state.previewFollow) return;  // Already loaded via /api/file

            // Store data for view toggle (adapt SSE data to match API response format)
            state.previewFileData = {
                content: data.content,
                truncated: data.truncated,
                size: data.size
            };

            // Render content with syntax highlighting
            renderPreviewContent(state.previewFileData, dom.previewViewCheckbox ? dom.previewViewCheckbox.checked : true);

            if (data.truncated) {
                showPreviewStatus('warning', 'File truncated (showing first 1MB)');
            } else {
                hidePreviewStatus();
            }

            // Scroll to bottom if Follow is enabled
            if (state.previewFollow && dom.previewContent) {
                dom.previewContent.scrollTop = dom.previewContent.scrollHeight;
            }
        },
        onAppend: function(data) {
            // Append new content to existing preview
            if (!dom.previewContent) return;

            const pre = dom.previewContent.querySelector('pre');
            const code = pre ? pre.querySelector('code') : null;

            if (code) {
                // Append to existing code block
                code.textContent += data.content;

                // Re-apply highlighting
                if (window.hljs) {
                    hljs.highlightElement(code);
                }
            }

            // Update stored data
            if (state.previewFileData) {
                state.previewFileData.content += data.content;
            }

            // Scroll to bottom if Follow is enabled
            if (state.previewFollow && dom.previewContent) {
                dom.previewContent.scrollTop = dom.previewContent.scrollHeight;
            }
        },
        onReplace: function(data) {
            // Full content replacement
            state.previewFileData = {
                content: data.content,
                truncated: data.truncated,
                size: data.size
            };

            // Save scroll position if not following
            const scrollPos = dom.previewContent ? dom.previewContent.scrollTop : 0;

            // Re-render content
            renderPreviewContent(state.previewFileData, dom.previewViewCheckbox ? dom.previewViewCheckbox.checked : true);

            if (data.truncated) {
                showPreviewStatus('warning', 'File truncated (showing first 1MB)');
            } else {
                hidePreviewStatus();
            }

            // Restore scroll position or scroll to bottom
            if (dom.previewContent) {
                if (state.previewFollow) {
                    dom.previewContent.scrollTop = dom.previewContent.scrollHeight;
                } else {
                    dom.previewContent.scrollTop = scrollPos;
                }
            }
        },
        onChanged: async function() {
            // File changed notification (follow=false mode)
            // Refetch via /api/file to get full content with markdown rendering
            const scrollPos = dom.previewContent ? dom.previewContent.scrollTop : 0;

            try {
                const response = await fetch(`/api/file?path=${encodeURIComponent(filePath)}`);
                if (!response.ok) {
                    showPreviewStatus('error', `Failed to reload: ${response.statusText}`);
                    return;
                }
                const data = await response.json();

                state.previewFileData = data;

                // Show view toggle if this is a markdown file
                if (dom.previewViewToggle) {
                    dom.previewViewToggle.style.display = data.rendered_html ? '' : 'none';
                }

                renderPreviewContent(data, dom.previewViewCheckbox ? dom.previewViewCheckbox.checked : true);

                if (data.truncated) {
                    showPreviewStatus('warning', 'File truncated (showing first 1MB)');
                } else {
                    hidePreviewStatus();
                }

                // Restore scroll position
                if (dom.previewContent) {
                    dom.previewContent.scrollTop = scrollPos;
                }
            } catch (err) {
                showPreviewStatus('error', `Failed to reload: ${err.message}`);
            }
        },
        onError: function(message) {
            showPreviewStatus('error', message);
            stopFileWatch();
        }
    }, { follow: state.previewFollow });
}

export function closePreviewPane(clearSessionAssociation = true) {
    // Stop file watching
    stopFileWatch();

    dom.previewPane.classList.remove('open');
    dom.mainContent.classList.remove('preview-open');
    dom.inputBar.classList.remove('preview-open');
    dom.floatingControls.classList.remove('preview-open');
    state.previewPaneOpen = false;
    state.previewFilePath = null;
    state.previewFileData = null;

    // Clear session association if requested (not when switching sessions)
    if (clearSessionAssociation && state.activeSessionId) {
        state.sessionPreviewPaths.delete(state.activeSessionId);
    }
}

function renderPreviewContent(data, showRendered = true) {
    if (!dom.previewContent) return;
    
    dom.previewContent.innerHTML = '';

    // If we have pre-rendered HTML (markdown) and showRendered is true, display that
    if (data.rendered_html && showRendered) {
        const wrapper = document.createElement('div');
        wrapper.className = 'markdown-preview';
        wrapper.innerHTML = data.rendered_html;
        dom.previewContent.appendChild(wrapper);

        // Apply highlight.js to any code blocks in the markdown
        if (window.hljs) {
            wrapper.querySelectorAll('pre code').forEach((block) => {
                hljs.highlightElement(block);
            });
        }
        return;
    }

    // Otherwise render as code with syntax highlighting
    const pre = document.createElement('pre');
    const code = document.createElement('code');

    // Set language class if detected
    if (data.language) {
        code.className = 'language-' + data.language;
    }

    code.textContent = data.content;
    pre.appendChild(code);
    dom.previewContent.appendChild(pre);

    // Apply highlight.js if available
    if (window.hljs) {
        hljs.highlightElement(code);
    }
}

/**
 * Render an image file in the preview pane.
 * Uses the /api/file/raw endpoint to serve the image.
 */
function renderImagePreview(filePath) {
    if (!dom.previewContent) return;

    dom.previewContent.innerHTML = '';

    const wrapper = document.createElement('div');
    wrapper.className = 'image-preview';

    const img = document.createElement('img');
    // Add timestamp to bust browser cache on reload
    img.src = `/api/file/raw?path=${encodeURIComponent(filePath)}&t=${Date.now()}`;
    img.alt = filePath.split('/').pop();

    // Handle load error
    img.onerror = function() {
        showPreviewStatus('error', 'Failed to load image');
    };

    wrapper.appendChild(img);
    dom.previewContent.appendChild(wrapper);
}

/**
 * Render an audio file in the preview pane.
 * Uses the /api/file/raw endpoint to serve the audio.
 */
function renderAudioPreview(filePath) {
    if (!dom.previewContent) return;

    dom.previewContent.innerHTML = '';

    const wrapper = document.createElement('div');
    wrapper.className = 'audio-preview';

    const audio = document.createElement('audio');
    audio.controls = true;
    // Add timestamp to bust browser cache on reload
    audio.src = `/api/file/raw?path=${encodeURIComponent(filePath)}&t=${Date.now()}`;

    // Handle load error
    audio.onerror = function() {
        showPreviewStatus('error', 'Failed to load audio');
    };

    // Show filename
    const filename = document.createElement('div');
    filename.className = 'audio-filename';
    filename.textContent = filePath.split('/').pop();

    wrapper.appendChild(filename);
    wrapper.appendChild(audio);
    dom.previewContent.appendChild(wrapper);
}

function showPreviewStatus(type, message) {
    if (!dom.previewStatus) return;
    dom.previewStatus.className = 'preview-status visible ' + type;
    dom.previewStatus.textContent = message;
}

function hidePreviewStatus() {
    if (!dom.previewStatus) return;
    dom.previewStatus.className = 'preview-status';
}

function updateViewToggleLabel() {
    if (!dom.previewViewToggle) return;
    const label = dom.previewViewToggle.querySelector('.toggle-label');
    if (label) {
        label.textContent = dom.previewViewCheckbox.checked ? 'Rendered' : 'Source';
    }
}

/**
 * Extract a URL from clicked text.
 * Tries to find http:// or https:// URL in or around the clicked element.
 * Returns null if no valid URL is found.
 */
function extractUrlFromClick(event) {
    const target = event.target;

    // Get the text content of the clicked element or its innermost text node
    let text = '';

    if (target.nodeType === Node.ELEMENT_NODE) {
        // Try to get the specific text at the click position using Range
        const clickedText = getUrlTextAtPoint(event.clientX, event.clientY);
        if (clickedText) {
            text = clickedText;
        } else {
            // Fall back to element's text content
            text = target.textContent || '';
        }
    }

    if (!text) return null;

    // Try to extract a URL from the text
    return findUrlInText(text);
}

/**
 * Get the URL text content at a specific point.
 * Similar to getTextAtPoint but includes URL-valid characters like : and ?
 */
function getUrlTextAtPoint(x, y) {
    let textNode;
    let offset;

    // Try caretPositionFromPoint (Firefox) or caretRangeFromPoint (Chrome/Safari)
    if (document.caretPositionFromPoint) {
        const pos = document.caretPositionFromPoint(x, y);
        if (pos && pos.offsetNode) {
            textNode = pos.offsetNode;
            offset = pos.offset;
        }
    } else if (document.caretRangeFromPoint) {
        const range = document.caretRangeFromPoint(x, y);
        if (range) {
            textNode = range.startContainer;
            offset = range.startOffset;
        }
    }

    if (!textNode || textNode.nodeType !== Node.TEXT_NODE) {
        return null;
    }

    const fullText = textNode.textContent || '';

    // URL characters - include everything valid in URLs
    // RFC 3986 unreserved: A-Z a-z 0-9 - . _ ~
    // RFC 3986 reserved: : / ? # [ ] @ ! $ & ' ( ) * + , ; =
    // Also include % for percent-encoding
    const urlChars = /[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]/;

    let start = offset;
    let end = offset;

    // Expand left
    while (start > 0 && urlChars.test(fullText[start - 1])) {
        start--;
    }

    // Expand right
    while (end < fullText.length && urlChars.test(fullText[end])) {
        end++;
    }

    const word = fullText.substring(start, end);
    return word.length > 0 ? word : null;
}

/**
 * Find a URL pattern in text.
 * Returns the URL if found, null otherwise.
 */
function findUrlInText(text) {
    // Look for http:// or https:// URLs
    const urlPattern = /https?:\/\/[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+/i;
    const match = text.match(urlPattern);

    if (match) {
        // Clean up trailing punctuation that's unlikely to be part of the URL
        let url = match[0];
        // Remove trailing punctuation like ), ], ., ,, ;, :, ! if they aren't balanced
        url = url.replace(/[)\].,;:!]+$/, '');
        // But restore ) if there's a matching ( in the URL (common in Wikipedia URLs)
        const openParens = (url.match(/\(/g) || []).length;
        const closeParens = (url.match(/\)/g) || []).length;
        if (openParens > closeParens && match[0].charAt(url.length) === ')') {
            url += ')';
        }
        return url;
    }

    return null;
}

/**
 * Extract a file path from clicked text.
 * Tries to find a file path pattern in or around the clicked element.
 * Returns null if no valid file path is found.
 */
function extractFilePathFromClick(event) {
    const target = event.target;

    // Get the text content of the clicked element or its innermost text node
    let text = '';

    // If we clicked directly on a text node's parent, get that text
    if (target.nodeType === Node.ELEMENT_NODE) {
        // Try to get the specific text at the click position using Range
        const clickedText = getTextAtPoint(event.clientX, event.clientY);
        if (clickedText) {
            text = clickedText;
        } else {
            // Fall back to element's text content
            text = target.textContent || '';
        }
    }

    if (!text) return null;

    // Try to extract a file path from the text
    return findFilePathInText(text);
}

/**
 * Get the text content at a specific point using document.caretPositionFromPoint
 * or document.caretRangeFromPoint for browser compatibility.
 */
function getTextAtPoint(x, y) {
    let range;
    let textNode;
    let offset;

    // Try caretPositionFromPoint (Firefox) or caretRangeFromPoint (Chrome/Safari)
    if (document.caretPositionFromPoint) {
        const pos = document.caretPositionFromPoint(x, y);
        if (pos && pos.offsetNode) {
            textNode = pos.offsetNode;
            offset = pos.offset;
        }
    } else if (document.caretRangeFromPoint) {
        range = document.caretRangeFromPoint(x, y);
        if (range) {
            textNode = range.startContainer;
            offset = range.startOffset;
        }
    }

    if (!textNode || textNode.nodeType !== Node.TEXT_NODE) {
        return null;
    }

    const fullText = textNode.textContent || '';

    // Find word boundaries around the click position, expanding to capture full file path
    // Include characters common in file paths: letters, numbers, /, ., -, _, ~
    let start = offset;
    let end = offset;

    // Path characters (don't include : since we want to stop before line numbers)
    const pathChars = /[a-zA-Z0-9\/._\-~]/;

    // Expand left
    while (start > 0 && pathChars.test(fullText[start - 1])) {
        start--;
    }

    // Expand right
    while (end < fullText.length && pathChars.test(fullText[end])) {
        end++;
    }

    const word = fullText.substring(start, end);
    return word.length > 0 ? word : null;
}

/**
 * Find a file path pattern in text.
 * Returns the path if found, null otherwise.
 */
function findFilePathInText(text) {
    // Skip if text starts with http:// or https:// - it's a URL
    if (/^https?:\/\//i.test(text)) {
        return null;
    }

    // Common file path patterns:
    // 1. Absolute path: /home/user/file.txt or /var/log/file.log
    // 2. Home-relative: ~/file.txt or ~/.config/file
    // 3. Relative with directories: src/components/file.tsx, ./file.txt, ../file.txt

    // Pattern explanation:
    // - Starts with /, ~/, ./, or ../
    // - Or looks like a relative path with at least one directory separator
    // - Contains valid path characters
    // - Has some minimum length to avoid false positives

    // First, try to match patterns that start with explicit path indicators
    const explicitPathPattern = /^(\/|~\/|\.\/)[\w.\-\/]+/;
    const explicitMatch = text.match(explicitPathPattern);
    if (explicitMatch) {
        return cleanFilePath(explicitMatch[0]);
    }

    // Try to match relative paths with directory structure (must have at least one /)
    // This catches things like: src/file.ts, components/Button.tsx
    const relativePathPattern = /^[\w.\-]+\/[\w.\-\/]+/;
    const relativeMatch = text.match(relativePathPattern);
    if (relativeMatch) {
        // Only return if it looks like a real file path (has a file extension or ends reasonably)
        const path = relativeMatch[0];
        if (looksLikeFilePath(path)) {
            return cleanFilePath(path);
        }
    }

    return null;
}

/**
 * Clean up a file path - remove trailing punctuation, etc.
 */
function cleanFilePath(path) {
    // Remove trailing punctuation that might have been captured
    return path.replace(/[,;:!?'")\]}>]+$/, '');
}

/**
 * Check if a string looks like a real file path.
 * Helps avoid false positives on random text.
 */
function looksLikeFilePath(text) {
    // Has a file extension
    if (/\.\w{1,10}$/.test(text)) {
        return true;
    }

    // Is a known config/dotfile pattern
    if (/\/\.\w+/.test(text)) {
        return true;
    }

    // Ends with a directory-like name (all lowercase or common patterns)
    if (/\/[a-z_\-]+$/.test(text)) {
        return true;
    }

    return false;
}

/**
 * Get the type of a path on the server.
 * Returns "file", "directory", or null if not found.
 * Never throws - returns null on any error.
 */
async function getPathType(path) {
    try {
        const response = await fetch(`/api/path/type?path=${encodeURIComponent(path)}`);
        if (!response.ok) return null;
        const data = await response.json();
        return data.type;  // "file" or "directory"
    } catch {
        return null;
    }
}

/**
 * Programmatically set the follow mode.
 * Used by GUI commands to enable follow mode before opening a file.
 * @param {boolean} enabled - Whether to enable follow mode
 */
export function setFollowMode(enabled) {
    state.previewFollow = enabled;
    if (dom.previewFollowCheckbox) {
        dom.previewFollowCheckbox.checked = enabled;
    }
    localStorage.setItem('previewFollow', enabled ? 'true' : 'false');
}

/**
 * Scroll the preview pane to a specific line number.
 * Highlights the line briefly to draw attention to it.
 * @param {number} lineNumber - The line number to scroll to (1-indexed)
 */
export function scrollToLine(lineNumber) {
    if (!dom.previewContent) return;

    const pre = dom.previewContent.querySelector('pre');
    const code = pre ? pre.querySelector('code') : null;
    if (!code) return;

    // Get line height from computed style
    const computedStyle = getComputedStyle(code);
    const lineHeight = parseFloat(computedStyle.lineHeight) || 20;

    // Calculate scroll position (line numbers are 1-indexed)
    const scrollTop = (lineNumber - 1) * lineHeight;

    // Scroll to the line with some padding at the top
    dom.previewContent.scrollTop = Math.max(0, scrollTop - 50);

    // Add highlight effect to the line
    // We'll use a CSS custom property to indicate the highlighted line
    code.dataset.highlightLine = lineNumber;

    // Remove highlight after 2 seconds
    setTimeout(() => {
        delete code.dataset.highlightLine;
    }, 2000);
}

/**
 * Open a URL in the preview pane using a sandboxed iframe.
 * @param {string} url - The URL to display (must be http:// or https://)
 */
export function openUrlPane(url) {
    // Stop any file watching
    stopFileWatch();

    // Clear file-related state
    state.previewFilePath = null;
    state.previewFileData = null;
    state.previewUrlMode = true;
    state.previewUrl = url;

    // Extract hostname for display
    let hostname = url;
    try {
        hostname = new URL(url).hostname;
    } catch {
        // Use full URL if parsing fails
    }

    // Update header
    if (dom.previewFilename) dom.previewFilename.textContent = hostname;
    if (dom.previewPath) dom.previewPath.textContent = url;

    // Hide file-specific controls
    if (dom.previewViewToggle) dom.previewViewToggle.style.display = 'none';
    if (dom.previewFollowToggle) dom.previewFollowToggle.style.display = 'none';
    if (dom.previewCopyBtn) dom.previewCopyBtn.style.display = 'none';

    // Clear content and render iframe
    dom.previewContent.innerHTML = '';

    const iframe = document.createElement('iframe');
    iframe.className = 'url-preview-iframe';
    iframe.src = url;
    // Restricted sandbox - no same-origin to prevent access to VibeDeck's storage
    iframe.sandbox = 'allow-scripts';

    // Handle load errors (note: iframe onerror doesn't fire for HTTP errors)
    iframe.onerror = function() {
        showPreviewStatus('error', 'Failed to load URL');
    };

    dom.previewContent.appendChild(iframe);

    // Open pane
    openRightPane();
    hidePreviewStatus();
}

/**
 * Check if a filename is an HTML file based on extension.
 * @param {string} filename - The filename to check
 * @returns {boolean}
 */
function isHtmlFile(filename) {
    const ext = filename.includes('.') ? filename.split('.').pop().toLowerCase() : '';
    return ext === 'html' || ext === 'htm';
}

/**
 * Render an HTML file in a sandboxed iframe.
 * @param {string} filePath - The path to the HTML file
 */
function renderHtmlPreview(filePath) {
    if (!dom.previewContent) return;

    dom.previewContent.innerHTML = '';

    const iframe = document.createElement('iframe');
    iframe.className = 'html-preview-iframe';
    // Use /api/file/raw to serve the HTML file
    iframe.src = `/api/file/raw?path=${encodeURIComponent(filePath)}`;
    // More restricted sandbox for local files - no same-origin to prevent access to VibeDeck's storage
    iframe.sandbox = 'allow-scripts';

    iframe.onerror = function() {
        showPreviewStatus('error', 'Failed to load HTML file');
    };

    dom.previewContent.appendChild(iframe);

    // Open pane
    openRightPane();
}
