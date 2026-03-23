// Modal module - new session modal, backend/model selection

import { dom, state } from './state.js';
import { createPendingSession } from './messaging.js';
import { formatModelName } from './utils.js';

let modalPreferredModelName = '';

function getSavedBackendPreference() {
    return localStorage.getItem('newSessionBackend') || '';
}

function getSavedModelPreference(backendName) {
    if (!backendName) return '';
    return localStorage.getItem('newSessionModel_' + backendName) || '';
}

function getActiveSessionModalDefaults() {
    if (!state.activeSessionId) {
        return { cwd: '', backend: '', modelName: '' };
    }

    const activeSession = state.sessions.get(state.activeSessionId);
    if (!activeSession) {
        return { cwd: '', backend: '', modelName: '' };
    }

    return {
        cwd: activeSession.cwd || '',
        backend: activeSession.selectedBackend || activeSession.backend || '',
        modelName: activeSession.selectedModelName || activeSession.model || ''
    };
}

function resolvePreferredBackend(preferredBackend) {
    const preferred = preferredBackend || '';
    const saved = getSavedBackendPreference();
    const candidates = [preferred, saved];

    for (const candidate of candidates) {
        if (candidate && state.availableBackends.some(function(backend) {
            return backend.name === candidate && backend.cli_available;
        })) {
            return candidate;
        }
    }

    const firstAvailable = state.availableBackends.find(function(backend) {
        return backend.cli_available;
    });
    return firstAvailable ? firstAvailable.name : '';
}

function resolvePreferredModel(backendName, models, preferredModelName, currentModelName = '') {
    const candidates = [
        currentModelName || '',
        preferredModelName || '',
        getSavedModelPreference(backendName),
        models.length > 0 ? models[0] : ''
    ];

    return candidates.find(function(candidate) {
        return candidate && models.includes(candidate);
    }) || '';
}

// Load available backends
async function loadBackends() {
    try {
        const response = await fetch('backends');
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
        const response = await fetch('backends/' + encodeURIComponent(backendName) + '/models');
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
function populateBackendSelect(preferredBackend = '') {
    dom.modalBackend.innerHTML = '';

    if (state.availableBackends.length === 0) {
        dom.modalBackend.innerHTML = '<option value="">No backends available</option>';
        return '';
    }

    const selectedBackend = resolvePreferredBackend(preferredBackend);

    state.availableBackends.forEach(function(backend) {
        const option = document.createElement('option');
        option.value = backend.name;
        option.textContent = backend.name;
        if (!backend.cli_available) {
            option.textContent += ' (CLI not available)';
            option.disabled = true;
        }
        dom.modalBackend.appendChild(option);
    });

    if (selectedBackend) {
        dom.modalBackend.value = selectedBackend;
    }

    return dom.modalBackend.value;
}

// Populate model select dropdown
async function populateModelSelect(backendName, preferredModelName = '') {
    // Find the backend info
    const backend = state.availableBackends.find(function(b) { return b.name === backendName; });

    if (!backend || !backend.supports_models) {
        dom.modalModelField.style.display = 'none';
        state.allModelsForFilter = [];
        modalPreferredModelName = '';
        return;
    }

    dom.modalModelField.style.display = 'block';
    dom.modalModel.innerHTML = '';
    dom.modalModelSearch.value = '';

    const models = await loadModelsForBackend(backendName);
    state.allModelsForFilter = models;
    modalPreferredModelName = preferredModelName || '';

    const selectedModel = resolvePreferredModel(
        backendName,
        models,
        modalPreferredModelName
    );

    models.forEach(function(model) {
        const option = document.createElement('option');
        option.value = model;
        option.textContent = formatModelName(model);
        dom.modalModel.appendChild(option);
    });

    if (selectedModel) {
        dom.modalModel.value = selectedModel;
    }
}

// Filter models based on search text
function filterModels(searchText) {
    const search = searchText.toLowerCase();
    dom.modalModel.innerHTML = '';

    const backendName = dom.modalBackend.value;
    const selectedModel = resolvePreferredModel(
        backendName,
        state.allModelsForFilter,
        modalPreferredModelName,
        dom.modalModel.value
    );

    state.allModelsForFilter.forEach(function(model) {
        if (!search || model.toLowerCase().includes(search) || formatModelName(model).toLowerCase().includes(search)) {
            const option = document.createElement('option');
            option.value = model;
            option.textContent = formatModelName(model);
            dom.modalModel.appendChild(option);
        }
    });

    if (selectedModel) {
        dom.modalModel.value = selectedModel;
    }
}

// Open the new session modal
async function openNewSessionModal() {
    // Load backends if not already loaded
    if (state.availableBackends.length === 0) {
        await loadBackends();
    }

    const activeDefaults = getActiveSessionModalDefaults();

    // Prefer the active session when opening the top-level "+ New" modal.
    dom.modalCwd.value = activeDefaults.cwd || localStorage.getItem('newSessionCwd') || '';

    // Populate backend/model based on the active session, with saved preferences as fallback.
    const selectedBackend = populateBackendSelect(activeDefaults.backend);

    // Populate model select for the selected backend
    await populateModelSelect(selectedBackend, activeDefaults.modelName);

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
