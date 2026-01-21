// Modal module - new session modal, backend/model selection

import { dom, state } from './state.js';
import { createPendingSession } from './messaging.js';

// Load available backends
async function loadBackends() {
    try {
        const response = await fetch('/backends');
        if (response.ok) {
            const data = await response.json();
            state.availableBackends = data.backends || [];
        }
    } catch (e) {
        console.error('Failed to load backends:', e);
        state.availableBackends = [];
    }
}

// Load models for a specific backend
async function loadModelsForBackend(backendName) {
    // Check cache first
    if (state.cachedModels[backendName]) {
        return state.cachedModels[backendName];
    }

    try {
        const response = await fetch('/backends/' + encodeURIComponent(backendName) + '/models');
        if (response.ok) {
            const data = await response.json();
            state.cachedModels[backendName] = data.models || [];
            return state.cachedModels[backendName];
        }
    } catch (e) {
        console.error('Failed to load models for ' + backendName + ':', e);
    }
    return [];
}

// Populate backend select dropdown
function populateBackendSelect() {
    dom.modalBackend.innerHTML = '';

    if (state.availableBackends.length === 0) {
        dom.modalBackend.innerHTML = '<option value="">No backends available</option>';
        return;
    }

    // Get saved backend preference
    const savedBackend = localStorage.getItem('newSessionBackend') || '';

    state.availableBackends.forEach(function(backend) {
        const option = document.createElement('option');
        option.value = backend.name;
        option.textContent = backend.name;
        if (!backend.cli_available) {
            option.textContent += ' (CLI not available)';
            option.disabled = true;
        }
        if (backend.name === savedBackend || (!savedBackend && backend.cli_available)) {
            option.selected = true;
        }
        dom.modalBackend.appendChild(option);
    });
}

// Populate model select dropdown
async function populateModelSelect(backendName) {
    // Find the backend info
    const backend = state.availableBackends.find(function(b) { return b.name === backendName; });

    if (!backend || !backend.supports_models) {
        dom.modalModelField.style.display = 'none';
        state.allModelsForFilter = [];
        return;
    }

    dom.modalModelField.style.display = 'block';
    dom.modalModel.innerHTML = '';
    dom.modalModelSearch.value = '';

    const models = await loadModelsForBackend(backendName);
    state.allModelsForFilter = models;

    // Get saved model preference for this backend (default to first model if none saved)
    const savedModel = localStorage.getItem('newSessionModel_' + backendName) || (models.length > 0 ? models[0] : '');

    models.forEach(function(model) {
        const option = document.createElement('option');
        option.value = model;
        option.textContent = model;
        if (model === savedModel) {
            option.selected = true;
        }
        dom.modalModel.appendChild(option);
    });
}

// Filter models based on search text
function filterModels(searchText) {
    const search = searchText.toLowerCase();
    dom.modalModel.innerHTML = '';

    const savedBackend = dom.modalBackend.value;
    // Default to first model if none saved
    const savedModel = localStorage.getItem('newSessionModel_' + savedBackend) || (state.allModelsForFilter.length > 0 ? state.allModelsForFilter[0] : '');

    state.allModelsForFilter.forEach(function(model) {
        if (!search || model.toLowerCase().includes(search)) {
            const option = document.createElement('option');
            option.value = model;
            option.textContent = model;
            if (model === savedModel) {
                option.selected = true;
            }
            dom.modalModel.appendChild(option);
        }
    });
}

// Open the new session modal
async function openNewSessionModal() {
    // Load backends if not already loaded
    if (state.availableBackends.length === 0) {
        await loadBackends();
    }

    // Get a default directory from active session if available
    let defaultDir = '';
    if (state.activeSessionId) {
        const activeSession = state.sessions.get(state.activeSessionId);
        if (activeSession && activeSession.cwd) {
            defaultDir = activeSession.cwd;
        }
    }

    // Load saved cwd or use default
    const savedCwd = localStorage.getItem('newSessionCwd');
    dom.modalCwd.value = savedCwd || defaultDir;

    // Populate backend select
    populateBackendSelect();

    // Populate model select for the selected backend
    await populateModelSelect(dom.modalBackend.value);

    // Show modal
    dom.newSessionModal.showModal();
    dom.modalCwd.focus();
}

// Close the modal
function closeNewSessionModal() {
    dom.newSessionModal.close();
}

// Handle form submission
function handleNewSessionSubmit(e) {
    e.preventDefault();

    const cwd = dom.modalCwd.value.trim();
    if (!cwd) {
        alert('Please enter a directory path');
        return;
    }

    const backend = dom.modalBackend.value;
    const modelSelect = dom.modalModel;
    // Model index is direct now (no "(Default)" option)
    const modelIndex = modelSelect.selectedIndex >= 0 ? modelSelect.selectedIndex : null;
    const modelName = modelIndex !== null ? modelSelect.value : null;

    // Save preferences to localStorage
    localStorage.setItem('newSessionCwd', cwd);
    if (backend) {
        localStorage.setItem('newSessionBackend', backend);
    }
    if (backend && modelName) {
        localStorage.setItem('newSessionModel_' + backend, modelName);
    }

    // Extract project name from path (last component)
    const projectName = cwd.split('/').filter(function(s) { return s; }).pop() || 'New Session';

    // Close modal
    closeNewSessionModal();

    // Create pending session with backend/model info stored
    createPendingSession(cwd, projectName, backend, modelIndex, modelName);
}

// Initialize modal event listeners
export function initModal() {
    dom.modalBackend.addEventListener('change', function() {
        populateModelSelect(dom.modalBackend.value);
    });

    dom.modalModelSearch.addEventListener('input', function() {
        filterModels(dom.modalModelSearch.value);
    });

    dom.modalCloseBtn.addEventListener('click', closeNewSessionModal);
    dom.modalCancelBtn.addEventListener('click', closeNewSessionModal);

    dom.newSessionForm.addEventListener('submit', handleNewSessionSubmit);

    // Close modal on backdrop click
    dom.newSessionModal.addEventListener('click', function(e) {
        if (e.target === dom.newSessionModal) {
            closeNewSessionModal();
        }
    });

    dom.newSessionBtn.addEventListener('click', openNewSessionModal);
}
