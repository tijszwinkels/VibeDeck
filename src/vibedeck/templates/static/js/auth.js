// Authentication UI module
// Shows user indicator and logout button when auth is enabled

import { state } from './state.js';

let authContainer = null;

export function initAuth() {
    // Create auth container in status bar (before theme toggle)
    const statusBar = document.getElementById('status-bar');
    const themeToggle = document.getElementById('theme-toggle');

    if (!statusBar || !themeToggle) return;

    authContainer = document.createElement('div');
    authContainer.className = 'auth-container';
    authContainer.style.display = 'none';
    statusBar.insertBefore(authContainer, themeToggle);

    // Check auth status
    checkAuthStatus();
}

async function checkAuthStatus() {
    try {
        const resp = await fetch('/auth/user');
        if (!resp.ok) return;

        const data = await resp.json();
        state.authEnabled = data.auth_enabled;
        state.currentUser = data.user;

        if (data.auth_enabled && data.user) {
            showUserIndicator(data.user);
        }
    } catch (e) {
        // Auth endpoint not available — auth is disabled
    }
}

function showUserIndicator(user) {
    if (!authContainer) return;

    authContainer.innerHTML = '';
    authContainer.style.display = 'flex';

    const userName = document.createElement('span');
    userName.className = 'auth-user-name';
    userName.textContent = user.name || user.id;
    userName.title = 'Logged in as ' + (user.name || user.id);

    const logoutBtn = document.createElement('button');
    logoutBtn.className = 'auth-logout-btn';
    logoutBtn.textContent = 'Logout';
    logoutBtn.title = 'Sign out';
    logoutBtn.addEventListener('click', function() {
        window.location.href = '/logout';
    });

    authContainer.appendChild(userName);
    authContainer.appendChild(logoutBtn);
}
