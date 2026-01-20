// State management module - DOM elements, state variables, and constants

// Constants
export const MAX_MESSAGES = 500;
export const MAX_TITLE_LENGTH = 50;

export const dateCategoryLabels = {
    today: 'Today',
    yesterday: 'Yesterday',
    lastWeek: 'Past 7 Days',
    older: 'Older',
    archived: 'Archived'
};

export const dateCategoryOrder = ['today', 'yesterday', 'lastWeek', 'older'];
export const archivedCategory = 'archived';

// DOM Elements - initialized after DOM is ready
export const dom = {
    sessionsContainer: null,
    projectListContainer: null,
    statusBar: null,
    statusText: null,
    sessionTitleBar: null,
    sidebar: null,
    sidebarOverlay: null,
    sidebarResizeHandle: null,
    hamburgerBtn: null,
    autoSwitchCheckbox: null,
    autoSwitchLabel: null,
    statusColorsCheckbox: null,
    groupBySelect: null,
    orderBySelect: null,
    searchInput: null,
    inputBar: null,
    mainContent: null,
    previewPane: null,
    previewFilename: null,
    previewPath: null,
    previewContent: null,
    previewStatus: null,
    previewCloseBtn: null,
    previewCopyBtn: null,
    previewResizeHandle: null,
    previewViewToggle: null,
    previewViewCheckbox: null,
    previewFollowToggle: null,
    previewFollowCheckbox: null,
    floatingControls: null,
    messageInput: null,
    inputStatus: null,
    sendBtn: null,
    forkBtn: null,
    interruptBtn: null,
    newSessionBtn: null,
    sessionTooltip: null,
    messageTooltip: null,
    scrollTopBtn: null,
    scrollBottomBtn: null,
    prevUserBtn: null,
    nextUserBtn: null,
    autoScrollCheckbox: null,
    autoScrollFloat: null,
    projectTitle: null,
    copyPathBtn: null,
    sessionIdBar: null,
    sessionIdValue: null,
    copySessionBtn: null,
    flashMessage: null,
    themeToggle: null,
    // Modal elements
    newSessionModal: null,
    newSessionForm: null,
    modalCloseBtn: null,
    modalCancelBtn: null,
    modalCwd: null,
    modalBackend: null,
    modalModelField: null,
    modalModel: null,
    modalModelSearch: null,
    // Permission modal elements
    permissionModal: null,
    permissionDenialsList: null,
    permissionGrantBtn: null,
    permissionRejectBtn: null
};

// Initialize DOM elements
export function initDom() {
    dom.sessionsContainer = document.getElementById('sessions');
    dom.projectListContainer = document.getElementById('project-list');
    dom.statusBar = document.getElementById('status-bar');
    dom.statusText = document.getElementById('status-text');
    dom.sessionTitleBar = document.getElementById('session-title-bar');
    dom.sidebar = document.getElementById('sidebar');
    dom.sidebarOverlay = document.getElementById('sidebar-overlay');
    dom.sidebarResizeHandle = document.getElementById('sidebar-resize-handle');
    dom.hamburgerBtn = document.getElementById('hamburger-btn');
    dom.autoSwitchCheckbox = document.getElementById('auto-switch');
    dom.autoSwitchLabel = document.getElementById('auto-switch-label');
    dom.statusColorsCheckbox = document.getElementById('status-colors');
    dom.groupBySelect = document.getElementById('group-by-select');
    dom.orderBySelect = document.getElementById('order-by-select');
    dom.searchInput = document.getElementById('sidebar-search');
    dom.inputBar = document.getElementById('input-bar');
    dom.mainContent = document.getElementById('main-content');
    dom.previewPane = document.getElementById('preview-pane');
    dom.previewFilename = document.getElementById('preview-filename');
    dom.previewPath = document.getElementById('preview-path');
    dom.previewContent = document.getElementById('preview-content');
    dom.previewStatus = document.getElementById('preview-status');
    dom.previewCloseBtn = document.getElementById('preview-close-btn');
    dom.previewCopyBtn = document.getElementById('preview-copy-btn');
    dom.previewResizeHandle = document.getElementById('preview-resize-handle');
    dom.previewViewToggle = document.getElementById('preview-view-toggle');
    dom.previewViewCheckbox = document.getElementById('preview-view-checkbox');
    dom.previewFollowToggle = document.getElementById('preview-follow-toggle');
    dom.previewFollowCheckbox = document.getElementById('preview-follow-checkbox');
    dom.floatingControls = document.getElementById('floating-controls');
    dom.messageInput = document.getElementById('message-input');
    dom.inputStatus = document.getElementById('input-status');
    dom.sendBtn = document.getElementById('send-btn');
    dom.forkBtn = document.getElementById('fork-btn');
    dom.interruptBtn = document.getElementById('interrupt-btn');
    dom.newSessionBtn = document.getElementById('new-session-btn');
    dom.sessionTooltip = document.getElementById('session-tooltip');
    dom.messageTooltip = document.getElementById('message-tooltip');
    dom.scrollTopBtn = document.getElementById('scroll-top-btn');
    dom.scrollBottomBtn = document.getElementById('scroll-bottom-btn');
    dom.prevUserBtn = document.getElementById('prev-user-btn');
    dom.nextUserBtn = document.getElementById('next-user-btn');
    dom.autoScrollCheckbox = document.getElementById('auto-scroll-checkbox');
    dom.autoScrollFloat = document.getElementById('auto-scroll-float');
    dom.projectTitle = document.getElementById('project-title');
    dom.copyPathBtn = document.getElementById('copy-path-btn');
    dom.sessionIdBar = document.getElementById('session-id-bar');
    dom.sessionIdValue = document.getElementById('session-id-value');
    dom.copySessionBtn = document.getElementById('copy-session-btn');
    dom.flashMessage = document.getElementById('flash-message');
    dom.themeToggle = document.getElementById('theme-toggle');
    dom.rightSidebarToggle = document.getElementById('right-sidebar-toggle');
    dom.previewBackBtn = document.getElementById('preview-back-btn');
    dom.treeCollapseBtn = document.getElementById('tree-collapse-btn');
    dom.treeExpandBtn = document.getElementById('tree-expand-btn');
    dom.fileTreeContent = document.getElementById('file-tree-content');
    // Modal elements
    dom.newSessionModal = document.getElementById('new-session-modal');
    dom.newSessionForm = document.getElementById('new-session-form');
    dom.modalCloseBtn = document.getElementById('modal-close-btn');
    dom.modalCancelBtn = document.getElementById('modal-cancel-btn');
    dom.modalCwd = document.getElementById('modal-cwd');
    dom.modalBackend = document.getElementById('modal-backend');
    dom.modalModelField = document.getElementById('modal-model-field');
    dom.modalModel = document.getElementById('modal-model');
    dom.modalModelSearch = document.getElementById('modal-model-search');
    // Permission modal elements
    dom.permissionModal = document.getElementById('permission-modal');
    dom.permissionDenialsList = document.getElementById('permission-denials-list');
    dom.permissionGrantBtn = document.getElementById('permission-grant-btn');
    dom.permissionRejectBtn = document.getElementById('permission-reject-btn');
}

