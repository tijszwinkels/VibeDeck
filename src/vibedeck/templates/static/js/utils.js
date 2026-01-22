// Utility functions module

import { dateCategoryLabels, dateCategoryOrder } from './state.js';

// Check if mobile viewport
export function isMobile() {
    return window.innerWidth <= 768;
}

// Date category helpers
export function getDateCategory(timestamp) {
    if (!timestamp) return 'older';
    const date = typeof timestamp === 'number' ? new Date(timestamp * 1000) : new Date(timestamp);
    if (isNaN(date.getTime())) return 'older';

    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const lastWeek = new Date(today);
    lastWeek.setDate(lastWeek.getDate() - 7);

    const sessionDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());

    if (sessionDate >= today) return 'today';
    if (sessionDate >= yesterday) return 'yesterday';
    if (sessionDate >= lastWeek) return 'lastWeek';
    return 'older';
}

// Get the sort timestamp for a session based on current sort mode
export function getSessionSortTimestamp(session, sortBy) {
    if (sortBy === 'created') {
        // startedAt is ISO string, convert to ms
        return session.startedAt ? new Date(session.startedAt).getTime() : 0;
    }
    // Default: modified (lastActivity is already in ms)
    return session.lastActivity || 0;
}

// Get the timestamp for date categorization (for sidebar grouping)
export function getSessionCategoryTimestamp(session, sortBy) {
    if (sortBy === 'created') {
        return session.startedAt;  // ISO string or null
    }
    return session.lastUpdatedAt;  // Unix seconds or null
}

// Status color helpers - parse status from title suffix (e.g., "Title - Merged" -> "Merged")
export function parseStatusFromTitle(title) {
    if (!title) return null;
    const match = title.match(/ - ([^-]+)$/);
    return match ? match[1].trim() : null;
}

export function getStatusClass(status) {
    if (!status) return '';
    const s = status.toLowerCase();
    // Green: done or merged
    if (s === 'done' || s === 'merged') {
        return 'status-done';
    }
    // Yellow: waiting for something
    if (s.startsWith('waiting for')) {
        return 'status-waiting';
    }
    // Blue: in progress
    if (s === 'in progress') {
        return 'status-in-progress';
    }
    return '';
}

// HTML escaping
export function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Title truncation
export function truncateTitle(text, maxLength) {
    if (!text) return 'Untitled';
    // Remove newlines and excess whitespace
    text = text.replace(/\s+/g, ' ').trim();
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
}

// Timestamp formatting
export function formatTimestamp(ts) {
    if (!ts) return 'Unknown';
    const date = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
    if (isNaN(date.getTime())) return 'Unknown';
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    const timeStr = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    if (isToday) {
        return 'Today ' + timeStr;
    }
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + timeStr;
}

// Short timestamp formatting for sidebar cards
// Shows: "9:35" if today, "Mon 9:35" if this week, "Jan 15" if older
export function formatShortTimestamp(ts) {
    if (!ts) return '';
    const date = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
    if (isNaN(date.getTime())) return '';

    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const sessionDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());

    const timeStr = date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

    // Today: just time
    if (sessionDate.getTime() === today.getTime()) {
        return timeStr;
    }

    // Within the last 7 days: "Mon 9:35"
    const oneWeekAgo = new Date(today);
    oneWeekAgo.setDate(oneWeekAgo.getDate() - 7);
    if (sessionDate > oneWeekAgo) {
        const dayName = date.toLocaleDateString(undefined, { weekday: 'short' });
        return dayName + ' ' + timeStr;
    }

    // Different year: "2025 Jan 15"
    if (date.getFullYear() !== now.getFullYear()) {
        return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    }

    // Older but same year: "Jan 15"
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

// Token count formatting
export function formatTokenCount(count) {
    if (!count || count === 0) return '0';
    if (count >= 1000000) {
        return (count / 1000000).toFixed(1) + 'M';
    } else if (count >= 1000) {
        return (count / 1000).toFixed(1) + 'K';
    }
    return count.toString();
}

// Cost formatting
export function formatCost(cost) {
    if (!cost || cost === 0) return '$0.00';
    if (cost < 0.01) return '<$0.01';
    return '$' + cost.toFixed(2);
}

// Model name formatting
export function formatModelName(model) {
    if (!model) return '';
    // Format 1: claude-opus-4-5-20251101 -> "Opus 4.5"
    // Format 2: claude-sonnet-4-20250514 -> "Sonnet 4"
    let match = model.match(/claude-(\w+)-(\d+)(?:-(\d+))?-\d{8}/);
    if (match) {
        const name = match[1].charAt(0).toUpperCase() + match[1].slice(1);
        const version = match[3] ? `${match[2]}.${match[3]}` : match[2];
        return `${name} ${version}`;
    }
    // Format 3: claude-3-7-sonnet-20250219 -> "Sonnet 3.7"
    match = model.match(/claude-(\d+)(?:-(\d+))?-(\w+)-\d{8}/);
    if (match) {
        const name = match[3].charAt(0).toUpperCase() + match[3].slice(1);
        const version = match[2] ? `${match[1]}.${match[2]}` : match[1];
        return `${name} ${version}`;
    }
    return model;
}

// Clipboard operations
export function copyToClipboard(text, btn) {
    // Try modern clipboard API first, fall back to execCommand for non-HTTPS
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() {
            showCopySuccess(btn);
        }).catch(function() {
            fallbackCopy(text, btn);
        });
    } else {
        fallbackCopy(text, btn);
    }
}

function fallbackCopy(text, btn) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    try {
        document.execCommand('copy');
        showCopySuccess(btn);
    } catch (err) {
        console.error('Failed to copy:', err);
    }
    document.body.removeChild(textarea);
}

function showCopySuccess(btn) {
    if (btn) {
        btn.classList.add('copied');
        setTimeout(function() {
            btn.classList.remove('copied');
        }, 1500);
    }
}

// Check if user is scrolled near bottom of page
export function isNearBottom() {
    const threshold = 150;
    return (window.innerHeight + window.scrollY) >= (document.body.offsetHeight - threshold);
}
