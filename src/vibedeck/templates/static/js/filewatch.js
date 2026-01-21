// File watch module - SSE-based live file updates

import { state } from './state.js';

// Current file watch connection
let fileWatchEventSource = null;
let currentWatchPath = null;

/**
 * Start watching a file for changes via SSE.
 *
 * @param {string} filePath - Absolute path to the file to watch
 * @param {Object} callbacks - Event handlers
 * @param {function(Object)} callbacks.onInitial - Called with initial file content
 * @param {function(Object)} callbacks.onAppend - Called when content is appended (follow=true only)
 * @param {function(Object)} callbacks.onReplace - Called when file is replaced/rewritten (follow=true only)
 * @param {function(Object)} callbacks.onChanged - Called when file changed (follow=false only) - frontend should refetch
 * @param {function(string)} callbacks.onError - Called on error
 * @param {Object} options - Watch options
 * @param {boolean} options.follow - If true, detect appends and send content. If false, just notify of changes.
 */
export function startFileWatch(filePath, callbacks, options = {}) {
    // Stop any existing watch
    stopFileWatch();

    currentWatchPath = filePath;
    const follow = options.follow ? 'true' : 'false';
    const url = `/api/file/watch?path=${encodeURIComponent(filePath)}&follow=${follow}`;

    fileWatchEventSource = new EventSource(url);

    // Guard function to prevent processing events for stale connections
    // (e.g., if user rapidly switches between files)
    const isStale = () => currentWatchPath !== filePath;

    fileWatchEventSource.addEventListener('initial', function(e) {
        if (isStale()) return;  // Ignore events for old file
        const data = JSON.parse(e.data);
        if (callbacks.onInitial) {
            callbacks.onInitial(data);
        }
    });

    fileWatchEventSource.addEventListener('append', function(e) {
        if (isStale()) return;
        const data = JSON.parse(e.data);
        if (callbacks.onAppend) {
            callbacks.onAppend(data);
        }
    });

    fileWatchEventSource.addEventListener('replace', function(e) {
        if (isStale()) return;
        const data = JSON.parse(e.data);
        if (callbacks.onReplace) {
            callbacks.onReplace(data);
        }
    });

    // 'changed' event is sent when follow=false - frontend should refetch via /api/file
    fileWatchEventSource.addEventListener('changed', function(e) {
        if (isStale()) return;
        const data = JSON.parse(e.data);
        if (callbacks.onChanged) {
            callbacks.onChanged(data);
        }
    });

    fileWatchEventSource.addEventListener('error', function(e) {
        if (isStale()) return;
        // Check if this is an SSE error event with data
        if (e.data) {
            const data = JSON.parse(e.data);
            if (callbacks.onError) {
                callbacks.onError(data.message);
            }
        }
    });

    fileWatchEventSource.onerror = function(e) {
        // Connection error - attempt reconnect after delay
        if (fileWatchEventSource && fileWatchEventSource.readyState === EventSource.CLOSED) {
            console.warn('File watch connection closed, attempting reconnect...');
            // Reconnect after 1 second if we're still watching the same file
            setTimeout(function() {
                if (currentWatchPath === filePath && state.previewPaneOpen) {
                    // Use current follow state, not the stale one from options
                    startFileWatch(filePath, callbacks, { follow: state.previewFollow });
                }
            }, 1000);
        }
    };
}

/**
 * Stop watching the current file.
 */
export function stopFileWatch() {
    if (fileWatchEventSource) {
        fileWatchEventSource.close();
        fileWatchEventSource = null;
    }
    currentWatchPath = null;
}

/**
 * Check if currently watching a file.
 * @returns {boolean}
 */
export function isWatching() {
    return fileWatchEventSource !== null && fileWatchEventSource.readyState === EventSource.OPEN;
}

/**
 * Get the path of the currently watched file.
 * @returns {string|null}
 */
export function getWatchedPath() {
    return currentWatchPath;
}