// Application state
export const state = {
    currentProjectPath: null,
    currentSessionId: null,
    eventSource: null,

    // Data stores
    sessions: new Map(),      // session_id -> session object
    projects: new Map(),      // project_name -> { name, sessions: Set, lastActivity, element }
    sessionStatus: new Map(), // session_id -> { running, queued_messages, waiting_for_input }
    sessionPreviewPaths: new Map(), // session_id -> file path (track open preview per session)
    archivedSessionIds: new Set(), // session_ids that are archived (loaded from server)

    // UI state
    activeSessionId: null,
    sidebarOpen: localStorage.getItem('sidebarOpen') !== 'false',
    sidebarWidth: parseInt(localStorage.getItem('sidebarWidth')) || 280,
    previewPaneOpen: false,
    // Default wider pane for split view
    previewPaneWidth: parseInt(localStorage.getItem('previewPaneWidth')) || 700,
    previewFilePath: null,
    previewFileData: null,
    previewFollow: localStorage.getItem('previewFollow') !== 'false',  // Follow mode for file preview

    // Settings (with localStorage persistence)
    autoSwitch: localStorage.getItem('autoSwitch') !== 'false',
    autoScroll: localStorage.getItem('autoScroll') !== 'false',
    groupBy: localStorage.getItem('groupBy') || 'session',  // 'project' | 'session'
    sortBy: localStorage.getItem('sortBy') || 'created',    // 'created' | 'modified'

    // Session mode date sections (created lazily)
    dateSections: null,

    // Status colors feature (always enabled)
    titleColorsEnabled: true,
    statusColors: false, // initialized in init based on titleColorsEnabled

    // Runtime state
    autoSwitchDebounce: null,
    catchupComplete: false,
    sendEnabled: false,
    forkEnabled: false,
    defaultSendBackend: null,
    pendingSessionCounter: 0,
    pendingMessages: [],
    tooltipTimeout: null,
    messageTooltipTimeout: null,
    flashTimeout: null,

    // Modal state
    availableBackends: [],
    cachedModels: {},  // backend_name -> [models]
    allModelsForFilter: [],

    // Permission modal state
    pendingPermission: null,  // {session_id, denials, original_message}

    // Resize state
    isSidebarResizing: false,
    sidebarStartX: 0,
    sidebarStartWidth: 0,
    isPreviewResizing: false,
    previewStartX: 0,
    previewStartWidth: 0,
    isTreeResizing: false,
    treeStartX: 0,
    treeStartWidth: 0,
    treeSidebarWidth: parseInt(localStorage.getItem('treeSidebarWidth')) || 250
};

// Initialize status colors based on URL param
state.statusColors = state.titleColorsEnabled && localStorage.getItem('statusColors') !== 'false';
