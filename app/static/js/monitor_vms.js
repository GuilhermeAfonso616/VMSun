(function () {
    var bootstrapEl = document.getElementById("monitorBootstrap");
    var boot = {};

    if (bootstrapEl && bootstrapEl.textContent) {
        try {
            boot = JSON.parse(bootstrapEl.textContent);
        } catch (error) {
            console.warn("Falha ao ler bootstrap do monitor", error);
            boot = {};
        }
    }

    var selectedGrid = Number(boot.selectedGrid || 4);
    var initialStats = boot.stats || {};
    var initialGatewayHealth = boot.gatewayHealth || null;
    var initialAvailable = Array.isArray(boot.availableCameras) ?boot.availableCameras : [];
    var initialVisible = Array.isArray(boot.visibleCameras) ?boot.visibleCameras : [];
    var initialAlarms = Array.isArray(boot.alarms) ?boot.alarms : [];
    var webrtcMonitorEnabled = !!boot.webrtcMonitorEnabled;
    var webrtcPublicBaseUrl = safeString(boot.webrtcPublicBaseUrl).replace(/\/+$/, "");
    var monitorDevMode = !!boot.monitorDevMode;
    var canControlCamera = !!boot.monitorCanControlCamera;
    var mosaicStorageSuffix = ":user:" + safeString(boot.mosaicStorageUserId || "anonymous");
    var queueSessionKey = safeString(boot.queueSessionKey || boot.queueSessionStartedAt || "session");
    var queueSessionStartedAt = safeString(boot.queueSessionStartedAt || "");
    var queueSessionStartedMs = parseAlarmTimestamp(queueSessionStartedAt);
    var alarmStorageSuffix = mosaicStorageSuffix + ":session:" + safeString(queueSessionKey || "active");
    var initialQueueSignature = boot.alarmQueueSignature || "";
    var initialSignature = boot.latestAlarmSignature || "";
    var initialAllowSound = !!boot.alarmShouldPlay;
    var initialPopupShouldShow = !!boot.popupShouldShow;
    var initialPopupAlarm = boot.latestPopupAlarm || null;
    var webTrackTransportMode = safeString(
        boot.webTrackTransportMode || "polling"
    ).toLowerCase();
    if (["polling", "sse_prefer", "sse_strict"].indexOf(webTrackTransportMode) === -1) {
        webTrackTransportMode = "polling";
    }
    var webTrackSseCameraIds = safeString(boot.webTrackSseCameraIds || "");
    var visualTrackFreshMs = Math.max(100, Number(boot.visualTrackFreshMs || 500));
    var visualTrackRetentionMs = Math.max(
        visualTrackFreshMs,
        Number(boot.visualTrackRetentionMs || 3000)
    );

    var localFitOverrides = {};

    var wallEl = document.getElementById("vmsWall");
    var libraryListEl = document.getElementById("cameraLibraryList");
    var alarmSidebarEl = document.getElementById("alarmSidebarList");
    var alarmRefreshMetaEl = document.getElementById("alarmRefreshMeta");
    var searchInputEl = document.getElementById("cameraSearchInput");
    var gatewayHealthValueEl = document.getElementById("gatewayHealthValue");
    var gatewayHealthDetailEl = document.getElementById("gatewayHealthDetail");
    var codecFallbackNoticeEl = document.getElementById("vmsCodecFallbackNotice");
    var autoFillBtn = document.getElementById("autoFillBtn");
    var autoFillVisibleBtn = document.getElementById("autoFillVisibleBtn");
    var autoFillAlarmBtn = document.getElementById("autoFillAlarmBtn");
    var testCentralPopupBtn = document.getElementById("testCentralPopupBtn");
    var libraryFilterButtons = document.querySelectorAll("[data-library-filter]");
    var sequenceToggle = document.getElementById("sequenceToggle");
    var classicSeqPlay = document.getElementById("classicSeqPlay");
    var classicSeqPause = document.getElementById("classicSeqPause");
    var classicSeqNext = document.getElementById("classicSeqNext");
    var layoutLockToggle = document.getElementById("layoutLockToggle");
    var clearLayoutBtn = document.getElementById("clearLayoutBtn");
    var fullscreenBtn = document.getElementById("fullscreenBtn");
    var globalMuteToggle = document.getElementById("globalMuteToggle");
    var operatorModeToggle = document.getElementById("operatorModeToggle");
    var tileDetailsToggle = document.getElementById("tileDetailsToggle");
    var tileHeadersToggle = document.getElementById("tileHeadersToggle");
    var webrtcPureToggle = document.getElementById("webrtcPureToggle");
    var overlayToggleBtn = document.getElementById("overlayToggleBtn");
    var boxesToggleBtn = document.getElementById("boxesToggleBtn");
    var boxConfidenceInput = document.getElementById("boxConfidenceInput");
    var layoutModeSelect = document.getElementById("layoutModeSelect");
    var sandboxControlsEl = document.getElementById("vmsSandboxControls");
    var sandboxAddTileBtn = document.getElementById("sandboxAddTileBtn");
    var sandboxRemoveTileBtn = document.getElementById("sandboxRemoveTileBtn");
    var videoFitToggle = document.getElementById("videoFitToggle");
    var densityToggle = document.getElementById("densityToggle");
    var savedViewSelect = document.getElementById("savedViewSelect");
    var saveViewBtn = document.getElementById("saveViewBtn");
    var deleteViewBtn = document.getElementById("deleteViewBtn");
    var shareViewCheckbox = document.getElementById("shareViewCheckbox");
    var temporalSequenceSelect = document.getElementById("temporalSequenceSelect");
    var vmsTemporalSequenceBanner = document.getElementById("vmsTemporalSequenceBanner");
    var vmsTemporalSequenceInfo = document.getElementById("vmsTemporalSequenceInfo");
    var stopTemporalSequenceBtn = document.getElementById("stopTemporalSequenceBtn");
    var moreOptionsEl = document.querySelector(".vms-more-options");
    var gridButtons = document.querySelectorAll(".vms-grid-btn");
    var leftPanelButtons = document.querySelectorAll("[data-left-panel]");
    var libraryPanelEl = document.getElementById("sunorusLibraryPanel");
    var mapPanelEl = document.getElementById("sunorusMapPanel");
    var mapCanvasEl = document.getElementById("sunorusMapCanvas");
    var mapCountEl = document.getElementById("sunorusMapCount");
    var keyboardHelpBtn = document.getElementById("keyboardHelpBtn");
    var shortcutModalEl = document.getElementById("sunorusShortcutModal");
    var shortcutCloseBtn = document.getElementById("sunorusShortcutClose");
    var evidenceModalEl = document.getElementById("sunorusEvidenceModal");
    var evidenceCloseBtn = document.getElementById("sunorusEvidenceClose");
    var evidenceTitleEl = document.getElementById("sunorusEvidenceTitle");
    var evidenceKindEl = document.getElementById("sunorusEvidenceKind");
    var evidenceMetaEl = document.getElementById("sunorusEvidenceMeta");
    var evidenceBodyEl = document.getElementById("sunorusEvidenceBody");
    var evidenceOpenEl = document.getElementById("sunorusEvidenceOpen");
    var spotlightEl = document.getElementById("sunorusSpotlight");
    var spotlightTitleEl = document.getElementById("sunorusSpotlightTitle");
    var spotlightMetaEl = document.getElementById("sunorusSpotlightMeta");
    var spotlightOpenEl = document.getElementById("sunorusSpotlightOpen");
    var spotlightAckBtn = document.getElementById("sunorusSpotlightAck");
    var spotlightPinBtn = document.getElementById("sunorusSpotlightPin");
    var spotlightRemoveBtn = document.getElementById("sunorusSpotlightRemove");
    var spotlightCloseBtn = document.getElementById("sunorusSpotlightClose");
    var spotlightToggle = document.getElementById("spotlightToggle");
    var liveAlarmModalEl = document.getElementById("sunorusLiveAlarmModal");
    var liveAlarmKickerEl = document.getElementById("sunorusLiveAlarmKicker");
    var liveAlarmTitleEl = document.getElementById("sunorusLiveAlarmTitle");
    var liveAlarmMetaEl = document.getElementById("sunorusLiveAlarmMeta");
    var liveAlarmQueueEl = document.getElementById("sunorusLiveAlarmQueue");
    var liveAlarmVideoEl = document.getElementById("sunorusLiveAlarmVideo");
    var liveAlarmEventTypeEl = document.getElementById("sunorusLiveAlarmEventType");
    var liveAlarmSeverityEl = document.getElementById("sunorusLiveAlarmSeverity");
    var liveAlarmStatusEl = document.getElementById("sunorusLiveAlarmStatus");
    var liveAlarmAckBtn = document.getElementById("sunorusLiveAlarmAck");
    var liveAlarmAuthorizeBtn = document.getElementById("sunorusLiveAlarmAuthorize");
    var liveAlarmCloseEventBtn = document.getElementById("sunorusLiveAlarmCloseEvent");
    var liveAlarmOpenEventEl = document.getElementById("sunorusLiveAlarmOpenEvent");
    var liveAlarmPinBtn = document.getElementById("sunorusLiveAlarmPin");
    var liveAlarmMinimizeBtn = document.getElementById("sunorusLiveAlarmMinimize");
    var liveAlarmSnapshotBtn = document.getElementById("sunorusLiveAlarmSnapshot");
    var liveAlarmClipBtn = document.getElementById("sunorusLiveAlarmClip");
    var liveAlarmEvidenceEl = document.getElementById("sunorusLiveAlarmEvidence");
    var liveAlarmEvidenceKindEl = document.getElementById("sunorusLiveAlarmEvidenceKind");
    var liveAlarmEvidenceTitleEl = document.getElementById("sunorusLiveAlarmEvidenceTitle");
    var liveAlarmEvidenceCloseBtn = document.getElementById("sunorusLiveAlarmEvidenceClose");
    var liveAlarmEvidenceBodyEl = document.getElementById("sunorusLiveAlarmEvidenceBody");
    var clearLiveAlarmQueueBtn = document.getElementById("clearLiveAlarmQueueBtn");
    var ptzPanelEl = document.getElementById("monitorPtzPanel");
    var ptzCameraNameEl = document.getElementById("monitorPtzCameraName");
    var ptzCameraMetaEl = document.getElementById("monitorPtzCameraMeta");
    var ptzDiagnosticsWrapEl = document.getElementById("monitorPtzDiagnosticsWrap");
    var ptzDiagnosticsEl = document.getElementById("monitorPtzDiagnostics");
    var ptzOpenDetailEl = document.getElementById("monitorPtzOpenDetail");
    var ptzReinspectEl = document.getElementById("monitorPtzReinspect");
    var ptzStatusEl = document.getElementById("monitorPtzStatus");
    var ptzSpeedEl = document.getElementById("monitorPtzSpeed");
    var ptzSpeedValueEl = document.getElementById("monitorPtzSpeedValue");
    var ptzPresetSelectEl = document.getElementById("monitorPtzPresetSelect");
    var ptzPresetGoEl = document.getElementById("monitorPtzPresetGo");
    var ptzPresetRefreshEl = document.getElementById("monitorPtzPresetRefresh");
    var ptzPresetStatusEl = document.getElementById("monitorPtzPresetStatus");

    var queryString = new URLSearchParams(window.location.search).toString();
    var dataUrl = "/monitor/data" + (queryString ?"?" + queryString : "");
    var libraryDataUrl = "/monitor/library" + (queryString ?"?" + queryString : "");
    var alarmDataUrl = "/monitor/alarms" + (queryString ?"?" + queryString : "");
    var tracksDataUrl = "/monitor/tracks";
    var pollIntervalMs = 2500;
    var libraryPollIntervalMs = 15000;
    var alarmPollIntervalMs = 1500;
    var tracksPollIntervalMs = 800;
    var tracksMaxAgeSeconds = visualTrackRetentionMs / 1000;
    var webrtcDiagnosticsPollIntervalMs = 8000;
    var snapshotFallbackIntervalMs = 2500;
    // O navegador abre no maximo 6 conexoes simultaneas por origem (HTTP/1.1) e
    // cada MJPEG segura uma delas indefinidamente. Reservamos 1 slot por origem
    // para health check e retentativas; o excedente usa polling de snapshot.
    var streamSlotsPerOrigin = 5;
    var snapshotPollIntervalMs = 900;
    var alarmSignatureKey = "server_analiticos_monitor_alarm_signature" + alarmStorageSuffix;
    var alarmQueueSignatureKey = "server_analiticos_monitor_alarm_queue_signature" + alarmStorageSuffix;
    var layoutKey = "server_analiticos_vms_layout_v1:grid:" + selectedGrid;
    var layoutStateKey = "server_analiticos_vms_layout_state_v1";
    var tileDetailsKey = "server_analiticos_vms_tile_details_v1";
    var overlayKey = "server_analiticos_vms_overlays_v1";
    var boxesKey = "server_analiticos_vms_boxes_v1";
    var webrtcPureKey = "server_analiticos_vms_webrtc_pure_v2";
    var videoHelperBaseUrl = "http://127.0.0.1:34020";
    var webrtcPlayerProbeCache = new Map();
    var webrtcPlayerLogKeys = new Set();
    var boxConfidenceKey = "server_analiticos_vms_box_confidence_v2";
    var gridMemoryKey = "server_analiticos_vms_last_grid_v1";
    var layoutModeKey = "server_analiticos_vms_layout_mode_v1";
    var videoFitKey = "server_analiticos_vms_video_fit_v1";
    var densityKey = "server_analiticos_vms_density_v1";
    var operatorModeKey = "server_analiticos_vms_operator_mode_v1";
    var layoutLockedKey = "server_analiticos_vms_layout_locked_v1";
    var sequenceEnabledKey = "server_analiticos_vms_sequence_enabled_v1";
    var spotlightEnabledKey = "server_analiticos_vms_spotlight_enabled_v1";
    var savedViewsKey = "server_analiticos_vms_saved_views_v1" + mosaicStorageSuffix;
    var pendingViewKey = "server_analiticos_vms_pending_view_v1";
    var tileHeadersFixedKey = "server_analiticos_vms_tile_headers_fixed_v1";
    var centralPopupEnabledKey = "server_analiticos_vms_central_popup_v1";
    var temporalMosaicsKey = "vms_temporal_mosaics" + mosaicStorageSuffix;
    var activeSequenceKey = "vms_active_sequence" + mosaicStorageSuffix;
    var temporalSequenceTimerId = null;
    var temporalSequenceCountdownIntervalId = null;
    var tileDetailsEnabled = false;
    var centralPopupEnabled = true;
    var overlaysEnabled = true;
    var boxesEnabled = true;
    // This is only the visual filter; detector and alarm thresholds stay unchanged.
    var minBoxConfidence = 0.15;
    var layoutMode = "standard";
    var videoFitMode = "fit";
    var densityMode = "normal";
    var operatorModeEnabled = false;
    var layoutLocked = false;
    var sequenceEnabled = false;
    var spotlightEnabled = true;
    var tileHeadersFixed = false;
    var webrtcPureMode = false;
    var sequenceCursor = 0;
    var sequenceTimerId = null;
    var sequenceIntervalMs = 12000;

    var firstAlarmPoll = true;
    var stats = initialStats || {};
    var gatewayHealth = initialGatewayHealth || null;
    var availableCameras = Array.isArray(initialAvailable) ?initialAvailable : [];
    var visibleCameras = Array.isArray(initialVisible) ?initialVisible : [];
    var alarms = Array.isArray(initialAlarms) ?initialAlarms : [];
    var cameraSearch = "";
    var libraryFilter = "all";
    var assignments = [];
    var cameraById = new Map();
    var monitorPollRunning = false;
    var libraryPollRunning = false;
    var alarmPollRunning = false;
    var tracksPollRunning = false;
    var lastAlarmQueueSignature = initialQueueSignature || "";
    var monitorPollTimerId = null;
    var libraryPollTimerId = null;
    var alarmPollTimerId = null;
    var tracksPollTimerId = null;
    var tracksSseSource = null;
    var tracksSseCameraSignature = "";
    var tracksSseConnected = false;
    var tracksSseReconnectTimerId = null;
    var trackTransportMetrics = {
        polling_requests_total: 0,
        sse_messages_total: 0,
        sse_errors_total: 0,
        polling_fallback_total: 0,
        visual_updates_out_of_order_total: 0,
        visual_updates_identity_rejected_total: 0,
        visual_updates_coalesced_total: 0,
        visual_updates_stale_total: 0,
        visual_empty_results_total: 0,
        visual_boxes_expired_total: 0
    };
    var clientLatencySamples = {
        backend_to_client_ms: [],
        client_render_ms: []
    };
    var pendingTrackUpdates = new Map();
    var pendingTrackAnimationFrame = null;
    var webrtcDiagnosticsTimerId = null;
    var snapshotFallbackTimerId = null;
    var streamBootTimeouts = [];
    var pageCleanupDone = false;
    var disposed = false;
    var lastRenderedWallFingerprint = "";
    var tileClickTimerId = null;
    var focusRestoreState = null;
    var clientHevcSupportKnown = false;
    var clientHevcSupported = false;
    var videoHelperAvailable = false;
    var videoHelperRetryTimerId = null;
    var videoHelperRetryDelayMs = 3000;
    var videoHelperPorts = [34020];
    // Instalador publicado no servidor, consultado so quando o operador precisa
    // dele: navegador sem HEVC e sem helper rodando na estacao.
    var videoHelperDownload = null;
    var videoHelperDownloadRequested = false;
    var currentLeftPanel = "library";
    var spotlightAlarmState = null;
    var liveAlarmQueue = [];
    var liveAlarmCurrent = null;
    var liveAlarmHandledIds = new Set();
    var liveAlarmDismissedIds = new Set();
    var liveAlarmKnownIds = new Set(
        (initialAlarms || []).map(function (alarm) {
            return liveAlarmEventId(alarm);
        }).filter(Boolean)
    );
    var liveAlarmRenderedEventId = "";
    var liveAlarmSnapshotTimerId = null;
    var liveAlarmHelperFailedEvents = new Set();
    var liveAlarmActionPending = false;
    var allowedGridValues = [1, 2, 4, 6, 8, 9, 12, 16, 25];
    var selectedCameraId = "";
    var audioCameraId = "";
    var selectedCameraPtzInfo = null;
    var selectedCameraPtzLoading = false;
    var selectedCameraPtzError = "";
    var selectedCameraPtzRequestId = "";
    var selectedCameraPtzMoveState = null;
    var selectedCameraPtzPresets = [];
    var selectedCameraPtzPresetsCameraId = "";
    var selectedCameraPtzPresetsLoading = false;
    var selectedCameraPtzPresetsError = "";
    var selectedCameraPtzPresetsMessage = "";
    var selectedCameraPtzPresetsRequestId = "";
    var dpadMoveInFlight = false;
    var ptz3dEnabled = false;
    var selectedCameraPtzQueue = Promise.resolve();
    var ptzInspectionCache = {};

    if (restoreGridPreference()) {
        return;
    }

    function safeString(value) {
        if (value === null || value === undefined) return "";
        return String(value);
    }

    function parseAlarmTimestamp(value) {
        var parsed = Date.parse(safeString(value));
        return Number.isFinite(parsed) ?parsed : 0;
    }

    function escapeHtml(value) {
        return safeString(value)
            .split("&").join("&amp;")
            .split("<").join("&lt;")
            .split(">").join("&gt;")
            .split('"').join("&quot;")
            .split("'").join("&#039;");
    }

    function syncMoreOptionsOpenState() {
        document.body.classList.toggle("vms-more-options-open", !!(moreOptionsEl && moreOptionsEl.open));
    }

    function buildQueueSignatureFromAlarms(list) {
        if (!Array.isArray(list) || !list.length) return "";
        return list.map(function (alarm) {
            return [
                safeString(alarm.id),
                safeString(alarm.status),
                safeString(alarm.severity),
                safeString(alarm.created_at_label)
            ].join(":");
        }).join("|");
    }

    function clampAssignments(input) {
        var next = [];
        var used = new Set();

        for (var idx = 0; idx < selectedGrid; idx += 1) {
            next.push(null);
        }

        if (!Array.isArray(input)) return next;

        for (var i = 0; i < Math.min(input.length, selectedGrid); i += 1) {
            var raw = input[i];

            if (raw === null || raw === undefined || raw === "") {
                continue;
            }

            var cameraId = String(raw);
            if (used.has(cameraId)) continue;
            if (cameraById && cameraById.size > 0 && !cameraById.has(cameraId)) continue;

            next[i] = cameraId;
            used.add(cameraId);
        }

        return next;
    }

    function persistAssignments() {
        try {
            localStorage.setItem(layoutKey, JSON.stringify(assignments));
            localStorage.setItem(layoutStateKey, JSON.stringify({
                grid: selectedGrid,
                assignments: assignments,
                updatedAt: Date.now()
            }));
            localStorage.setItem(gridMemoryKey, String(selectedGrid));
        } catch (error) {}
    }

    function updateLayoutKey() {
        layoutKey = "server_analiticos_vms_layout_v1:grid:" + selectedGrid;
    }

    function syncGridPresetButtons() {
        Array.prototype.forEach.call(gridButtons || [], function (button) {
            var isCurrent = Number(button.dataset.gridPreset || 0) === selectedGrid;
            button.classList.toggle("btn-primary", isCurrent);
            button.classList.toggle("btn-secondary", !isCurrent);
        });
    }

    function updateGridQueryParam() {
        try {
            var params = new URLSearchParams(window.location.search);
            params.set("grid", String(selectedGrid));
            var nextQuery = params.toString();
            var nextUrl = window.location.pathname + (nextQuery ?"?" + nextQuery : "") + window.location.hash;
            window.history.replaceState(window.history.state, "", nextUrl);
        } catch (error) {}
    }

    function switchGridInPlace(nextGrid, options) {
        var grid = Number(nextGrid);
        if (allowedGridValues.indexOf(grid) === -1) return false;

        var previousGrid = selectedGrid;
        var previousAssignments = Array.isArray(assignments) ?assignments.slice() : [];
        selectedGrid = grid;
        updateLayoutKey();

        try {
            localStorage.setItem(gridMemoryKey, String(selectedGrid));
        } catch (error) {}

        updateGridQueryParam();
        syncGridPresetButtons();

        if (options && options.loadStoredAssignments) {
            loadAssignments();
        } else if (options && Array.isArray(options.assignments)) {
            assignments = clampAssignments(options.assignments);
        } else {
            assignments = clampAssignments(previousAssignments);
        }

        return previousGrid !== selectedGrid;
    }

    function loadAssignments() {
        var loaded = null;

        try {
            var raw = localStorage.getItem(layoutKey);
            loaded = raw ?JSON.parse(raw) : null;
        } catch (error) {
            loaded = null;
        }

        if (!loaded) {
            try {
                var rawState = localStorage.getItem(layoutStateKey);
                var storedState = rawState ?JSON.parse(rawState) : null;
                if (storedState && Number(storedState.grid) === selectedGrid && Array.isArray(storedState.assignments)) {
                    loaded = storedState.assignments;
                }
            } catch (error) {
                loaded = null;
            }
        }

        assignments = clampAssignments(loaded, { keepUnknown: true });
        persistAssignments();
    }

    function ensureAssignmentsMatchAvailable() {
        assignments = clampAssignments(assignments, { keepUnknown: true });
    }

    function mergeVisibleCamerasIntoLibrary() {
        if (!Array.isArray(visibleCameras) || !visibleCameras.length) return;
        var nextById = new Map();
        (availableCameras || []).forEach(function (camera) {
            nextById.set(String(camera.id), camera);
        });
        visibleCameras.forEach(function (camera) {
            nextById.set(String(camera.id), camera);
        });
        availableCameras = Array.from(nextById.values());
    }

    function isLayoutEmpty() {
        for (var i = 0; i < assignments.length; i += 1) {
            if (assignments[i]) return false;
        }
        return true;
    }

    function cameraHasVisibleImage(camera) {
        var health = cameraOperationalHealth(camera);
        var image = health && health.image ?health.image : {};
        return safeString(image.status).toLowerCase() === "ok";
    }

    function visibleImageCameras() {
        return (availableCameras || []).filter(cameraHasVisibleImage);
    }

    function autoFillSource() {
        if (libraryFilter === "visible") {
            return visibleImageCameras();
        }
        return visibleCameras.length ?visibleCameras : availableCameras;
    }

    function autoFillAssignments() {
        stopAllSequences(true);
        if (layoutLocked) return;
        var source = autoFillSource();
        var ordered = source.map(function (camera) {
            return String(camera.id);
        });
        assignments = clampAssignments(ordered);
        persistAssignments();
        renderWall();
    }

    function autoFillVisibleAssignments() {
        stopAllSequences(true);
        if (layoutLocked) return;
        var ordered = visibleImageCameras().map(function (camera) {
            return String(camera.id);
        });
        assignments = clampAssignments(ordered);
        persistAssignments();
        renderWall();
    }

    function severityRank(value) {
        var normalized = safeString(value).toLowerCase();
        if (normalized === "critical" || normalized === "critico") return 5;
        if (normalized === "high" || normalized === "alto" || normalized === "alta") return 4;
        if (normalized === "medium" || normalized === "medio" || normalized === "media") return 3;
        if (normalized === "low" || normalized === "baixo" || normalized === "baixa") return 2;
        return 0;
    }

    function cameraPriorityScore(camera) {
        var score = 0;
        var severity = camera && (camera.highest_open_severity || camera.priority || camera.alarm_severity);
        score += severityRank(severity) * 100;

        if (camera && camera.has_open_alarm) score += 80;
        score += Math.min(50, Number(camera && (camera.open_events_count || camera.open_alarm_count || 0)) || 0);
        score += Math.min(25, Number(camera && (camera.new_events_count || camera.unread_alarm_count || 0)) || 0);

        var health = cameraOperationalHealth(camera);
        if (health && health.image !== "ok") score += 5;

        return score;
    }

    function autoFillAlarmAssignments() {
        stopAllSequences(true);
        if (layoutLocked) return;
        var source = autoFillSource();
        var ordered = source.slice().sort(function (left, right) {
            return cameraPriorityScore(right) - cameraPriorityScore(left);
        }).filter(function (camera) {
            return cameraPriorityScore(camera) > 0;
        });

        if (!ordered.length) {
            ordered = source.slice();
        }

        assignments = clampAssignments(ordered.map(function (camera) {
            return String(camera.id);
        }));
        persistAssignments();
        renderWall();
    }

    function clearAssignments() {
        stopAllSequences(true);
        if (layoutLocked) return;
        assignments = [];
        for (var i = 0; i < selectedGrid; i += 1) {
            assignments.push(null);
        }
        persistAssignments();
        renderWall();
    }

    function updateLayoutLockUi() {
        document.body.classList.toggle("vms-layout-locked", layoutLocked);

        if (layoutLockToggle) {
            layoutLockToggle.textContent = layoutLocked ?"Mosaico: Bloqueado" : "Mosaico: Destravado";
            layoutLockToggle.classList.toggle("btn-primary", layoutLocked);
            layoutLockToggle.classList.toggle("btn-secondary", !layoutLocked);
        }

        if (clearLayoutBtn) {
            clearLayoutBtn.disabled = layoutLocked;
            clearLayoutBtn.title = layoutLocked ?"Desbloqueie o layout para limpar o mosaico" : "Limpar mosaico";
        }

        if (autoFillBtn) autoFillBtn.disabled = layoutLocked;
        if (autoFillVisibleBtn) autoFillVisibleBtn.disabled = layoutLocked;
        if (autoFillAlarmBtn) autoFillAlarmBtn.disabled = layoutLocked;
        if (sequenceToggle) sequenceToggle.disabled = layoutLocked;
    }

    function loadLayoutLockPreference() {
        try {
            layoutLocked = localStorage.getItem(layoutLockedKey) === "1";
        } catch (error) {
            layoutLocked = false;
        }
        updateLayoutLockUi();
    }

    function setLayoutLocked(nextValue) {
        layoutLocked = !!nextValue;
        if (layoutLocked) {
            stopAllSequences(true);
        }
        try {
            localStorage.setItem(layoutLockedKey, layoutLocked ?"1" : "0");
        } catch (error) {}
        updateLayoutLockUi();
    }

    function sequenceCameraSource() {
        var source = autoFillSource();
        return (source || []).filter(function (camera) {
            return camera && camera.id !== null && camera.id !== undefined;
        });
    }

    function applySequencePage() {
        var source = sequenceCameraSource();
        if (!source.length) return;

        var page = [];
        for (var i = 0; i < selectedGrid; i += 1) {
            var camera = source[(sequenceCursor + i) % source.length];
            page.push(camera ?String(camera.id) : null);
        }

        sequenceCursor = (sequenceCursor + selectedGrid) % source.length;
        assignments = clampAssignments(page);
        persistAssignments();
        renderWall();
    }

    function updateSequenceUi() {
        var temporalActive = isTemporalSequenceActive();
        var anySequenceActive = sequenceEnabled || temporalActive;
        if (sequenceToggle) {
            sequenceToggle.textContent = anySequenceActive ?"Sequencial: Ligado" : "Sequencial: Desligado";
            sequenceToggle.classList.toggle("btn-primary", anySequenceActive);
            sequenceToggle.classList.toggle("btn-secondary", !anySequenceActive);
        }
        if (classicSeqPlay) classicSeqPlay.classList.toggle("active", anySequenceActive);
        if (classicSeqPause) classicSeqPause.classList.toggle("active", !anySequenceActive);
    }

    function scheduleSequencePage() {
        if (sequenceTimerId) {
            clearTimeout(sequenceTimerId);
            sequenceTimerId = null;
        }
        if (!sequenceEnabled || disposed || document.hidden) return;

        sequenceTimerId = setTimeout(function () {
            applySequencePage();
            scheduleSequencePage();
        }, sequenceIntervalMs);
    }

    function stopSequence(persist) {
        sequenceEnabled = false;
        if (sequenceTimerId) {
            clearTimeout(sequenceTimerId);
            sequenceTimerId = null;
        }
        if (persist) {
            try {
                localStorage.setItem(sequenceEnabledKey, "0");
            } catch (error) {}
        }
        updateSequenceUi();
    }

    function stopAllSequences(persist) {
        stopSequence(persist);
        stopTemporalSequence();
        updateSequenceUi();
    }

    function setSequenceEnabled(nextValue) {
        if (layoutLocked && nextValue) return;

        sequenceEnabled = !!nextValue;
        if (sequenceEnabled) {
            stopTemporalSequence();
        }
        try {
            localStorage.setItem(sequenceEnabledKey, sequenceEnabled ?"1" : "0");
        } catch (error) {}

        if (sequenceEnabled) {
            sequenceCursor = 0;
            applySequencePage();
            scheduleSequencePage();
        } else if (sequenceTimerId) {
            clearTimeout(sequenceTimerId);
            sequenceTimerId = null;
        }

        updateSequenceUi();
    }

    function loadSequencePreference() {
        try {
            sequenceEnabled = localStorage.getItem(sequenceEnabledKey) === "1";
        } catch (error) {
            sequenceEnabled = false;
        }
        updateSequenceUi();
    }

    function refreshCameraMap() {
        cameraById = new Map();
        (availableCameras || []).forEach(function (camera) {
            var cameraId = String(camera.id);
            cameraById.set(cameraId, camera);
            if (camera.ptz_profile) {
                ptzInspectionCache[cameraId] = camera.ptz_profile;
                if (selectedCameraId === cameraId && !selectedCameraPtzLoading) {
                    selectedCameraPtzInfo = camera.ptz_profile;
                }
            }
        });
    }

    function ptzCameraDetailUrl(camera) {
        if (!camera) return "";
        return camera.detail_url || ("/cameras/" + camera.id);
    }

    function currentSelectedCamera() {
        if (!selectedCameraId) return null;
        return cameraById.get(String(selectedCameraId)) || null;
    }

    function fallbackSelectedCameraId() {
        var i;

        for (i = 0; i < assignments.length; i += 1) {
            if (assignments[i] && cameraById.has(String(assignments[i]))) {
                return String(assignments[i]);
            }
        }

        for (i = 0; i < visibleCameras.length; i += 1) {
            if (visibleCameras[i] && visibleCameras[i].id !== undefined && visibleCameras[i].id !== null) {
                return String(visibleCameras[i].id);
            }
        }

        for (i = 0; i < availableCameras.length; i += 1) {
            if (availableCameras[i] && availableCameras[i].id !== undefined && availableCameras[i].id !== null) {
                return String(availableCameras[i].id);
            }
        }

        return "";
    }

    function selectedCameraSupportsPtz() {
        var info = selectedCameraPtzInfo || (selectedCameraId ?ptzInspectionCache[selectedCameraId] || null : null);
        if (!info) return false;
        if (info.ptz_capable !== undefined) return !!info.ptz_capable;
        return !!(info.capabilities && info.capabilities.ptz);
    }

    function ptzInfoNeedsInspection(info) {
        if (!info) return true;
        var status = safeString(info.status);
        return status === "" || status === "unknown" || status === "stale" || status === "probing";
    }

    function syncSelectedTileHighlight() {
        if (!wallEl) return;

        Array.prototype.forEach.call(wallEl.querySelectorAll(".vms-tile"), function (tile) {
            tile.classList.toggle("is-selected", safeString(tile.getAttribute("data-camera-id")) === selectedCameraId);
        });
    }

    function renderPtzPresetControls(camera, supportsPtz) {
        if (!ptzPresetSelectEl) return;

        var previousValue = safeString(ptzPresetSelectEl.value);
        var canUsePresets = !!camera && supportsPtz && canControlCamera;
        ptzPresetSelectEl.innerHTML = "";

        var placeholder = document.createElement("option");
        placeholder.value = "";
        if (!camera) {
            placeholder.textContent = "Selecione uma câmera PTZ";
        } else if (!supportsPtz) {
            placeholder.textContent = "PTZ indisponível";
        } else if (selectedCameraPtzPresetsLoading) {
            placeholder.textContent = "Atualizando presets...";
        } else if (!selectedCameraPtzPresets.length) {
            placeholder.textContent = "Nenhum preset encontrado";
        } else {
            placeholder.textContent = "Selecione um preset";
        }
        ptzPresetSelectEl.appendChild(placeholder);

        selectedCameraPtzPresets.forEach(function (preset) {
            var option = document.createElement("option");
            option.value = safeString(preset.token);
            option.textContent = safeString(preset.name) || ("Preset " + option.value);
            ptzPresetSelectEl.appendChild(option);
        });

        if (previousValue && selectedCameraPtzPresets.some(function (preset) {
            return safeString(preset.token) === previousValue;
        })) {
            ptzPresetSelectEl.value = previousValue;
        }

        ptzPresetSelectEl.disabled = !canUsePresets || selectedCameraPtzPresetsLoading || !selectedCameraPtzPresets.length;
        if (ptzPresetGoEl) {
            ptzPresetGoEl.disabled = ptzPresetSelectEl.disabled || !safeString(ptzPresetSelectEl.value);
        }
        if (ptzPresetRefreshEl) {
            ptzPresetRefreshEl.disabled = !canUsePresets || selectedCameraPtzPresetsLoading;
        }
        if (ptzPresetStatusEl) {
            var message = "Os presets serão atualizados ao conectar ao PTZ.";
            var stateClass = "";
            if (selectedCameraPtzPresetsLoading) {
                message = "Consultando presets existentes...";
            } else if (selectedCameraPtzPresetsError) {
                message = selectedCameraPtzPresetsError;
                stateClass = "is-error";
            } else if (selectedCameraPtzPresetsMessage) {
                message = selectedCameraPtzPresetsMessage;
                stateClass = "is-ok";
            } else if (canUsePresets && selectedCameraPtzPresetsCameraId === selectedCameraId) {
                message = selectedCameraPtzPresets.length
                    ?selectedCameraPtzPresets.length + " preset(s) encontrado(s)."
                    : "Nenhum preset existente foi retornado.";
            }
            ptzPresetStatusEl.textContent = message;
            ptzPresetStatusEl.classList.remove("is-error", "is-ok");
            if (stateClass) ptzPresetStatusEl.classList.add(stateClass);
        }
    }

    function renderPtzPanel() {
        if (!ptzPanelEl) return;

        var camera = currentSelectedCamera();
        var ptzInfo = selectedCameraPtzInfo || (selectedCameraId ?ptzInspectionCache[selectedCameraId] || null : null);
        var supportsPtz = !!(ptzInfo && (ptzInfo.ptz_capable !== undefined ?ptzInfo.ptz_capable : ptzInfo.capabilities && ptzInfo.capabilities.ptz));
        var statusText = "";
        var statusClass = "is-warn";
        var cameraUrl = camera ?ptzCameraDetailUrl(camera) : "";
        var metaText = "";
        var buttonDisabled = !camera || selectedCameraPtzLoading || !supportsPtz || !canControlCamera;

        if (!camera) {
            statusText = "Selecione uma camera no mosaico.";
            metaText = "Clique em um tile para controlar a camera selecionada.";
            statusClass = "is-warn";
        } else {
            metaText = [
                safeString(camera.site_name || "Sem local"),
                safeString(camera.group_name || "Sem grupo"),
                safeString(camera.camera_priority || "medium")
            ].join(" · ");

            if (selectedCameraPtzLoading) {
                statusText = "Detectando suporte PTZ nesta camera...";
                statusClass = "is-warn";
            } else if (selectedCameraPtzError) {
                statusText = selectedCameraPtzError;
                statusClass = "is-error";
            } else if (ptzInfo && ptzInfo.status === "probing") {
                statusText = ptzInfo.status_label || "Verificando PTZ...";
                statusClass = "is-warn";
            } else if (ptzInfo && (ptzInfo.status === "unknown" || ptzInfo.status === "stale")) {
                statusText = ptzInfo.status_label || "PTZ nao avaliado";
                statusClass = "is-warn";
            } else if (supportsPtz) {
                statusText = "PTZ disponivel";
                if (ptzInfo && (ptzInfo.backend_label || ptzInfo.backend)) {
                    statusText += " · " + safeString(ptzInfo.backend_label || ptzInfo.backend);
                }
                if (ptzInfo && ptzInfo.endpoint) {
                    statusText += " · " + safeString(ptzInfo.endpoint);
                }
                statusClass = "is-ok";
            } else if (ptzInfo && ptzInfo.status_label) {
                statusText = ptzInfo.status_label;
                statusClass = ptzInfo.status === "unavailable" ?"is-error" : "is-warn";
            } else {
                statusText = "PTZ nao avaliado.";
                statusClass = "is-warn";
            }
        }

        if (ptzCameraNameEl) {
            ptzCameraNameEl.textContent = camera ?safeString(camera.name) : "Selecione uma camera";
        }

        if (ptzCameraMetaEl) {
            ptzCameraMetaEl.textContent = metaText;
        }
        var ptzDiagnostics = ptzInfo && ptzInfo.diagnostics && typeof ptzInfo.diagnostics === "object"
            ?ptzInfo.diagnostics
            : null;
        if (ptzDiagnosticsWrapEl) {
            ptzDiagnosticsWrapEl.style.display = ptzDiagnostics && Object.keys(ptzDiagnostics).length ?"block" : "none";
        }
        if (ptzDiagnosticsEl) {
            ptzDiagnosticsEl.textContent = ptzDiagnostics ?JSON.stringify(ptzDiagnostics, null, 2) : "";
        }

        if (ptzOpenDetailEl) {
            if (cameraUrl) {
                ptzOpenDetailEl.href = cameraUrl;
                ptzOpenDetailEl.removeAttribute("aria-disabled");
                ptzOpenDetailEl.classList.remove("is-disabled");
            } else {
                ptzOpenDetailEl.href = "#";
                ptzOpenDetailEl.setAttribute("aria-disabled", "true");
                ptzOpenDetailEl.classList.add("is-disabled");
            }
        }
        if (ptzReinspectEl) {
            ptzReinspectEl.disabled = !camera || selectedCameraPtzLoading || !canControlCamera;
        }

        if (ptzPanelEl) {
            ptzPanelEl.classList.toggle("is-disabled", buttonDisabled);
        }

        if (ptzStatusEl) {
            ptzStatusEl.textContent = statusText;
            ptzStatusEl.classList.remove("is-warn", "is-error", "is-ok");
            ptzStatusEl.classList.add(statusClass);
        }

        var infoIconEl = document.getElementById("monitorPtzInfoIcon");
        if (infoIconEl) {
            if (camera && !selectedCameraPtzLoading && !supportsPtz) {
                var failReason = safeString(
                    (ptzInfo && (ptzInfo.reason || ptzInfo.last_error)) ||
                    selectedCameraPtzError ||
                    ""
                );
                var statusLabel = safeString(ptzInfo && ptzInfo.status_label) || "Sem capacidade PTZ";
                if (!failReason || failReason === statusLabel || failReason === "Sem capacidade PTZ") {
                    failReason = "• Teste ONVIF: Não expõe nó PTZConfiguration nos perfis.\n• Teste SDK Nativo: Dispositivo/NVR informou que este canal não possui motores PTZ.";
                }
                infoIconEl.style.display = "inline-flex";
                infoIconEl.title = "Diagnóstico de PTZ (" + statusLabel + "):\n" + failReason;
                infoIconEl.setAttribute("aria-label", failReason);
            } else {
                infoIconEl.style.display = "none";
                infoIconEl.title = "";
            }
        }

        if (ptzSpeedEl) {
            ptzSpeedEl.disabled = !camera || selectedCameraPtzLoading || !canControlCamera;
        }

        if (ptzSpeedValueEl && ptzSpeedEl) {
            ptzSpeedValueEl.textContent = Number(ptzSpeedEl.value || 0.5).toFixed(1);
        }

        if (ptzPanelEl) {
            Array.prototype.forEach.call(ptzPanelEl.querySelectorAll("[data-ptz-action]"), function (button) {
                button.disabled = buttonDisabled;
            });
        }
        if (buttonDisabled && ptz3dEnabled) setSelectedCameraPtz3dEnabled(false);
        renderPtzPresetControls(camera, supportsPtz);
    }

    function queueSelectedCameraPtzCommand(command) {
        selectedCameraPtzQueue = selectedCameraPtzQueue.catch(function () {
            return null;
        }).then(command);
        return selectedCameraPtzQueue;
    }

    async function postSelectedCameraPtzCommand(url, payload) {
        var response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload || {})
        });

        var data = {};
        try {
            data = await response.json();
        } catch (error) {
            data = {};
        }

        if (!response.ok || data.ok === false) {
            throw new Error(safeString(data.error) || "Falha ao enviar comando PTZ.");
        }

        return data;
    }

    function setSelectedCameraPtz3dEnabled(enabled) {
        Array.prototype.forEach.call(document.querySelectorAll(".vms-ptz-3d-overlay"), function (overlay) {
            if (overlay._ptz3dCleanup) overlay._ptz3dCleanup();
            overlay.remove();
        });
        ptz3dEnabled = false;
        var controls = ptzPanelEl ?ptzPanelEl.querySelectorAll('[data-ptz-action="3d"]') : [];
        Array.prototype.forEach.call(controls, function (button) {
            button.classList.remove("is-active");
            button.setAttribute("aria-pressed", "false");
            if (button.id === "monitorPtz3dJoystick") button.textContent = "Ativar 3D";
            if (button.classList.contains("vms-ptz-stop")) button.title = "Ativar posicionamento 3D";
        });
        if (!enabled || !selectedCameraId || !selectedCameraSupportsPtz() || !canControlCamera) return;

        var tile = wallEl && wallEl.querySelector('.vms-tile[data-camera-id="' + String(selectedCameraId) + '"]');
        var videoWrap = tile && (tile.querySelector(".vms-video-wrap") || tile.querySelector(".vms-pure-webrtc-frame-wrap"));
        if (!videoWrap) return;
        var overlay = document.createElement("div");
        var selection = document.createElement("div");
        var status = document.createElement("div");
        overlay.className = "vms-ptz-3d-overlay";
        selection.className = "vms-ptz-3d-selection";
        selection.hidden = true;
        status.className = "vms-ptz-3d-status";
        status.textContent = "PTZ 3D ATIVO · clique ou arraste na imagem";
        overlay.appendChild(selection); overlay.appendChild(status);
        videoWrap.appendChild(overlay);
        ptz3dEnabled = true;

        // O video roda em iframe WebRTC de outra origem, entao o JS nao consegue
        // ler a resolucao real do frame. Sem isso, o overlay cobria o wrap
        // inteiro (inset:0) e, quando a proporcao do stream nao batia com a do
        // tile, incluia a barra preta do letterbox no calculo do clique, o que
        // deslocava o ponto/area enviados ao PTZ 3D. Medimos no servidor a
        // proporcao principal da camera (mesmo quando o player usa substream) e
        // encolhemos o proprio overlay para cobrir a geometria de referencia.
        var streamAspect = 0;
        function syncOverlayBounds() {
            var wrapRect = videoWrap.getBoundingClientRect();
            if (!wrapRect.width || !wrapRect.height) return;
            if (streamAspect > 0) {
                var wrapAspect = wrapRect.width / wrapRect.height;
                var contentWidth = wrapRect.width, contentHeight = wrapRect.height, left = 0, top = 0;
                if (wrapAspect > streamAspect) {
                    contentWidth = wrapRect.height * streamAspect;
                    left = (wrapRect.width - contentWidth) / 2;
                } else if (wrapAspect < streamAspect) {
                    contentHeight = wrapRect.width / streamAspect;
                    top = (wrapRect.height - contentHeight) / 2;
                }
                overlay.style.left = left + "px";
                overlay.style.top = top + "px";
                overlay.style.width = contentWidth + "px";
                overlay.style.height = contentHeight + "px";
            } else {
                overlay.style.left = "0px";
                overlay.style.top = "0px";
                overlay.style.width = "100%";
                overlay.style.height = "100%";
            }
        }
        syncOverlayBounds();
        window.addEventListener("resize", syncOverlayBounds);
        var resizeObserver = window.ResizeObserver ?new ResizeObserver(syncOverlayBounds) : null;
        if (resizeObserver) resizeObserver.observe(videoWrap);
        overlay._ptz3dCleanup = function () {
            window.removeEventListener("resize", syncOverlayBounds);
            if (resizeObserver) resizeObserver.disconnect();
        };
        fetch("/monitor/cameras/" + encodeURIComponent(selectedCameraId) + "/ptz/3d/dimensoes")
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (data && data.ok && data.width > 0 && data.height > 0) {
                    streamAspect = data.width / data.height;
                    syncOverlayBounds();
                }
            })
            .catch(function () {});

        Array.prototype.forEach.call(controls, function (button) {
            button.classList.add("is-active");
            button.setAttribute("aria-pressed", "true");
            if (button.id === "monitorPtz3dJoystick") button.textContent = "3D: ATIVO";
            if (button.classList.contains("vms-ptz-stop")) button.title = "3D ativo — clique para desativar";
        });
        if (ptzStatusEl) ptzStatusEl.textContent = "3D ativo: clique para centralizar ou arraste uma area para enquadrar.";

        var drag = null;
        function point(event) {
            var rect = overlay.getBoundingClientRect();
            return { x: Math.max(0, Math.min(rect.width, event.clientX - rect.left)), y: Math.max(0, Math.min(rect.height, event.clientY - rect.top)), width: rect.width, height: rect.height };
        }
        function draw(end) {
            if (!drag) return;
            selection.hidden = false;
            selection.style.left = Math.min(drag.x, end.x) + "px";
            selection.style.top = Math.min(drag.y, end.y) + "px";
            selection.style.width = Math.abs(end.x - drag.x) + "px";
            selection.style.height = Math.abs(end.y - drag.y) + "px";
            selection.classList.toggle("zoom-out", end.x < drag.x);
        }
        overlay.addEventListener("pointerdown", function (event) {
            if (event.button !== 0) return;
            event.preventDefault(); event.stopPropagation();
            drag = point(event); overlay.setPointerCapture(event.pointerId); draw(drag);
        });
        overlay.addEventListener("pointermove", function (event) { if (drag) draw(point(event)); });
        overlay.addEventListener("pointerup", function (event) {
            if (!drag) return;
            event.preventDefault(); event.stopPropagation();
            var start = drag, end = point(event); drag = null; selection.hidden = true;
            var click = Math.hypot(end.x - start.x, end.y - start.y) < 6;
            if (click) end = start;
            function normalized(value, size) { return Math.max(0, Math.min(255, Math.round(value / Math.max(1, size) * 255))); }
            queueSelectedCameraPtzCommand(function () {
                return postSelectedCameraPtzCommand("/monitor/cameras/" + encodeURIComponent(selectedCameraId) + "/ptz/3d", {
                    x_start: normalized(start.x, start.width), y_start: normalized(start.y, start.height),
                    x_end: normalized(end.x, end.width), y_end: normalized(end.y, end.height)
                });
            }).then(function () {
                if (ptzStatusEl) ptzStatusEl.textContent = click ?"Ponto centralizado pelo PTZ 3D." : "Enquadramento 3D enviado.";
            }).catch(function (error) {
                if (ptzStatusEl) ptzStatusEl.textContent = error.message || "Falha no PTZ 3D.";
            });
        });
        overlay.addEventListener("pointercancel", function () { drag = null; selection.hidden = true; });
        overlay.addEventListener("click", function (event) { event.preventDefault(); event.stopPropagation(); });
    }

    async function refreshSelectedCameraPtzPresets(cameraId) {
        var normalizedId = safeString(cameraId || selectedCameraId);
        if (!normalizedId || normalizedId !== selectedCameraId || !selectedCameraSupportsPtz()) {
            return null;
        }

        var requestId = normalizedId + ":" + Date.now();
        selectedCameraPtzPresetsRequestId = requestId;
        selectedCameraPtzPresetsLoading = true;
        selectedCameraPtzPresetsError = "";
        selectedCameraPtzPresetsMessage = "";
        renderPtzPanel();

        try {
            var response = await fetch(
                "/monitor/cameras/" + encodeURIComponent(normalizedId) + "/ptz/presets?_ts=" + Date.now(),
                { cache: "no-store", headers: { "Cache-Control": "no-cache" } }
            );
            var data = {};
            try {
                data = await response.json();
            } catch (error) {
                data = {};
            }
            if (selectedCameraPtzPresetsRequestId !== requestId || selectedCameraId !== normalizedId) {
                return null;
            }
            if (!response.ok || data.ok === false) {
                throw new Error(safeString(data.error) || "Falha ao consultar presets.");
            }
            selectedCameraPtzPresets = (Array.isArray(data.presets) ?data.presets : []).filter(function (preset) {
                return safeString(preset && preset.token);
            });
            selectedCameraPtzPresetsCameraId = normalizedId;
            selectedCameraPtzPresetsError = "";
            return selectedCameraPtzPresets;
        } catch (error) {
            if (selectedCameraPtzPresetsRequestId === requestId) {
                selectedCameraPtzPresets = [];
                selectedCameraPtzPresetsCameraId = normalizedId;
                selectedCameraPtzPresetsError = safeString(error && error.message) || "Falha ao consultar presets.";
            }
            return null;
        } finally {
            if (selectedCameraPtzPresetsRequestId === requestId) {
                selectedCameraPtzPresetsLoading = false;
                selectedCameraPtzPresetsRequestId = "";
                renderPtzPanel();
            }
        }
    }

    async function gotoSelectedCameraPtzPreset() {
        var cameraId = selectedCameraId;
        var presetToken = safeString(ptzPresetSelectEl && ptzPresetSelectEl.value);
        if (!cameraId || !presetToken || !selectedCameraSupportsPtz()) return;

        if (ptzPresetGoEl) ptzPresetGoEl.disabled = true;
        selectedCameraPtzPresetsError = "";
        selectedCameraPtzPresetsMessage = "";
        try {
            await postSelectedCameraPtzCommand(
                "/monitor/cameras/" + encodeURIComponent(cameraId) + "/ptz/presets/goto",
                { preset_token: presetToken }
            );
            if (selectedCameraId === cameraId) {
                var selectedOption = ptzPresetSelectEl.options[ptzPresetSelectEl.selectedIndex];
                var presetLabel = safeString(selectedOption && selectedOption.textContent) || presetToken;
                selectedCameraPtzPresetsMessage = "Comando enviado para “" + presetLabel + "”.";
            }
        } catch (error) {
            selectedCameraPtzPresetsError = safeString(error && error.message) || "Falha ao acionar preset.";
        } finally {
            renderPtzPanel();
        }
    }

    async function inspectSelectedCameraPtz(cameraId, force) {
        var normalizedId = safeString(cameraId);
        if (!normalizedId || !canControlCamera || !cameraById.has(normalizedId)) {
            return null;
        }

        var cachedInfo = ptzInspectionCache[normalizedId] || null;
        var cachedStatus = safeString(cachedInfo && cachedInfo.status);
        var retryAtMs = Date.parse(safeString(cachedInfo && cachedInfo.next_retry_at));
        var retryDue = cachedStatus === "unavailable" && Number.isFinite(retryAtMs) && retryAtMs <= Date.now();
        if (!force && !retryDue && cachedInfo && ["controllable", "not_controllable", "unavailable"].indexOf(cachedStatus) >= 0) {
            selectedCameraPtzInfo = cachedInfo;
            selectedCameraPtzError = "";
            selectedCameraPtzLoading = false;
            selectedCameraPtzRequestId = "";
            renderPtzPanel();
            if (selectedCameraPtzPresetsCameraId !== normalizedId) {
                refreshSelectedCameraPtzPresets(normalizedId);
            }
            return selectedCameraPtzInfo;
        }

        var requestId = normalizedId + ":" + Date.now();
        selectedCameraPtzRequestId = requestId;
        selectedCameraPtzLoading = true;
        selectedCameraPtzError = "";
        renderPtzPanel();

        try {
            var inspectUrl = "/monitor/cameras/" + encodeURIComponent(normalizedId) + "/ptz/inspect?_ts=" + Date.now();
            if (force) inspectUrl += "&force=true";
            var response = await fetch(inspectUrl, {
                cache: "no-store",
                headers: { "Cache-Control": "no-cache" }
            });
            var data = {};
            try {
                data = await response.json();
            } catch (error) {
                data = {};
            }

            if (selectedCameraPtzRequestId !== requestId) {
                return null;
            }

            if (!response.ok || data.ok === false) {
                throw new Error(safeString(data.error) || "Falha ao inspecionar suporte PTZ.");
            }

            ptzInspectionCache[normalizedId] = data;
            if (selectedCameraId === normalizedId) {
                selectedCameraPtzInfo = data;
            }
            selectedCameraPtzError = "";
            updateTilePtzBadge(normalizedId);
            if (data.status === "probing" && selectedCameraId === normalizedId) {
                setTimeout(function () {
                    if (selectedCameraId === normalizedId && !selectedCameraPtzLoading) {
                        inspectSelectedCameraPtz(normalizedId, false);
                    }
                }, 750);
            } else if (selectedCameraId === normalizedId && selectedCameraSupportsPtz()) {
                refreshSelectedCameraPtzPresets(normalizedId);
            }
            return data;
        } catch (error) {
            if (selectedCameraPtzRequestId === requestId) {
                var errorMessage = safeString(error && error.message) || "Falha ao inspecionar suporte PTZ.";
                var errorInfo = {
                    ok: false,
                    ptz_capable: false,
                    capabilities: { ptz: false },
                    error: errorMessage
                };
                ptzInspectionCache[normalizedId] = errorInfo;
                selectedCameraPtzInfo = errorInfo;
                selectedCameraPtzError = errorMessage;
                updateTilePtzBadge(normalizedId);
            }
            return null;
        } finally {
            if (selectedCameraPtzRequestId === requestId) {
                selectedCameraPtzLoading = false;
                selectedCameraPtzRequestId = "";
                renderPtzPanel();
                updateTilePtzBadge(normalizedId);
            }
        }
    }

    function setSelectedCamera(cameraId, options) {
        var normalizedId = safeString(cameraId);
        if (normalizedId && !cameraById.has(normalizedId)) {
            normalizedId = "";
        }

        if (!normalizedId) {
            selectedCameraId = "";
            selectedCameraPtzInfo = null;
            selectedCameraPtzLoading = false;
            selectedCameraPtzError = "";
            selectedCameraPtzRequestId = "";
            selectedCameraPtzMoveState = null;
            selectedCameraPtzPresets = [];
            selectedCameraPtzPresetsCameraId = "";
            selectedCameraPtzPresetsLoading = false;
            selectedCameraPtzPresetsError = "";
            selectedCameraPtzPresetsMessage = "";
            selectedCameraPtzPresetsRequestId = "";
            syncSelectedTileHighlight();
            renderPtzPanel();
            return;
        }

        var changed = normalizedId !== selectedCameraId;
        selectedCameraId = normalizedId;
        if (changed || !selectedCameraPtzInfo) {
            selectedCameraPtzInfo = ptzInspectionCache[normalizedId] || null;
            selectedCameraPtzError = "";
        }
        if (changed) {
            setSelectedCameraPtz3dEnabled(false);
            selectedCameraPtzPresets = [];
            selectedCameraPtzPresetsCameraId = "";
            selectedCameraPtzPresetsLoading = false;
            selectedCameraPtzPresetsError = "";
            selectedCameraPtzPresetsMessage = "";
            selectedCameraPtzPresetsRequestId = "";
        }
        syncSelectedTileHighlight();
        renderPtzPanel();


        if (options && options.fetch === false) {
            return;
        }

        if (ptzInfoNeedsInspection(selectedCameraPtzInfo) && !selectedCameraPtzLoading) {
            inspectSelectedCameraPtz(normalizedId, !!(options && options.forceInspect));
        } else if (changed && selectedCameraSupportsPtz()) {
            refreshSelectedCameraPtzPresets(normalizedId);
        }
    }

    function ensureSelectedCameraSelection() {
        var fallbackId = selectedCameraId && cameraById.has(selectedCameraId) ?selectedCameraId : fallbackSelectedCameraId();
        if (!fallbackId) {
            setSelectedCamera("", { fetch: false });
            return;
        }
        if (fallbackId !== selectedCameraId) {
            setSelectedCamera(fallbackId, { fetch: true });
            return;
        }
        if (ptzInfoNeedsInspection(selectedCameraPtzInfo) && !selectedCameraPtzLoading) {
            renderPtzPanel();
            inspectSelectedCameraPtz(fallbackId, false);
            return;
        }
        syncSelectedTileHighlight();
        renderPtzPanel();
    }

    function ptzMoveSpeed() {
        var value = Number(ptzSpeedEl && ptzSpeedEl.value);
        if (!Number.isFinite(value)) return 0.5;
        return Math.max(0.1, Math.min(1, value));
    }

    function stopSelectedCameraPtzMove(force) {
        var cameraId = selectedCameraPtzMoveState && selectedCameraPtzMoveState.cameraId ?selectedCameraPtzMoveState.cameraId : selectedCameraId;
        if (!cameraId || !canControlCamera || (!selectedCameraPtzMoveState && !force)) {
            return selectedCameraPtzQueue;
        }

        selectedCameraPtzMoveState = null;
        return queueSelectedCameraPtzCommand(function () {
            return postSelectedCameraPtzCommand("/monitor/cameras/" + encodeURIComponent(cameraId) + "/ptz/stop", {});
        }).catch(function (error) {
            console.warn("Falha ao parar PTZ", error);
            // Stop e best-effort: uma camera ONVIF offline nao pode propagar
            // rejeicao para selecao de tile, spotlight ou reproducao de video.
            return { ok: false, error: safeString(error && error.message) || "ptz_stop_failed" };
        });
    }

    function startSelectedCameraPtzMove(pan, tilt, zoom, isRepeat) {
        var cameraId = selectedCameraId;
        if (!cameraId || !canControlCamera || !selectedCameraSupportsPtz()) {
            return selectedCameraPtzQueue;
        }

        if (!isRepeat && selectedCameraPtzMoveState && selectedCameraPtzMoveState.cameraId === cameraId) {
            return selectedCameraPtzQueue;
        }

        var velocity = ptzMoveSpeed();
        selectedCameraPtzMoveState = { cameraId: cameraId };

        return queueSelectedCameraPtzCommand(function () {
            return postSelectedCameraPtzCommand(
                "/monitor/cameras/" + encodeURIComponent(cameraId) + "/ptz/move",
                {
                    pan: Number(pan || 0) * velocity,
                    tilt: Number(tilt || 0) * velocity,
                    zoom: Number(zoom || 0) * velocity
                }
            );
        }).catch(function (error) {
            console.warn("Falha ao mover PTZ", error);
            throw error;
        });
    }

    function statusBadge(type, value) {
        if (!value) return "";
        return '<span class="status ' + type + '-' + escapeHtml(value) + '">' + escapeHtml(value) + '</span>';
    }

    function ptzTileBadge(camera) {
        var profile = camera && camera.ptz_profile ?camera.ptz_profile : null;
        var status = safeString(profile && profile.status) || "unknown";
        var label = safeString(profile && profile.status_label) || "PTZ nao avaliado";
        var reason = safeString(profile && (profile.reason || profile.last_error)) || label;
        var shortLabel = status === "controllable"
            ?"PTZ"
            : (status === "probing" ?"PTZ..." : (status === "not_controllable" ?"Sem PTZ" : "PTZ ?"));
        var tooltip = status === "controllable" ?label : (label + " — Motivo: " + reason);
        var infoDot = (status !== "controllable" && status !== "probing") ?' <i style="font-style:normal; font-size:11px; margin-left:2px; opacity:0.9;" title="' + escapeHtml(tooltip) + '">ℹ</i>' : '';
        return '<span class="vms-ptz-badge is-' + escapeHtml(status) + '" title="' + escapeHtml(tooltip) + '">' + escapeHtml(shortLabel) + infoDot + '</span>';
    }

    function updateTilePtzBadge(cameraId) {
        var idStr = safeString(cameraId);
        if (!idStr) return;
        var camera = cameraById.get(idStr);
        if (camera && ptzInspectionCache[idStr]) {
            camera.ptz_profile = ptzInspectionCache[idStr];
        }
        if (!wallEl) return;
        var tile = wallEl.querySelector('.vms-tile[data-camera-id="' + idStr.replace(/"/g, '\\"') + '"]');
        if (!tile) return;
        var badgeEl = tile.querySelector('.vms-ptz-badge');
        if (!badgeEl) return;
        var newBadgeHtml = ptzTileBadge(camera || { ptz_profile: ptzInspectionCache[idStr] });
        var tempWrap = document.createElement("div");
        tempWrap.innerHTML = newBadgeHtml;
        var newBadgeNode = tempWrap.firstElementChild;
        if (newBadgeNode) {
            badgeEl.replaceWith(newBadgeNode);
        }
    }

    function cameraHealthStatus(camera) {
        var health = safeString(camera && camera.health_status_display).toLowerCase() || safeString(camera && camera.health_status).toLowerCase();
        if (health) return health;
        var raw = safeString(camera && camera.status).toLowerCase();
        return raw || "idle";
    }

    function cameraFpsValue(camera) {
        var value = Number(camera && (camera.display_fps ?? camera.processed_fps ?? camera.raw_fps ?? camera.fps ?? 0));
        if (!Number.isFinite(value) || value < 0) return "0.00";
        return value.toFixed(2);
    }

    function cameraStreamAspectRatio(camera) {
        var width = Number(camera && camera.source_frame_width);
        var height = Number(camera && camera.source_frame_height);
        if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
            return null;
        }

        return String(width) + " / " + String(height);
    }

    function cameraDisplayAspectRatioValue(camera) {
        var width = Number(camera && (camera.player_frame_width || camera.source_frame_width));
        var height = Number(camera && (camera.player_frame_height || camera.source_frame_height));
        if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
            return 16 / 9;
        }
        return width / height;
    }

    function syncTileVisualLayers(tile, camera) {
        if (!tile) return;

        var videoWrapEl = tile.querySelector(".vms-video-wrap") || tile.querySelector(".vms-pure-webrtc-frame-wrap");
        if (!videoWrapEl) return;

        var overlayLayerEl = tile.querySelector(".vms-overlay-layer");
        var boxLayerEl = tile.querySelector(".vms-box-layer");
        var layers = [overlayLayerEl, boxLayerEl].filter(Boolean);
        if (!layers.length) return;

        var containerWidth = videoWrapEl.clientWidth;
        var containerHeight = videoWrapEl.clientHeight;
        if (containerWidth <= 0 || containerHeight <= 0) return;

        var aspectRatio = cameraDisplayAspectRatioValue(camera);
        var useCover = tile.classList.contains("vms-local-fit-cover")
            || (!tile.classList.contains("vms-local-fit-contain") && videoFitMode === "fill");
        var scale = useCover
            ?Math.max(containerWidth / aspectRatio, containerHeight)
            : Math.min(containerWidth / aspectRatio, containerHeight);
        var renderedWidth = Math.max(1, aspectRatio * scale);
        var renderedHeight = Math.max(1, scale);
        var left = (containerWidth - renderedWidth) / 2;
        var top = (containerHeight - renderedHeight) / 2;

        layers.forEach(function (layer) {
            layer.style.inset = "auto";
            layer.style.left = left.toFixed(2) + "px";
            layer.style.top = top.toFixed(2) + "px";
            layer.style.width = renderedWidth.toFixed(2) + "px";
            layer.style.height = renderedHeight.toFixed(2) + "px";
        });
    }

    function syncWallVisualLayers() {
        if (!wallEl) return;
        var tiles = wallEl.querySelectorAll(".vms-tile");
        Array.prototype.forEach.call(tiles, function (tile) {
            var camera = cameraById.get(String(tile.getAttribute("data-camera-id")));
            if (camera) syncTileVisualLayers(tile, camera);
        });
    }

    function scheduleWallVisualLayerSync() {
        window.requestAnimationFrame(function () {
            syncWallVisualLayers();
        });
    }

    function formatDurationLabel(seconds) {
        var value = Number(seconds);
        if (!Number.isFinite(value) || value < 0) return "";
        if (value < 60) {
            return value.toFixed(1).replace(/\.0$/, "") + "s";
        }

        var totalSeconds = Math.floor(value);
        var hours = Math.floor(totalSeconds / 3600);
        var minutes = Math.floor((totalSeconds % 3600) / 60);
        var secs = totalSeconds % 60;

        if (hours > 0) {
            return String(hours) + "h " + String(minutes).padStart(2, "0") + "m";
        }

        return String(minutes) + "m " + String(secs).padStart(2, "0") + "s";
    }

    function formatTimeLabel(isoValue) {
        if (!isoValue) return "";

        try {
            var date = new Date(isoValue);
            if (!date || Number.isNaN(date.getTime())) return "";
            return date.toLocaleTimeString("pt-BR", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false
            });
        } catch (error) {
            return "";
        }
    }

    function cameraGatewayMeta(camera) {
        if (!camera) return "";

        var parts = [];
        var state = safeString(camera.gateway_state).toLowerCase();
        if (state) {
            parts.push("GW " + state);
        }

        var frameAge = Number(camera.gateway_last_frame_age_seconds);
        if (Number.isFinite(frameAge) && frameAge >= 0) {
            parts.push("frame " + formatDurationLabel(frameAge));
        }

        var reconnectLabel = formatTimeLabel(camera.gateway_last_reconnect_at);
        if (reconnectLabel) {
            parts.push("recon " + reconnectLabel);
        }

        var failures = Number(camera.gateway_failure_count);
        if (Number.isFinite(failures)) {
            parts.push("falhas " + String(Math.max(0, failures)));
        }

        if (camera.gateway_source_active) {
            parts.push("origem ativa");
        }

        return parts.join(" · ");
    }

    function cameraHealthLabel(camera) {
        var status = cameraHealthStatus(camera);
        if (status === "running_motion_test") return "running";
        return status || "idle";
    }

    function cameraOperationalHealth(camera) {
        return (camera && camera.operational_health && typeof camera.operational_health === "object")
            ?camera.operational_health
            : {};
    }

    function cameraCanRenderBoxes(camera) {
        return cameraHasVisibleImage(camera);
    }

    function cameraConnectionUnavailable(camera) {
        if (!camera) return true;
        if (!cameraCanRenderBoxes(camera)) return true;
        var health = cameraHealthStatus(camera);
        if (["offline", "reconnecting", "stopped", "down", "error"].indexOf(health) >= 0) return true;
        var operational = cameraOperationalHealth(camera);
        var image = safeString(operational.image && operational.image.status).toLowerCase();
        var player = safeString(operational.player && operational.player.status).toLowerCase();
        return [image, player].some(function (status) {
            return ["offline", "reconnecting", "down", "error", "stale"].indexOf(status) >= 0;
        });
    }

    function operationalChip(item, fallbackLabel) {
        item = item && typeof item === "object" ?item : {};
        var status = safeString(item.status || "idle").toLowerCase() || "idle";
        var label = safeString(item.label || fallbackLabel || status);
        var detail = safeString(item.detail || "");
        var symbol = "?";
        var kind = safeString(fallbackLabel).toLowerCase();
        if (kind.indexOf("imagem") >= 0) {
            symbol = "IMG";
        } else if (kind.indexOf("ia") >= 0) {
            symbol = "IA";
        } else if (kind.indexOf("player") >= 0) {
            symbol = "P";
        }

        var title = label + (detail ?" - " + detail : "");
        return '<span class="vms-op-chip state-' + escapeHtml(status) + '" title="' + escapeHtml(title) + '">'
            + '<span class="vms-op-symbol" aria-hidden="true">' + escapeHtml(symbol) + '</span>'
            + '<span class="vms-op-text">' + escapeHtml(label) + '</span>'
            + '</span>';
    }

    function cameraOperationalChips(camera) {
        var health = cameraOperationalHealth(camera);
        return '<div class="vms-health-stack">'
            + operationalChip(health.image, "Imagem")
            + operationalChip(health.analysis, "IA")
            + operationalChip(health.player, "Player")
            + '</div>';
    }

    function cameraModeValue(camera) {
        var workerMode = safeString(camera && camera.worker_mode).toLowerCase();
        if (workerMode) return workerMode;

        var health = safeString(camera && (camera.health_status_display || camera.health_status || camera.status)).toLowerCase();
        if (health === "running_motion_test") return "motion_test";
        if (health.indexOf("running") === 0 || camera && camera.is_running) return "normal";
        return health || "stopped";
    }

    function restoreGridPreference() {
        try {
            var params = new URLSearchParams(window.location.search);
            var allowed = allowedGridValues.map(String);

            if (params.has("grid")) {
                localStorage.setItem(gridMemoryKey, String(selectedGrid));
                return false;
            }

            var storedGrid = localStorage.getItem(gridMemoryKey) || "";
            if (!storedGrid) {
                try {
                    var storedState = JSON.parse(localStorage.getItem(layoutStateKey) || "{}");
                    if (storedState && storedState.grid) {
                        storedGrid = String(storedState.grid);
                    }
                } catch (error) {
                    storedGrid = "";
                }
            }
            if (allowed.indexOf(storedGrid) >= 0 && Number(storedGrid) !== selectedGrid) {
                params.set("grid", storedGrid);
                var nextQuery = params.toString();
                window.location.replace(window.location.pathname + (nextQuery ?"?" + nextQuery : ""));
                return true;
            }
        } catch (error) {}

        return false;
    }

    function loadTileDetailsPreference() {
        var stored = "";

        try {
            stored = localStorage.getItem(tileDetailsKey) || "";
        } catch (error) {
            stored = "";
        }

        tileDetailsEnabled = stored === "show";
        applyTileDetailsPreference();
    }

    function persistTileDetailsPreference() {
        try {
            localStorage.setItem(tileDetailsKey, tileDetailsEnabled ?"show" : "hide");
        } catch (error) {}
    }

    function applyTileDetailsPreference() {
        document.body.classList.toggle("vms-show-details", !!tileDetailsEnabled);

        if (tileDetailsToggle) {
            tileDetailsToggle.textContent = tileDetailsEnabled ?"Detalhes: Visíveis" : "Detalhes: Ocultos";
            tileDetailsToggle.classList.toggle("btn-primary", !!tileDetailsEnabled);
            tileDetailsToggle.classList.toggle("btn-secondary", !tileDetailsEnabled);
        }
    }

    function setTileDetailsEnabled(nextValue) {
        tileDetailsEnabled = !!nextValue;
        persistTileDetailsPreference();
        applyTileDetailsPreference();
    }

    function loadTileHeadersPreference() {
        var stored = "";
        try {
            stored = localStorage.getItem(tileHeadersFixedKey) || "";
        } catch (error) {
            stored = "";
        }
        tileHeadersFixed = stored === "fixed";
        applyTileHeadersPreference();
    }

    function persistTileHeadersPreference() {
        try {
            localStorage.setItem(tileHeadersFixedKey, tileHeadersFixed ?"fixed" : "retractable");
        } catch (error) {}
    }

    function applyTileHeadersPreference() {
        document.body.classList.toggle("vms-fixed-headers", !!tileHeadersFixed);
        document.body.classList.toggle("vms-retractable-headers", !tileHeadersFixed);

        if (tileHeadersToggle) {
            tileHeadersToggle.textContent = tileHeadersFixed ?"Nomes: Fixos" : "Nomes: Ocultos (Hover)";
            tileHeadersToggle.classList.toggle("btn-primary", !!tileHeadersFixed);
            tileHeadersToggle.classList.toggle("btn-secondary", !tileHeadersFixed);
        }
    }

    function setTileHeadersFixed(nextValue) {
        tileHeadersFixed = !!nextValue;
        persistTileHeadersPreference();
        applyTileHeadersPreference();
    }

    function loadWebrtcPurePreference() {
        if (!monitorDevMode) {
            webrtcPureMode = false;
            persistWebrtcPurePreference();
            applyWebrtcPurePreference();
            return;
        }

        var stored = "";
        try {
            stored = localStorage.getItem(webrtcPureKey) || "";
        } catch (error) {
            stored = "";
        }
        webrtcPureMode = stored === "1";
        applyWebrtcPurePreference();
    }

    function persistWebrtcPurePreference() {
        try {
            localStorage.setItem(webrtcPureKey, webrtcPureMode ?"1" : "0");
        } catch (error) {}
    }

    function applyWebrtcPurePreference() {
        document.body.classList.toggle("vms-webrtc-pure-mode", !!webrtcPureMode);

        if (webrtcPureToggle) {
            webrtcPureToggle.textContent = webrtcPureMode ?"WebRTC: Direto" : "WebRTC: Híbrido";
            webrtcPureToggle.classList.toggle("btn-primary", !!webrtcPureMode);
            webrtcPureToggle.classList.toggle("btn-secondary", !webrtcPureMode);
        }

        if (webrtcPureMode) {
            if (webrtcDiagnosticsTimerId) {
                clearInterval(webrtcDiagnosticsTimerId);
                webrtcDiagnosticsTimerId = null;
            }
            if (snapshotFallbackTimerId) {
                clearInterval(snapshotFallbackTimerId);
                snapshotFallbackTimerId = null;
            }
            if (wallEl) {
                Array.prototype.forEach.call(wallEl.querySelectorAll(".vms-tile.vms-using-snapshot-fallback"), function (tile) {
                    tile.classList.remove("vms-using-snapshot-fallback");
                });
            }
            updateSnapshotFallbackNotice();
            pollTrackBoxes();
        } else {
            pollTrackBoxes();
            pollWebrtcDiagnostics();
            if (!disposed && !document.hidden) {
                if (!tracksPollTimerId) {
                    tracksPollTimerId = setInterval(function () {
                        pollTrackBoxes();
                    }, tracksPollIntervalMs);
                }
                if (!webrtcDiagnosticsTimerId) {
                    webrtcDiagnosticsTimerId = setInterval(function () {
                        pollWebrtcDiagnostics();
                    }, webrtcDiagnosticsPollIntervalMs);
                }
                if (!snapshotFallbackTimerId) {
                    snapshotFallbackTimerId = setInterval(function () {
                        applyDefaultVideoHelperFallback();
                        refreshSnapshotFallbacks();
                    }, snapshotFallbackIntervalMs);
                }
            }
        }
    }

    function setWebrtcPureMode(nextValue) {
        if (!monitorDevMode) {
            nextValue = false;
        }
        webrtcPureMode = !!nextValue;
        persistWebrtcPurePreference();
        applyWebrtcPurePreference();
        renderWall();
    }

    function loadOverlayPreference() {
        var stored = "";

        try {
            stored = localStorage.getItem(overlayKey) || "";
        } catch (error) {
            stored = "";
        }

        overlaysEnabled = stored !== "hide";
        applyOverlayPreference();
    }

    function persistOverlayPreference() {
        try {
            localStorage.setItem(overlayKey, overlaysEnabled ?"show" : "hide");
        } catch (error) {}
    }

    function applyOverlayPreference() {
        document.body.classList.toggle("vms-hide-overlays", !overlaysEnabled);

        if (overlayToggleBtn) {
            overlayToggleBtn.textContent = overlaysEnabled ?"Linhas IA: Visíveis" : "Linhas IA: Ocultas";
            overlayToggleBtn.classList.toggle("btn-primary", !!overlaysEnabled);
            overlayToggleBtn.classList.toggle("btn-secondary", !overlaysEnabled);
        }
    }

    function setOverlaysEnabled(nextValue) {
        overlaysEnabled = !!nextValue;
        persistOverlayPreference();
        applyOverlayPreference();
    }

    function loadBoxesPreference() {
        var stored = "";

        try {
            stored = localStorage.getItem(boxesKey) || "";
        } catch (error) {
            stored = "";
        }

        boxesEnabled = stored !== "hide";
        applyBoxesPreference();
    }

    function persistBoxesPreference() {
        try {
            localStorage.setItem(boxesKey, boxesEnabled ?"show" : "hide");
        } catch (error) {}
    }

    function applyBoxesPreference() {
        document.body.classList.toggle("vms-hide-boxes", !boxesEnabled);

        if (boxesToggleBtn) {
            boxesToggleBtn.textContent = boxesEnabled ?"Boxes: Visíveis" : "Boxes: Ocultas";
            boxesToggleBtn.classList.toggle("btn-primary", !!boxesEnabled);
            boxesToggleBtn.classList.toggle("btn-secondary", !boxesEnabled);
        }
    }

    function setBoxesEnabled(nextValue) {
        boxesEnabled = !!nextValue;
        persistBoxesPreference();
        applyBoxesPreference();
    }

    function clampBoxConfidencePercent(value) {
        var parsed = Number(value);
        if (!Number.isFinite(parsed)) return 40;
        return Math.max(0, Math.min(100, Math.round(parsed)));
    }

    function updateBoxConfidenceControl() {
        if (boxConfidenceInput) {
            boxConfidenceInput.value = String(Math.round(minBoxConfidence * 100));
        }
    }

    function applyBoxConfidenceToLoadedCameras() {
        availableCameras.forEach(function (camera) {
            if (!camera) return;
            camera.monitor_boxes = filterTrackBoxes(camera.monitor_boxes);
        });
        visibleCameras.forEach(function (camera) {
            if (!camera) return;
            camera.monitor_boxes = filterTrackBoxes(camera.monitor_boxes);
        });
    }

    function setBoxConfidencePercent(value, persist) {
        var percent = clampBoxConfidencePercent(value);
        minBoxConfidence = percent / 100;
        if (persist) {
            try {
                localStorage.setItem(boxConfidenceKey, String(percent));
            } catch (error) {}
        }
        updateBoxConfidenceControl();
        applyBoxConfidenceToLoadedCameras();
        renderWall();
        if (boxesEnabled) {
            pollTrackBoxes();
        }
    }

    function loadBoxConfidencePreference() {
        var stored = "";
        try {
            stored = localStorage.getItem(boxConfidenceKey) || "";
        } catch (error) {
            stored = "";
        }
        minBoxConfidence = clampBoxConfidencePercent(stored || 15) / 100;
        updateBoxConfidenceControl();
        applyBoxConfidenceToLoadedCameras();
    }

    function trackPassesMinBoxConfidence(track) {
        var confidence = Number(track && track.confidence);
        return Number.isFinite(confidence) && confidence >= minBoxConfidence;
    }

    function filterTrackBoxes(tracks) {
        if (!Array.isArray(tracks)) return [];
        return tracks.filter(trackPassesMinBoxConfidence);
    }

    var sandboxSpansKey = "server_analiticos_vms_sandbox_spans_v1";
    var sandboxTileSpans = [];

    function loadSandboxSpans() {
        try {
            var parsed = JSON.parse(localStorage.getItem(sandboxSpansKey) || "[]");
            if (Array.isArray(parsed)) sandboxTileSpans = parsed;
        } catch (e) {
            sandboxTileSpans = [];
        }
    }

    function persistSandboxSpans() {
        try {
            localStorage.setItem(sandboxSpansKey, JSON.stringify(sandboxTileSpans));
        } catch (e) {}
    }

    function getTileSpan(slotIndex) {
        var span = sandboxTileSpans[slotIndex];
        if (span && typeof span.colSpan === "number" && typeof span.rowSpan === "number") {
            return {
                colSpan: Math.max(1, Math.min(12, span.colSpan)),
                rowSpan: Math.max(1, Math.min(12, span.rowSpan))
            };
        }
        return { colSpan: 6, rowSpan: 6 };
    }

    function setTileSpan(slotIndex, colSpan, rowSpan) {
        sandboxTileSpans[slotIndex] = {
            colSpan: Math.max(1, Math.min(12, colSpan)),
            rowSpan: Math.max(1, Math.min(12, rowSpan))
        };
        persistSandboxSpans();
    }

    function normalizeLayoutMode(value) {
        return ["standard", "featured5", "featured7", "sandbox"].indexOf(value) >= 0 ?value : "standard";
    }

    function effectiveLayoutMode() {
        if (layoutMode === "sandbox") return "sandbox";
        if (layoutMode === "featured5" && selectedGrid === 6) {
            return layoutMode;
        }
        if (layoutMode === "featured7" && selectedGrid === 8) {
            return layoutMode;
        }
        return "standard";
    }

    function wallLayoutClass() {
        var mode = effectiveLayoutMode();
        return mode === "standard" ?"layout-standard" : "layout-" + mode;
    }

    function requiredGridForLayout() {
        if (layoutMode === "featured5") return 6;
        if (layoutMode === "featured7") return 8;
        return null;
    }

    function loadLayoutModePreference() {
        var stored = "";
        try {
            stored = localStorage.getItem(layoutModeKey) || "";
        } catch (error) {
            stored = "";
        }
        layoutMode = normalizeLayoutMode(stored || layoutMode);
        loadSandboxSpans();
        applyLayoutModePreference();
    }

    function persistLayoutModePreference() {
        try {
            localStorage.setItem(layoutModeKey, layoutMode);
        } catch (error) {}
    }

    function applyLayoutModePreference() {
        if (layoutModeSelect) {
            layoutModeSelect.value = layoutMode;
        }
        if (sandboxControlsEl) {
            sandboxControlsEl.style.display = layoutMode === "sandbox" ?"inline-flex" : "none";
        }
    }

    function setLayoutMode(nextValue) {
        layoutMode = normalizeLayoutMode(nextValue);
        persistLayoutModePreference();
        applyLayoutModePreference();

        if (layoutMode === "featured5" && selectedGrid !== 6) {
            applyGridPreset(6);
            return;
        }
        if (layoutMode === "featured7" && selectedGrid !== 8) {
            applyGridPreset(8);
            return;
        }

        renderWall();
    }

    function loadVideoFitPreference() {
        var stored = "";
        try {
            stored = localStorage.getItem(videoFitKey) || "";
        } catch (error) {
            stored = "";
        }
        videoFitMode = stored === "fill" ?"fill" : "fit";
        applyVideoFitPreference();
    }

    function persistVideoFitPreference() {
        try {
            localStorage.setItem(videoFitKey, videoFitMode);
        } catch (error) {}
    }

    function applyVideoFitPreference() {
        document.body.classList.toggle("vms-fit-fill", videoFitMode === "fill");
        if (videoFitToggle) {
            videoFitToggle.textContent = videoFitMode === "fill" ?"Fill" : "Fit";
            videoFitToggle.classList.toggle("btn-primary", videoFitMode === "fill");
            videoFitToggle.classList.toggle("btn-secondary", videoFitMode !== "fill");
        }
    }

    function setVideoFitMode(nextValue) {
        videoFitMode = nextValue === "fill" ?"fill" : "fit";
        persistVideoFitPreference();
        applyVideoFitPreference();
        scheduleWallVisualLayerSync();
    }

    function loadDensityPreference() {
        var stored = "";
        try {
            stored = localStorage.getItem(densityKey) || "";
        } catch (error) {
            stored = "";
        }
        densityMode = stored === "compact" ?"compact" : "normal";
        applyDensityPreference();
    }

    function persistDensityPreference() {
        try {
            localStorage.setItem(densityKey, densityMode);
        } catch (error) {}
    }

    function applyDensityPreference() {
        document.body.classList.toggle("vms-density-compact", densityMode === "compact");
        if (densityToggle) {
            densityToggle.textContent = densityMode === "compact" ?"Normal" : "Compacto";
            densityToggle.classList.toggle("btn-primary", densityMode === "compact");
            densityToggle.classList.toggle("btn-secondary", densityMode !== "compact");
        }
    }

    function setDensityMode(nextValue) {
        densityMode = nextValue === "compact" ?"compact" : "normal";
        persistDensityPreference();
        applyDensityPreference();
        scheduleWallVisualLayerSync();
    }

    function loadOperatorModePreference() {
        try {
            operatorModeEnabled = localStorage.getItem(operatorModeKey) === "1";
        } catch (error) {
            operatorModeEnabled = false;
        }
    }

    function persistOperatorModePreference() {
        try {
            localStorage.setItem(operatorModeKey, operatorModeEnabled ?"1" : "0");
        } catch (error) {}
    }

    function applyOperatorModePreference() {
        document.body.classList.toggle("vms-operator-mode", !!operatorModeEnabled);

        if (operatorModeToggle) {
            operatorModeToggle.textContent = operatorModeEnabled ?"Modo: Operador" : "Modo: Padrão";
            operatorModeToggle.classList.toggle("btn-primary", !!operatorModeEnabled);
            operatorModeToggle.classList.toggle("btn-secondary", !operatorModeEnabled);
        }

        hydrateWallStreams({ stagger: true, forceRefresh: true });
    }

    function setOperatorMode(nextValue) {
        operatorModeEnabled = !!nextValue;
        persistOperatorModePreference();
        applyOperatorModePreference();
    }

    function loadSpotlightPreference() {
        try {
            spotlightEnabled = localStorage.getItem(spotlightEnabledKey) !== "0";
        } catch (error) {
            spotlightEnabled = true;
        }
    }

    function persistSpotlightPreference() {
        try {
            localStorage.setItem(spotlightEnabledKey, spotlightEnabled ?"1" : "0");
        } catch (error) {}
    }

    function applySpotlightPreference() {
        if (spotlightToggle) {
            spotlightToggle.textContent = spotlightEnabled ?"Spotlight: Ligado" : "Spotlight: Desligado";
            spotlightToggle.classList.toggle("btn-primary", !!spotlightEnabled);
            spotlightToggle.classList.toggle("btn-secondary", !spotlightEnabled);
        }

        if (!spotlightEnabled) {
            resetSpotlight();
        } else {
            updateAlarmSpotlight();
        }
    }

    function loadCentralPopupPreference() {
        try {
            centralPopupEnabled = localStorage.getItem(centralPopupEnabledKey) !== "0";
        } catch (error) {
            centralPopupEnabled = true;
        }
    }

    function persistCentralPopupPreference() {
        try {
            localStorage.setItem(centralPopupEnabledKey, centralPopupEnabled ?"1" : "0");
        } catch (error) {}
    }

    function applyCentralPopupPreference() {
        var btn = document.getElementById("centralPopupToggleBtn");
        if (btn) {
            btn.textContent = centralPopupEnabled ?"Popup Central ligado" : "Popup Central desligado";
            btn.classList.toggle("btn-primary", !!centralPopupEnabled);
            btn.classList.toggle("btn-secondary", !centralPopupEnabled);
        }
    }

    function setSpotlightEnabled(nextValue) {
        spotlightEnabled = !!nextValue;
        persistSpotlightPreference();
        applySpotlightPreference();
    }

    function loadSavedViews() {
        try {
            var parsed = JSON.parse(localStorage.getItem(savedViewsKey) || "[]");
            return Array.isArray(parsed) ?parsed.filter(function (view) {
                return view && view.id && view.name;
            }) : [];
        } catch (error) {
            return [];
        }
    }

    function persistSavedViews(views) {
        try {
            localStorage.setItem(savedViewsKey, JSON.stringify(views || []));
        } catch (error) {}
    }

    function normalizeServerView(view) {
        var config = view && view.view_config && typeof view.view_config === "object" ?view.view_config : {};
        return Object.assign({}, config, {
            id: view.id,
            name: view.name,
            grid: Number(config.grid || view.grid_size || 16),
            assignments: Array.isArray(config.assignments) ?config.assignments : (Array.isArray(view.camera_ids) ?view.camera_ids : []),
            hideOffline: config.hideOffline === true || view.hide_offline === true,
            boxesEnabled: config.boxesEnabled !== false && view.boxes_enabled !== false,
            isShared: view.is_shared === true,
            ownerUsername: view.owner_username || "",
            canManage: view.can_manage === true
        });
    }

    function viewPresetRequestPayload(view) {
        return {
            id: view.id,
            name: view.name,
            grid_size: Number(view.grid || 16),
            camera_ids: Array.isArray(view.assignments) ?view.assignments : [],
            hide_offline: view.hideOffline === true,
            boxes_enabled: view.boxesEnabled !== false,
            view_config: view,
            is_shared: view.isShared === true
        };
    }

    function selectedSavedView() {
        if (!savedViewSelect || !savedViewSelect.value) return null;
        return loadSavedViews().find(function (view) {
            return view.id === savedViewSelect.value;
        }) || null;
    }

    function updateSavedViewActions() {
        var view = selectedSavedView();
        var canManage = !!(view && view.canManage !== false);
        if (deleteViewBtn) deleteViewBtn.disabled = !canManage;
        if (shareViewCheckbox && view) {
            shareViewCheckbox.checked = view.isShared === true;
            shareViewCheckbox.disabled = !canManage;
        } else if (shareViewCheckbox) {
            shareViewCheckbox.disabled = false;
        }
    }

    function renderSavedViewsSelect(selectedId) {
        if (!savedViewSelect) return;
        var views = loadSavedViews();
        savedViewSelect.innerHTML = '<option value="">Visoes salvas</option>' + views.map(function (view) {
            var source = view.isShared && view.ownerUsername ?" - " + view.ownerUsername : "";
            return '<option value="' + escapeHtml(view.id) + '">' + escapeHtml(view.name + source) + '</option>';
        }).join("");
        if (selectedId) savedViewSelect.value = selectedId;
        updateSavedViewActions();
    }

    function loadTemporalMosaics() {
        try {
            var parsed = JSON.parse(localStorage.getItem(temporalMosaicsKey) || "[]");
            return Array.isArray(parsed) ?parsed : [];
        } catch (e) {
            return [];
        }
    }

    function renderTemporalSequenceSelect() {
        if (!temporalSequenceSelect) return;
        var seqs = loadTemporalMosaics();
        temporalSequenceSelect.innerHTML = '<option value="">Mosaicos Temporais</option>' + seqs.map(function (seq) {
            return '<option value="' + escapeHtml(seq.id) + '">' + escapeHtml(seq.name) + '</option>';
        }).join("");
    }

    function applySelectedTemporalSequence() {
        if (!temporalSequenceSelect || !temporalSequenceSelect.value) return;
        var id = temporalSequenceSelect.value;
        var seqs = loadTemporalMosaics();
        var seq = seqs.find(function (s) { return s.id === id; });
        if (!seq || !seq.steps.length) return;

        stopSequence(true);
        localStorage.setItem(activeSequenceKey, JSON.stringify({
            sequenceId: id,
            stepIndex: 0,
            lastUpdated: Date.now()
        }));

        initTemporalSequencePlayback();
    }

    function isTemporalSequenceActive() {
        try {
            var activeSeqData = JSON.parse(localStorage.getItem(activeSequenceKey));
            return !!(activeSeqData && activeSeqData.sequenceId);
        } catch (e) {
            return false;
        }
    }

    function normalizeTemporalStepIndex(value, stepCount) {
        var count = Number(stepCount);
        if (!Number.isFinite(count) || count <= 0) return 0;
        var index = Number(value);
        if (!Number.isFinite(index)) index = 0;
        index = Math.floor(index);
        return ((index % count) + count) % count;
    }

    function normalizeTemporalStepDurationSeconds(value) {
        var duration = Number(value);
        if (!Number.isFinite(duration) || duration <= 0) {
            duration = 10;
        }
        return Math.max(1, Math.min(3600, duration));
    }

    function persistTemporalSequenceState(sequenceId, stepIndex) {
        localStorage.setItem(activeSequenceKey, JSON.stringify({
            sequenceId: sequenceId,
            stepIndex: stepIndex,
            lastUpdated: Date.now()
        }));
    }

    function stopTemporalSequence() {
        localStorage.removeItem(activeSequenceKey);
        if (temporalSequenceTimerId) {
            clearTimeout(temporalSequenceTimerId);
            temporalSequenceTimerId = null;
        }
        if (temporalSequenceCountdownIntervalId) {
            clearInterval(temporalSequenceCountdownIntervalId);
            temporalSequenceCountdownIntervalId = null;
        }
        if (vmsTemporalSequenceBanner) {
            vmsTemporalSequenceBanner.style.display = "none";
            vmsTemporalSequenceBanner.setAttribute("hidden", "true");
        }
        if (temporalSequenceSelect) {
            temporalSequenceSelect.value = "";
        }
        
        var barContainer = document.getElementById("vmsSequenceProgressBarContainer");
        if (barContainer) barContainer.style.display = "none";
        
        var nameOverlay = document.getElementById("vmsSequenceNameOverlay");
        if (nameOverlay) nameOverlay.style.opacity = "0";

        updateSequenceUi();
    }

    function ensureVmsSequenceHud() {
        if (!wallEl) return;
        
        wallEl.style.position = "relative";
        
        if (!document.getElementById("vmsPulseStyle")) {
            var style = document.createElement("style");
            style.id = "vmsPulseStyle";
            style.innerHTML = "@keyframes vmsPulse { 0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(139, 92, 246, 0.7); } 70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(139, 92, 246, 0); } 100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(139, 92, 246, 0); } }";
            document.head.appendChild(style);
        }
        
        var barContainer = document.getElementById("vmsSequenceProgressBarContainer");
        if (!barContainer) {
            barContainer = document.createElement("div");
            barContainer.id = "vmsSequenceProgressBarContainer";
            barContainer.style.cssText = "position: absolute; top: 0; left: 0; right: 0; height: 6px; background: rgba(0,0,0,0.3); z-index: 10000; display: none; pointer-events: none;";
            
            var progressBar = document.createElement("div");
            progressBar.id = "vmsSequenceProgressBar";
            progressBar.style.cssText = "width: 0%; height: 100%; background: linear-gradient(90deg, #8b5cf6, #3b82f6); transition: none; box-shadow: 0 0 10px rgba(139, 92, 246, 0.8);";
            
            barContainer.appendChild(progressBar);
            wallEl.appendChild(barContainer);
        } else if (barContainer.parentElement !== wallEl) {
            wallEl.appendChild(barContainer);
        }

        var nameOverlay = document.getElementById("vmsSequenceNameOverlay");
        if (!nameOverlay) {
            nameOverlay = document.createElement("div");
            nameOverlay.id = "vmsSequenceNameOverlay";
            nameOverlay.style.cssText = "position: absolute; top: 24px; left: 50%; transform: translateX(-50%); background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 99px; padding: 12px 28px; z-index: 10000; color: #fff; font-family: inherit; font-size: 16px; font-weight: 700; box-shadow: 0 10px 30px rgba(0,0,0,0.6); opacity: 0; transition: opacity 0.4s cubic-bezier(0.4, 0, 0.2, 1); pointer-events: none; display: flex; align-items: center; gap: 8px;";
            
            var statusDot = document.createElement("span");
            statusDot.style.cssText = "background: #8b5cf6; width: 10px; height: 10px; border-radius: 50%; display: inline-block; box-shadow: 0 0 10px #8b5cf6; animation: vmsPulse 1.5s infinite;";
            
            var nameText = document.createElement("span");
            nameText.id = "vmsSequenceNameText";
            nameText.textContent = "";

            nameOverlay.appendChild(statusDot);
            nameOverlay.appendChild(nameText);
            wallEl.appendChild(nameOverlay);
        } else if (nameOverlay.parentElement !== wallEl) {
            wallEl.appendChild(nameOverlay);
        }
    }

    function initTemporalSequencePlayback() {
        var activeSeqData = null;
        try {
            activeSeqData = JSON.parse(localStorage.getItem(activeSequenceKey));
        } catch (e) {}

        if (!activeSeqData || !activeSeqData.sequenceId) {
            if (vmsTemporalSequenceBanner) {
                vmsTemporalSequenceBanner.style.display = "none";
                vmsTemporalSequenceBanner.setAttribute("hidden", "true");
            }
            return;
        }

        var seqs = loadTemporalMosaics();
        var seq = seqs.find(function (s) { return s.id === activeSeqData.sequenceId; });
        if (!seq || !seq.steps.length) {
            stopTemporalSequence();
            return;
        }

        var stepIndex = normalizeTemporalStepIndex(activeSeqData.stepIndex, seq.steps.length);
        if (stepIndex !== activeSeqData.stepIndex) {
            persistTemporalSequenceState(seq.id, stepIndex);
        }

        var step = seq.steps[stepIndex];
        var stepDurationSeconds = normalizeTemporalStepDurationSeconds(step && step.duration);
        var views = loadSavedViews();
        var view = views.find(function (v) { return v.id === step.viewId; });

        if (!view) {
            var missingViewNextIndex = normalizeTemporalStepIndex(stepIndex + 1, seq.steps.length);
            persistTemporalSequenceState(seq.id, missingViewNextIndex);
            if (temporalSequenceTimerId) clearTimeout(temporalSequenceTimerId);
            temporalSequenceTimerId = setTimeout(initTemporalSequencePlayback, 250);
            return;
        }

        applySavedView(view);
        ensureVmsSequenceHud();
        updateSequenceUi();

        var barContainer = document.getElementById("vmsSequenceProgressBarContainer");
        var progressBar = document.getElementById("vmsSequenceProgressBar");
        var nameOverlay = document.getElementById("vmsSequenceNameOverlay");
        var nameText = document.getElementById("vmsSequenceNameText");

        if (barContainer) barContainer.style.display = "block";
        if (progressBar) progressBar.style.width = "100%";

        if (nameOverlay && nameText) {
            nameText.textContent = view.name;
            nameOverlay.style.opacity = "1";
            
            if (nameOverlay.dataset.fadeTimeoutId) {
                clearTimeout(parseInt(nameOverlay.dataset.fadeTimeoutId));
            }
            var fadeId = setTimeout(function () {
                var el = document.getElementById("vmsSequenceNameOverlay");
                if (el) el.style.opacity = "0";
            }, 2500);
            nameOverlay.dataset.fadeTimeoutId = String(fadeId);
        }

        if (vmsTemporalSequenceBanner) {
            vmsTemporalSequenceBanner.style.display = "flex";
            vmsTemporalSequenceBanner.removeAttribute("hidden");
        }

        var infoEl = document.getElementById("vmsTemporalSequenceInfo");
        if (infoEl) {
            infoEl.textContent = "Sequência Ativa: " + seq.name + " (" + view.name + " - Próximo em " + stepDurationSeconds + "s)";
        }

        if (temporalSequenceSelect) {
            temporalSequenceSelect.value = seq.id;
        }

        if (temporalSequenceTimerId) clearTimeout(temporalSequenceTimerId);
        temporalSequenceTimerId = null;
        var nextIndex = normalizeTemporalStepIndex(stepIndex + 1, seq.steps.length);
        temporalSequenceTimerId = setTimeout(function () {
            temporalSequenceTimerId = null;
            persistTemporalSequenceState(seq.id, nextIndex);
            initTemporalSequencePlayback();
        }, stepDurationSeconds * 1000);

        if (temporalSequenceCountdownIntervalId) clearInterval(temporalSequenceCountdownIntervalId);
        var totalMs = stepDurationSeconds * 1000;
        var elapsedMs = 0;
        var tickMs = 100;
        temporalSequenceCountdownIntervalId = setInterval(function () {
            elapsedMs += tickMs;
            var secondsLeft = Math.ceil((totalMs - elapsedMs) / 1000);
            if (secondsLeft < 0) secondsLeft = 0;
            
            if (infoEl) {
                infoEl.textContent = "Sequência Ativa: " + seq.name + " (" + view.name + " - Próximo em " + secondsLeft + "s)";
            }
            
            if (progressBar) {
                var pct = Math.max(0, 100 - (elapsedMs / totalMs) * 100);
                progressBar.style.width = pct + "%";
            }
            
            if (elapsedMs >= totalMs) {
                clearInterval(temporalSequenceCountdownIntervalId);
            }
        }, tickMs);
    }

    function advanceTemporalSequenceNext() {
        var activeSeqData = null;
        try {
            activeSeqData = JSON.parse(localStorage.getItem(activeSequenceKey));
        } catch (e) {}
        if (!activeSeqData || !activeSeqData.sequenceId) return;

        var seqs = loadTemporalMosaics();
        var seq = seqs.find(function (s) { return s.id === activeSeqData.sequenceId; });
        if (!seq || !seq.steps.length) return;

        var currentIndex = normalizeTemporalStepIndex(activeSeqData.stepIndex, seq.steps.length);
        var nextIndex = normalizeTemporalStepIndex(currentIndex + 1, seq.steps.length);
        persistTemporalSequenceState(seq.id, nextIndex);
        initTemporalSequencePlayback();
    }

    function fetchMiniEvents(cameraId, contentEl) {
        if (!contentEl) return;
        contentEl.innerHTML = '<div style="text-align:center; padding:10px; color:var(--text-muted);">Carregando...</div>';
        fetch("/cameras/" + cameraId + "/events-data", { cache: "no-store" })
            .then(function(res) {
                if (!res.ok) throw new Error("HTTP " + res.status);
                return res.json();
            })
            .then(function(data) {
                var events = data.events || [];
                if (!events.length) {
                    contentEl.innerHTML = '<div style="text-align:center; padding:10px; color:var(--text-muted);">Nenhum alerta recente.</div>';
                    return;
                }
                var recent = events.slice(0, 3);
                contentEl.innerHTML = recent.map(function(ev) {
                    var imgHtml = ev.snapshot_url 
                        ?'<img src="' + ev.snapshot_url + '" style="width:50px; height:50px; object-fit:cover; border-radius:6px; border:1px solid rgba(255,255,255,0.12);" />'
                        : '<div style="width:50px; height:50px; background:rgba(255,255,255,0.05); border-radius:6px; display:flex; align-items:center; justify-content:center; font-size:16px;">🖼</div>';
                    var timeStr = "-";
                    if (ev.created_at_label) {
                        var parts = ev.created_at_label.split(' ');
                        timeStr = parts.length > 1 ?parts[1] : ev.created_at_label;
                    }
                    return ''
                        + '<div style="display:flex; gap:8px; align-items:center; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06); padding:6px; border-radius:8px; margin-bottom:4px;">'
                        + '    ' + imgHtml
                        + '    <div style="flex:1; min-width:0;">'
                        + '        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:2px;">'
                        + '            <span class="status priority-' + ev.severity + '" style="font-size:9px; padding:1px 4px; border-radius:4px; font-weight:700;">' + ev.severity_label + '</span>'
                        + '            <span style="font-size:9px; color:var(--text-muted);">' + timeStr + '</span>'
                        + '        </div>'
                        + '        <div style="font-weight:700; font-size:11px; text-overflow:ellipsis; overflow:hidden; white-space:nowrap; color:var(--text-primary);">' + ev.event_type_label + '</div>'
                        + '        <div style="font-size:10px; color:var(--text-secondary);">Confiança: ' + (ev.confidence ?ev.confidence.toFixed(2) : '-') + '</div>'
                        + '    </div>'
                        + '</div>';
                }).join("");
            })
            .catch(function(err) {
                console.error("Erro ao buscar mini eventos:", err);
                contentEl.innerHTML = '<div style="text-align:center; padding:10px; color:var(--red);">Erro ao carregar dados.</div>';
            });
    }

    function currentViewPayload(name) {
        return {
            id: "view_" + Date.now(),
            name: name,
            grid: selectedGrid,
            layoutMode: layoutMode,
            sandboxTileSpans: sandboxTileSpans.slice(),
            videoFitMode: videoFitMode,
            densityMode: densityMode,
            tileDetailsEnabled: !!tileDetailsEnabled,
            overlaysEnabled: !!overlaysEnabled,
            boxesEnabled: !!boxesEnabled,
            assignments: assignments.slice()
        };
    }

    async function saveCurrentView() {
        var name = window.prompt("Nome da visao", "");
        name = safeString(name).trim();
        if (!name) return;

        var views = loadSavedViews();
        var existing = views.find(function (view) {
            return view.canManage !== false && safeString(view.name).toLowerCase() === name.toLowerCase();
        });
        var payload = currentViewPayload(name);
        if (existing) payload.id = existing.id;
        payload.isShared = shareViewCheckbox ?shareViewCheckbox.checked : !!(existing && existing.isShared);
        payload.canManage = true;
        payload.ownerUsername = existing ?existing.ownerUsername : "";
        views = views.filter(function (view) {
            return view.id !== payload.id;
        });
        views.push(payload);
        views.sort(function (a, b) {
            return safeString(a.name).localeCompare(safeString(b.name), "pt-BR");
        });
        persistSavedViews(views);
        if (window.logClientAudit) {
            window.logClientAudit("mosaico_save", "Salvou o mosaico estático: " + name);
        }
        renderSavedViewsSelect(payload.id);

        fetch("/api/view-presets", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(viewPresetRequestPayload(payload))
        }).then(function (response) {
            if (!response.ok) throw new Error("Falha ao salvar mosaico no servidor");
            return syncMosaicosFromServer();
        }).catch(function(err) {
            console.error("Erro ao salvar mosaico no servidor:", err);
            window.alert("O mosaico foi salvo apenas neste navegador. Tente sincronizar novamente.");
        });
    }

    function applySavedView(view) {
        if (!view) return;
        if (window.logClientAudit) {
            window.logClientAudit("mosaico_apply", "Aplicou o mosaico: " + view.name);
        }

        var targetGrid = Number(view.grid || selectedGrid);
        if (allowedGridValues.indexOf(targetGrid) === -1) {
            targetGrid = selectedGrid;
        }

        if (targetGrid !== selectedGrid) {
            switchGridInPlace(targetGrid, {
                assignments: Array.isArray(view.assignments) ?view.assignments : []
            });
        }

        if (Array.isArray(view.sandboxTileSpans)) {
            sandboxTileSpans = view.sandboxTileSpans.slice();
            persistSandboxSpans();
        }

        layoutMode = normalizeLayoutMode(view.layoutMode);
        videoFitMode = view.videoFitMode === "fill" ?"fill" : "fit";
        densityMode = view.densityMode === "compact" ?"compact" : "normal";
        tileDetailsEnabled = !!view.tileDetailsEnabled;
        overlaysEnabled = view.overlaysEnabled !== false;
        boxesEnabled = view.boxesEnabled !== false;
        assignments = clampAssignments(Array.isArray(view.assignments) ?view.assignments : []);

        persistAssignments();
        persistLayoutModePreference();
        persistVideoFitPreference();
        persistDensityPreference();
        persistTileDetailsPreference();
        persistOverlayPreference();
        persistBoxesPreference();

        applyLayoutModePreference();
        applyVideoFitPreference();
        applyDensityPreference();
        applyTileDetailsPreference();
        applyOverlayPreference();
        applyBoxesPreference();
        renderWall();
    }

    function applySelectedSavedView() {
        applySavedView(selectedSavedView());
    }

    async function deleteSelectedSavedView() {
        var deletedView = selectedSavedView();
        if (!deletedView) return;
        if (deletedView.canManage === false) {
            window.alert("Somente o administrador que compartilhou este mosaico pode altera-lo ou exclui-lo.");
            return;
        }
        if (!window.confirm("Excluir o mosaico selecionado?")) return;
        var id = deletedView.id;
        var savedViews = loadSavedViews();
        var name = deletedView ?deletedView.name : id;

        try {
            var response = await fetch("/api/view-presets/" + encodeURIComponent(id), { method: "DELETE" });
            if (!response.ok) throw new Error("Falha ao excluir mosaico no servidor");
            persistSavedViews(savedViews.filter(function (view) { return view.id !== id; }));
            if (window.logClientAudit) {
                window.logClientAudit("mosaico_delete", "Excluiu o mosaico estático: " + name);
            }
            renderSavedViewsSelect();
        } catch (error) {
            console.error("Erro ao deletar mosaico no servidor:", error);
            window.alert("Nao foi possivel excluir este mosaico.");
        }
    }

    function consumePendingSavedView() {
        var view = null;
        try {
            var raw = localStorage.getItem(pendingViewKey);
            if (raw) view = JSON.parse(raw);
            localStorage.removeItem(pendingViewKey);
        } catch (error) {
            view = null;
        }

        if (view) {
            applySavedView(view);
        }
    }

    function isCameraWebrtcReady(camera) {
        return !!(camera && camera.webrtc_enabled && camera.webrtc_registration_ok && camera.webrtc_player_url);
    }

    function publicWebrtcBaseUrl() {
        if (webrtcPublicBaseUrl) {
            return webrtcPublicBaseUrl;
        }
        // O fallback direto existe apenas para instalacoes LAN servidas em HTTP.
        // Sob HTTPS, inventar https://host-do-painel:8889 causa URL incorreta e
        // mascara a ausencia de WEBRTC_GATEWAY_PUBLIC_BASE_URL.
        if (window.location.protocol === "http:") {
            return "http://" + window.location.hostname + ":8889";
        }
        return "";
    }

    function cameraWebrtcPath(camera) {
        return safeString(camera && camera.webrtc_path) || (camera && camera.id ?"cam_" + camera.id : "");
    }

    function sanitizedPlayerUrl(url) {
        if (!safeString(url)) return "";
        try {
            var parsed = new URL(url, window.location.href);
            parsed.username = "";
            parsed.password = "";
            return parsed.origin + parsed.pathname;
        } catch (error) {
            return safeString(url).split("?", 1)[0];
        }
    }

    function browserSafePlayerUrl(url) {
        try {
            var parsed = new URL(url, window.location.href);
            var host = safeString(parsed.hostname).toLowerCase();
            if (host === "localhost" || host === "127.0.0.1" || host === "::1" || host === "webrtc-gateway") {
                return false;
            }
            if (window.location.protocol === "https:" && (parsed.protocol !== "https:" || parsed.port === "8889")) {
                return false;
            }
            return parsed.protocol === "http:" || parsed.protocol === "https:";
        } catch (error) {
            return false;
        }
    }

    function logWebrtcPlayerResolution(camera, url, context) {
        var cameraId = safeString(camera && camera.id) || "unknown";
        var path = cameraWebrtcPath(camera) || "unknown";
        var safeUrl = sanitizedPlayerUrl(url) || "unavailable";
        var key = [context || "player", cameraId, path, safeUrl].join("|");
        if (webrtcPlayerLogKeys.has(key)) return;
        webrtcPlayerLogKeys.add(key);
        console.info("[WebRTC] player resolvido context=" + (context || "player")
            + " camera=" + cameraId + " path=" + path + " url=" + safeUrl);
    }

    function probeWebrtcPlayer(camera, url, context) {
        if (!url || !window.fetch) return Promise.resolve(null);
        var safeUrl = sanitizedPlayerUrl(url);
        if (webrtcPlayerProbeCache.has(safeUrl)) return webrtcPlayerProbeCache.get(safeUrl);

        var probe = fetch(url, { method: "GET", mode: "cors", cache: "no-store" })
            .then(function (response) {
                var message = response.ok ?"ok" : (response.statusText || "http_error");
                var line = "[WebRTC] player HTTP context=" + (context || "player")
                    + " camera=" + (safeString(camera && camera.id) || "unknown")
                    + " path=" + (cameraWebrtcPath(camera) || "unknown")
                    + " url=" + safeUrl + " status=" + response.status
                    + " message=" + message;
                if (response.ok) console.info(line); else console.error(line);
                return { ok: response.ok, status: response.status, message: message };
            })
            .catch(function (error) {
                var message = safeString(error && error.message) || "network_error";
                console.error("[WebRTC] player HTTP context=" + (context || "player")
                    + " camera=" + (safeString(camera && camera.id) || "unknown")
                    + " path=" + (cameraWebrtcPath(camera) || "unknown")
                    + " url=" + safeUrl + " status=0 message=" + message);
                return { ok: false, status: 0, message: message };
            });
        webrtcPlayerProbeCache.set(safeUrl, probe);
        return probe;
    }

    function resolveWebrtcPlayerUrl(camera, context) {
        var playerUrl = safeString(camera && camera.webrtc_player_url);
        var path = cameraWebrtcPath(camera);
        var publicBase = publicWebrtcBaseUrl();
        var resolved = "";

        // A base publica configurada e autoritativa, inclusive diante de um
        // player_url antigo em cache/payload. Mosaico e spotlight usam isto.
        if (webrtcPublicBaseUrl && path) {
            resolved = webrtcPublicBaseUrl + "/" + encodeURIComponent(path);
        } else if (playerUrl.indexOf("/__webrtc_public__/") === 0) {
            resolved = publicBase && path ?publicBase + "/" + encodeURIComponent(path) : "";
        } else {
            resolved = playerUrl || (publicBase && path ?publicBase + "/" + encodeURIComponent(path) : "");
        }

        if (!resolved) {
            logWebrtcPlayerResolution(camera, "", context || "player");
            return "";
        }
        if (!browserSafePlayerUrl(resolved)) {
            console.error("[WebRTC] URL publica rejeitada context=" + (context || "player")
                + " camera=" + (safeString(camera && camera.id) || "unknown")
                + " path=" + (path || "unknown") + " url=" + sanitizedPlayerUrl(resolved));
            return "";
        }
        resolved = appendPlayerParams(resolved, camera);
        logWebrtcPlayerResolution(camera, resolved, context || "player");
        return resolved;
    }

    function computeMutedValue(camera) {
        return (camera && audioCameraId && String(camera.id) === String(audioCameraId)) ?"false" : "true";
    }

    function appendPlayerParams(resolved, camera) {
        var mutedValue = computeMutedValue(camera);
        try {
            var url = new URL(resolved, window.location.href);
            url.searchParams.set("controls", "false");
            url.searchParams.set("muted", mutedValue);
            url.searchParams.set("autoplay", "true");
            url.searchParams.set("playsInline", "true");
            return url.toString();
        } catch (error) {
            var separator = resolved.indexOf("?") >= 0 ?"&" : "?";
            return resolved + separator + "controls=false&muted=" + mutedValue + "&autoplay=true&playsInline=true";
        }
    }

    // Somente uma camera pode ter audio por vez (evita varias tiles tocando
    // som ao mesmo tempo). Alterna o audio da camera indicada e reaplica o
    // estado nos botoes e nos iframes ja renderizados.
    function toggleTileAudio(cameraId) {
        var normalized = safeString(cameraId);
        if (!normalized) return;
        audioCameraId = (String(audioCameraId) === normalized) ?"" : normalized;
        applyTileAudioState();
    }

    function applyTileAudioState() {
        if (wallEl) {
            var audioButtons = wallEl.querySelectorAll('[data-action="toggle-audio"]');
            Array.prototype.forEach.call(audioButtons, function (btn) {
                var btnCameraId = btn.getAttribute("data-camera-id");
                var on = !!btnCameraId && String(btnCameraId) === String(audioCameraId);
                btn.classList.toggle("is-audio-on", on);
                btn.textContent = on ?"🔊" : "🔇";
                btn.title = on ?"Áudio ligado — clique para silenciar" : "Escutar áudio desta câmera";
                btn.setAttribute("aria-label", btn.title);
            });
        }
        // syncWallTileMetadata recalcula o src de cada iframe (via
        // cameraStreamUrl -> resolveWebrtcPlayerUrl, que agora depende de
        // audioCameraId) e o troca quando muda, religando a track de audio.
        syncWallTileMetadata();
    }

    // O mosaico e WebRTC: o que importa e se o navegador NEGOCIA H.265 em RTP,
    // nao se ele abre um MP4 HEVC. Chrome no Windows decodifica HEVC em arquivo
    // por hardware e ainda assim pode nao ofertar o codec no SDP - checar o MP4
    // dava falso positivo e o helper nunca era acionado.
    function detectWebrtcHevcSupport() {
        try {
            if (typeof RTCRtpReceiver === "undefined"
                || typeof RTCRtpReceiver.getCapabilities !== "function") {
                return null;
            }
            var capabilities = RTCRtpReceiver.getCapabilities("video");
            var codecs = capabilities && capabilities.codecs;
            if (!codecs || !codecs.length) return null;
            return codecs.some(function (codec) {
                return /h265|hevc/i.test(safeString(codec && codec.mimeType));
            });
        } catch (error) {
            return null;
        }
    }

    function detectClientHevcSupport() {
        var mediaCapabilities = navigator.mediaCapabilities;
        var sampleCodec = 'video/mp4; codecs="hvc1.1.6.L93.B0"';

        function finish(supported) {
            clientHevcSupported = !!supported;
            clientHevcSupportKnown = true;
            applyDefaultVideoHelperFallback();
            // So aqui sabemos se vale oferecer o instalador ao operador.
            updateSnapshotFallbackNotice();
            pollWebrtcDiagnostics();
        }

        // Caminho sincrono: decide ainda no boot, sem esperar promise nenhuma.
        var webrtcSupport = detectWebrtcHevcSupport();
        if (webrtcSupport !== null) {
            finish(webrtcSupport);
            return;
        }

        if (mediaCapabilities && typeof mediaCapabilities.decodingInfo === "function") {
            try {
                mediaCapabilities.decodingInfo({
                    type: "file",
                    video: {
                        contentType: sampleCodec,
                        width: 640,
                        height: 360,
                        bitrate: 1200000,
                        framerate: 15
                    }
                }).then(function (info) {
                    finish(!!(info && info.supported));
                }).catch(function () {
                    finish(!!(window.MediaSource && MediaSource.isTypeSupported && MediaSource.isTypeSupported(sampleCodec)));
                });
                return;
            } catch (error) {}
        }

        finish(!!(window.MediaSource && MediaSource.isTypeSupported && MediaSource.isTypeSupported(sampleCodec)));
    }

    // O helper pode subir depois da pagina (servico do Windows iniciando, instalacao
    // no meio da sessao). Sem re-checagem o operador precisava dar F5 para o mosaico
    // enxergar o decoder local.
    function scheduleVideoHelperRecheck() {
        if (disposed || videoHelperAvailable) return;
        if (videoHelperRetryTimerId) clearTimeout(videoHelperRetryTimerId);
        videoHelperRetryTimerId = setTimeout(function () {
            videoHelperRetryTimerId = null;
            detectVideoHelper();
        }, videoHelperRetryDelayMs);
        // Sobe de 3s ate 30s para nao martelar o localhost em maquina sem helper.
        videoHelperRetryDelayMs = Math.min(videoHelperRetryDelayMs * 2, 30000);
    }

    function detectVideoHelper() {
        if (!window.fetch || disposed) return;
        var controller = typeof AbortController !== "undefined" ?new AbortController() : null;
        var timeoutId = setTimeout(function () {
            if (controller) controller.abort();
        }, 1200);

        fetch(videoHelperBaseUrl + "/health", {
            method: "GET",
            cache: "no-store",
            mode: "cors",
            signal: controller ?controller.signal : undefined
        }).then(function (response) {
            if (!response.ok) throw new Error("helper_unavailable");
            return response.json();
        }).then(function (payload) {
            videoHelperAvailable = !!(payload && payload.ok);
            // Helpers antigos nao publicam "ports"; nesse caso segue so a 34020.
            var ports = payload && payload.ports;
            if (Array.isArray(ports) && ports.length) {
                videoHelperPorts = ports.map(Number).filter(function (port) {
                    return isFinite(port) && port > 0;
                });
            }
            if (!videoHelperPorts.length) videoHelperPorts = [34020];
        }).catch(function () {
            videoHelperAvailable = false;
        }).finally(function () {
            clearTimeout(timeoutId);
            if (videoHelperAvailable) {
                videoHelperRetryDelayMs = 3000;
            } else {
                scheduleVideoHelperRecheck();
            }
            applyDefaultVideoHelperFallback();
            refreshSnapshotFallbacks();
            updateSnapshotFallbackNotice();
            if (liveAlarmCurrent && liveAlarmModalEl && !liveAlarmModalEl.hidden) {
                liveAlarmRenderedEventId = "";
                renderLiveAlarmVideo(
                    liveAlarmCurrent,
                    cameraById.get(alarmCameraId(liveAlarmCurrent))
                );
            }
        });
    }

    // Diagnostico sob demanda: no console do navegador, rode sunorusDiagVideo()
    // para ver, por tile, por que existe (ou nao) imagem.
    window.sunorusDiagVideo = function () {
        var linhas = [];
        var tiles = wallEl ?wallEl.querySelectorAll(".vms-tile[data-camera-id]") : [];
        Array.prototype.forEach.call(tiles, function (tile) {
            var cameraId = tile.getAttribute("data-camera-id");
            var camera = cameraById.get(String(cameraId));
            var image = tile.querySelector(".vms-snapshot-fallback");
            var player = (cameraOperationalHealth(camera) || {}).player || {};
            var src = image ?safeString(image.getAttribute("src")) : "";
            linhas.push({
                camera: cameraId,
                nome: safeString(camera && camera.name),
                webrtcPronto: isCameraWebrtcReady(camera),
                emFallback: tile.classList.contains("vms-using-snapshot-fallback"),
                fonte: safeString(tile.getAttribute("data-fonte")) || "(nao definida)",
                usandoHelper: isVideoHelperUrl(src),
                helperFalhou: safeString(image && image.getAttribute("data-helper-failed")),
                playerStatus: safeString(player.label) || "(sem diagnostico)",
                playerOk: player.ok,
                imgSrc: src || "(vazio)",
                imgCarregou: image ?(image.naturalWidth > 0) : null
            });
        });
        console.log("helper disponivel:", videoHelperAvailable,
            "| HEVC no WebRTC:", clientHevcSupported,
            "| deteccao concluida:", clientHevcSupportKnown,
            "| forcar helper em todas:", shouldDefaultToVideoHelper());
        if (console.table) console.table(linhas); else console.log(linhas);
        return linhas;
    };

    // O helper escuta em portas vizinhas justamente para o mosaico ter varias
    // origens: o navegador conta o limite de 6 conexoes por origem, entao cada
    // porta extra libera mais 6 tiles. Distribuimos por camera para o tile manter
    // sempre a mesma porta entre re-renderizacoes.
    function videoHelperPortFor(cameraId) {
        if (videoHelperPorts.length <= 1) return videoHelperPorts[0] || 34020;
        var numeric = parseInt(String(cameraId), 10);
        if (!isFinite(numeric)) numeric = 0;
        var index = Math.abs(numeric) % videoHelperPorts.length;
        return videoHelperPorts[index];
    }

    function videoHelperOriginFor(cameraId) {
        return "http://127.0.0.1:" + videoHelperPortFor(cameraId);
    }

    function isVideoHelperUrl(url) {
        return /^http:\/\/127\.0\.0\.1:\d+\/stream\//.test(safeString(url));
    }

    function videoHelperStreamUrl(cameraId) {
        var serverHost = window.location.hostname || "";
        return videoHelperOriginFor(cameraId)
            + "/stream/" + encodeURIComponent(String(cameraId)) + ".mjpeg"
            + "?server=" + encodeURIComponent(serverHost)
            + "&width=960&fps=10";
    }

    function shouldDefaultToVideoHelper() {
        return videoHelperAvailable && clientHevcSupportKnown && !clientHevcSupported;
    }

    // Esta estacao ganharia video H.265 de verdade se o helper estivesse instalado.
    function precisaInstalarVideoHelper() {
        return clientHevcSupportKnown && !clientHevcSupported && !videoHelperAvailable;
    }

    // Uma consulta por sessao: o instalador so muda quando alguem publica um novo,
    // e a resposta so serve para decidir se o botao aparece.
    function loadVideoHelperDownload() {
        if (videoHelperDownloadRequested || !window.fetch || disposed) return;
        videoHelperDownloadRequested = true;

        fetch("/downloads/video-helper/status", {
            method: "GET",
            cache: "no-store",
            credentials: "same-origin"
        }).then(function (response) {
            if (!response.ok) throw new Error("status_indisponivel");
            return response.json();
        }).then(function (payload) {
            videoHelperDownload = payload && payload.disponivel ?payload : null;
        }).catch(function () {
            // Servidor sem instalador publicado: o aviso continua, sem o botao.
            videoHelperDownload = null;
        }).finally(function () {
            updateSnapshotFallbackNotice();
        });
    }

    function applyDefaultVideoHelperFallback() {
        if (!wallEl || !videoHelperAvailable) return;
        // Duas situacoes levam o helper a assumir o tile:
        //  1. o navegador nao decodifica HEVC em WebRTC (vale para todas as cameras);
        //  2. a camera nao tem WebRTC pronto - path nao registrado, on-demand frio.
        // O caso 2 nao passa por pollWebrtcDiagnostics, que so avalia cameras com
        // registro ok; sem isso o tile ficava preto para sempre, sem nem tentar.
        var forcarTodas = shouldDefaultToVideoHelper();
        var tiles = wallEl.querySelectorAll(".vms-tile[data-camera-id]");
        Array.prototype.forEach.call(tiles, function (tile) {
            var cameraId = tile.getAttribute("data-camera-id");
            if (!cameraId) return;
            if (!forcarTodas && isCameraWebrtcReady(cameraById.get(String(cameraId)))) return;
            var image = tile.querySelector(".vms-snapshot-fallback");
            if (image) {
                image.removeAttribute("data-helper-failed");
            }
            setTileSnapshotFallback(tile, true);
        });
    }

    function cameraStreamMode(slotIndex) {
        return "webrtc";
    }

    function cameraStreamUrl(camera, slotIndex) {
        if (!isCameraWebrtcReady(camera)) return "";
        var url = resolveWebrtcPlayerUrl(camera, "mosaico");
        if (url) probeWebrtcPlayer(camera, url, "mosaico");
        return url;
    }

    function cameraStreamAgeSeconds(camera) {
        var values = [
            camera && camera.latest_activity_age_seconds,
            camera && camera.last_processed_frame_at_age_seconds,
            camera && camera.preview_last_frame_age_seconds
        ];

        for (var i = 0; i < values.length; i += 1) {
            var value = Number(values[i]);
            if (Number.isFinite(value) && value >= 0) return value;
        }

        return null;
    }

    function cameraStreamState(camera, streamMode) {
        var health = cameraHealthStatus(camera);
        var age = cameraStreamAgeSeconds(camera);
        var previewHasFrame = !!(camera && camera.preview_has_frame);
        var player = cameraOperationalHealth(camera).player || {};
        var playerStatus = safeString(player.status).toLowerCase();
        var playerLabel = safeString(player.label);

        if (streamMode === "webrtc") {
            if (!camera || !camera.webrtc_enabled) {
                return { text: "WebRTC off", cls: "stream-bad" };
            }
            if (!camera.webrtc_registration_ok || !camera.webrtc_player_url) {
                return { text: "Sem rota WebRTC", cls: "stream-bad" };
            }
            if (health === "offline" || health === "reconnecting") {
                return { text: health === "offline" ?"Sem sinal" : "Reconectando", cls: "stream-bad" };
            }
            if (webrtcPureMode) {
                return { text: "WebRTC", cls: "stream-ok" };
            }
            if (playerStatus === "offline" || playerStatus === "down") {
                return { text: playerLabel || "Player falhou", cls: "stream-bad" };
            }
            if (playerStatus === "fallback") {
                return { text: playerLabel || "Snapshot", cls: "stream-warn" };
            }
            if (playerStatus === "stale" || playerStatus === "checking" || playerStatus === "unknown") {
                return { text: playerLabel || "Verificando", cls: "stream-warn" };
            }
            return { text: "WebRTC", cls: "stream-ok" };
        }

        if (streamMode === "snapshot") {
            if (health === "offline" || health === "reconnecting") {
                return { text: health === "offline" ?"Sem sinal" : "Reconectando", cls: "stream-bad" };
            }
            return { text: "Snapshot", cls: "stream-warn" };
        }

        if (health === "offline" || health === "reconnecting") {
            return { text: health === "offline" ?"Sem sinal" : "Reconectando", cls: "stream-bad" };
        }

        if (!previewHasFrame && age === null) {
            return { text: "Aguardando", cls: "stream-warn" };
        }

        if (age !== null && age > 12) {
            return { text: "Frame antigo", cls: "stream-warn" };
        }

        return { text: "Ao vivo", cls: "stream-ok" };
    }

    function isWallFullscreen() {
        return !!(document.fullscreenElement && document.fullscreenElement === wallEl);
    }

    async function requestWallFullscreen() {
        if (!wallEl || isWallFullscreen()) return;
        if (wallEl.requestFullscreen) {
            try {
                await wallEl.requestFullscreen();
            } catch (error) {}
        }
    }

    function enterFocusedCamera(cameraId, wasFullscreen) {
        if (!cameraId) return;

        if (!focusRestoreState) {
            focusRestoreState = {
                grid: selectedGrid,
                assignments: assignments.slice(),
                wasFullscreen: !!wasFullscreen
            };
        }

        selectedGrid = 1;
        updateLayoutKey();
        assignments = [String(cameraId)];
        setSelectedCamera(cameraId, { fetch: true });
        renderWall();
    }

    function restoreFocusedCamera() {
        if (!focusRestoreState) return false;

        selectedGrid = Number(focusRestoreState.grid || selectedGrid);
        updateLayoutKey();
        assignments = Array.isArray(focusRestoreState.assignments)
            ?focusRestoreState.assignments.slice()
            : clampAssignments([], { keepUnknown: true });
        focusRestoreState = null;
        ensureSelectedCameraSelection();
        renderWall();
        return true;
    }

    function handleVideoSingleClick(tile) {
        if (!tile) return;

        setSelectedCamera(tile.getAttribute("data-camera-id"), { fetch: true });

        if (isWallFullscreen()) {
            enterFocusedCamera(tile.getAttribute("data-camera-id"), true);
            return;
        }

        requestWallFullscreen();
    }

    function handleVideoDoubleClick(tile) {
        if (!tile) return;

        setSelectedCamera(tile.getAttribute("data-camera-id"), { fetch: true });

        if (focusRestoreState) {
            restoreFocusedCamera();
        } else {
            enterFocusedCamera(tile.getAttribute("data-camera-id"), false);
        }
    }

    function applyGridPreset(nextGrid) {
        var grid = Number(nextGrid);
        if (allowedGridValues.indexOf(grid) === -1) return;

        stopAllSequences(true);
        switchGridInPlace(grid, { loadStoredAssignments: true });
        renderWall();
    }

    function renderStats() {
        Object.keys(stats || {}).forEach(function (key) {
            var els = document.querySelectorAll('[data-stat="' + key + '"]');
            Array.prototype.forEach.call(els, function (el) {
                el.textContent = stats[key];
            });
        });
    }

    function renderGatewayHealth() {
        if (!gatewayHealthValueEl && !gatewayHealthDetailEl) return;

        var ok = !!(gatewayHealth && (gatewayHealth.ok === true || safeString(gatewayHealth.status).toLowerCase() === "ok"));
        var label = ok ?"Ativo" : "Indisponível";
        var service = safeString(gatewayHealth && gatewayHealth.service) || "camera-gateway";
        var timestamp = safeString(gatewayHealth && gatewayHealth.timestamp);

        if (gatewayHealthValueEl) {
            gatewayHealthValueEl.textContent = label;
            gatewayHealthValueEl.classList.toggle("alarm-refresh-ok", ok);
            gatewayHealthValueEl.classList.toggle("alarm-refresh-error", !ok);
        }

        if (gatewayHealthDetailEl) {
            gatewayHealthDetailEl.textContent = timestamp ?(service + " · " + timestamp) : service;
        }
    }

    function renderLibrary() {
        if (!libraryListEl) return;

        var term = safeString(cameraSearch).trim().toLowerCase();
        var filtered = (availableCameras || []).filter(function (camera) {
            if (libraryFilter === "visible" && !cameraHasVisibleImage(camera)) {
                return false;
            }
            if (libraryFilter === "alarm" && !camera.has_open_alarm) {
                return false;
            }
            if (!term) return true;
            var text = (safeString(camera.name) + " " + safeString(camera.site_name) + " " + safeString(camera.group_name)).toLowerCase();
            return text.indexOf(term) >= 0;
        });

        if (!filtered.length) {
            libraryListEl.innerHTML = '<div class="muted">Nenhuma câmera encontrada.</div>';
            return;
        }

        libraryListEl.innerHTML = filtered.map(function (camera) {
            var cameraId = String(camera.id);
            var hasAlarm = !!camera.highest_open_severity;
            var gatewayMeta = cameraGatewayMeta(camera);
            var opHealth = cameraOperationalHealth(camera);

            return ''
                + '<div class="camera-library-item" draggable="true" data-camera-id="' + escapeHtml(cameraId) + '">'
                + '    <div class="library-title">#' + escapeHtml(camera.id) + ' · ' + escapeHtml(camera.name) + '</div>'
                + '    <div class="library-meta">' + escapeHtml(camera.site_name || "Sem local") + ' · ' + escapeHtml(camera.group_name || "Sem grupo") + '</div>'
                + (gatewayMeta ?'    <div class="vms-gateway-meta">' + escapeHtml(gatewayMeta) + '</div>' : '')
                + '    <div class="library-badges">'
                +          operationalChip(opHealth.image, "Imagem")
                +          operationalChip(opHealth.analysis, "IA")
                +          (hasAlarm ?statusBadge("priority", camera.highest_open_severity) : "")
                + '    </div>'
                + '</div>';
        }).join("");
    }

    function updateLibraryFilterUi() {
        Array.prototype.forEach.call(libraryFilterButtons || [], function (button) {
            button.classList.toggle("active", button.getAttribute("data-library-filter") === libraryFilter);
        });
    }

    function setLeftPanel(panel) {
        currentLeftPanel = panel === "map" ?"map" : "library";
        if (libraryPanelEl) libraryPanelEl.hidden = currentLeftPanel !== "library";
        if (mapPanelEl) mapPanelEl.hidden = currentLeftPanel !== "map";
        Array.prototype.forEach.call(leftPanelButtons || [], function (button) {
            button.classList.toggle("active", button.getAttribute("data-left-panel") === currentLeftPanel);
        });
        if (currentLeftPanel === "map") {
            renderMapPins();
        }
    }

    function cameraMapPosition(camera, index, total) {
        var explicit = camera && (camera.mapPosition || camera.map_position);
        var rawX = explicit && (explicit.x !== undefined ?explicit.x : explicit[0]);
        var rawY = explicit && (explicit.y !== undefined ?explicit.y : explicit[1]);
        var x = Number(rawX);
        var y = Number(rawY);
        if (Number.isFinite(x) && Number.isFinite(y)) {
            return {
                x: Math.max(6, Math.min(94, x)),
                y: Math.max(6, Math.min(94, y))
            };
        }

        var columns = Math.max(2, Math.ceil(Math.sqrt(Math.max(1, total))));
        var row = Math.floor(index / columns);
        var col = index % columns;
        return {
            x: 14 + (col * (74 / Math.max(1, columns - 1))),
            y: 18 + (row * 18) % 68
        };
    }

    function isEditableShortcutTarget(target) {
        if (!target) return false;
        var tagName = safeString(target.tagName).toLowerCase();
        return tagName === "input"
            || tagName === "textarea"
            || tagName === "select"
            || target.isContentEditable
            || !!target.closest("[contenteditable='true']");
    }

    function clearDragState() {
        if (wallEl) {
            wallEl.classList.remove("drag-active");
            Array.prototype.forEach.call(wallEl.querySelectorAll(".dragging, .drop-target, .drag-over"), function (node) {
                node.classList.remove("dragging", "drop-target", "drag-over");
            });
        }
        if (libraryListEl) {
            Array.prototype.forEach.call(libraryListEl.querySelectorAll(".dragging"), function (node) {
                node.classList.remove("dragging");
            });
        }
    }

    function slotElementByIndex(slotIndex) {
        if (!wallEl || !Number.isFinite(Number(slotIndex))) return null;
        return wallEl.querySelector('[data-slot-index="' + Number(slotIndex) + '"]');
    }

    function dragSlotFromEvent(event) {
        if (!wallEl || !event) return null;

        var target = event.target && event.target.closest ?event.target.closest(".vms-slot") : null;
        if (target && wallEl.contains(target)) return target;

        if (typeof event.composedPath === "function") {
            var path = event.composedPath();
            for (var i = 0; i < path.length; i += 1) {
                var node = path[i];
                if (node && node.classList && node.classList.contains("vms-slot") && wallEl.contains(node)) {
                    return node;
                }
            }
        }

        if (Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) {
            var pointTarget = document.elementFromPoint(event.clientX, event.clientY);
            var pointSlot = pointTarget && pointTarget.closest ?pointTarget.closest(".vms-slot") : null;
            if (pointSlot && wallEl.contains(pointSlot)) return pointSlot;
        }

        return null;
    }

    function scrollSlotIntoView(slotIndex) {
        var slot = slotElementByIndex(slotIndex);
        if (!slot) return false;
        slot.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
        wallEl.dataset.selectedSlotIndex = String(slotIndex);
        return true;
    }

    function ensureCameraVisibleOnStage(cameraId, options) {
        var normalizedId = safeString(cameraId);
        var allowAssign = !!(options && options.allowAssign);
        var shouldScroll = !(options && options.scroll === false);
        if (!normalizedId || !cameraById.has(normalizedId)) {
            return { ok: false, reason: "camera_missing" };
        }

        setSelectedCamera(normalizedId, { fetch: true });

        var existingSlot = assignments.findIndex(function (id) {
            return String(id || "") === normalizedId;
        });
        if (existingSlot >= 0) {
            if (shouldScroll) scrollSlotIntoView(existingSlot);
            return { ok: true, slotIndex: existingSlot, reason: "existing" };
        }

        if (!allowAssign || layoutLocked) {
            return { ok: false, reason: layoutLocked ?"layout_locked" : "not_assigned" };
        }

        var emptySlot = assignments.findIndex(function (id) { return !id; });
        if (emptySlot < 0) {
            return { ok: false, reason: "grid_full" };
        }

        replaceCameraInSlot(emptySlot, normalizedId, null);
        if (shouldScroll) scrollSlotIntoView(emptySlot);
        return { ok: true, slotIndex: emptySlot, reason: "assigned" };
    }

    function alarmEventUrl(alarm) {
        if (alarm && alarm.event_url) return alarm.event_url;
        if (alarm && alarm.id) {
            var params = new URLSearchParams();
            var cameraId = alarmCameraId(alarm);
            if (cameraId) params.set("camera_id", cameraId);
            if (alarm.status) params.set("status", String(alarm.status));
            return "/events" + (params.toString() ?"?" + params.toString() : "");
        }
        return alarm && alarm.camera_url ?alarm.camera_url : "/events";
    }

    function renderMapPins() {
        if (!mapCanvasEl) return;
        var cameras = availableCameras || [];
        if (mapCountEl) {
            mapCountEl.textContent = cameras.length + " cams";
        }
        if (!cameras.length) {
            mapCanvasEl.innerHTML = '<div class="muted" style="position:absolute; inset:0; display:grid; place-items:center;">Sem cameras carregadas.</div>';
            return;
        }

        mapCanvasEl.innerHTML = cameras.map(function (camera, index) {
            var pos = cameraMapPosition(camera, index, cameras.length);
            var severity = safeString(camera && camera.highest_open_severity).toLowerCase();
            var isCritical = severity === "critical";
            var isWarning = !!(camera && camera.has_open_alarm) && !isCritical;
            var health = cameraHealthStatus(camera);
            var offline = health === "offline" || health === "stopped" || !cameraHasVisibleImage(camera);
            var classes = "sunorus-map-pin" + (isCritical ?" is-critical" : "") + (isWarning ?" is-warning" : "") + (offline ?" is-offline" : "");
            var title = "#" + safeString(camera.id) + " " + safeString(camera.name)
                + " | " + cameraHealthLabel(camera)
                + " | " + safeString(camera.site_name || "Sem local")
                + " | " + safeString(camera.group_name || "Sem grupo");
            return '<button type="button" class="' + classes + '" data-camera-id="' + escapeHtml(camera.id) + '" style="left:' + pos.x.toFixed(2) + '%; top:' + pos.y.toFixed(2) + '%" title="' + escapeHtml(title) + '">' + escapeHtml(String(index + 1).padStart(2, "0")) + '</button>';
        }).join("");
    }

    function renderGridIcons() {
        var shapes = {
            "1": [1, 1],
            "2": [2, 1],
            "4": [2, 2],
            "6": [3, 2],
            "8": [4, 2],
            "9": [3, 3],
            "12": [4, 3],
            "16": [4, 4],
            "25": [5, 5]
        };

        document.querySelectorAll("[data-grid-icon]").forEach(function (icon) {
            var shape = shapes[String(icon.getAttribute("data-grid-icon"))] || [1, 1];
            var cols = shape[0];
            var rows = shape[1];
            var cells = [];
            icon.style.gridTemplateColumns = "repeat(" + cols + ", 1fr)";
            icon.style.gridTemplateRows = "repeat(" + rows + ", 1fr)";
            for (var index = 0; index < cols * rows; index += 1) {
                cells.push('<span class="vms-grid-icon-cell"></span>');
            }
            icon.innerHTML = cells.join("");
        });
    }

    function overlayFrameSize(camera) {
        var width = Number(camera && camera.source_frame_width);
        var height = Number(camera && camera.source_frame_height);

        if (!Number.isFinite(width) || width <= 0) width = 16;
        if (!Number.isFinite(height) || height <= 0) height = 9;

        return { width: width, height: height };
    }

    function overlayUnits(frameSize) {
        var minSize = Math.max(1, Math.min(frameSize.width, frameSize.height));
        return {
            stroke: Math.max(0.028, minSize * 0.0036),
            thinStroke: Math.max(0.018, minSize * 0.0018),
            pointRadius: Math.max(0.045, minSize * 0.006),
            fontSize: Math.max(0.22, minSize * 0.027),
            labelHeight: Math.max(0.34, minSize * 0.04),
            labelPadX: Math.max(0.08, minSize * 0.012),
            labelOffset: Math.max(0.10, minSize * 0.018)
        };
    }

    function normalizeOverlayPoint(point, frameSize) {
        if (!point || typeof point !== "object") return null;

        var rawX = Array.isArray(point) ?point[0] : point.x;
        var rawY = Array.isArray(point) ?point[1] : point.y;
        var x = Number(rawX);
        var y = Number(rawY);

        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;

        var normalizedX = Math.max(0, Math.min(1, x));
        var normalizedY = Math.max(0, Math.min(1, y));

        return {
            x: normalizedX * frameSize.width,
            y: normalizedY * frameSize.height
        };
    }

    function overlayLinePoint(point, frameSize) {
        return normalizeOverlayPoint(point, frameSize);
    }

    function renderCameraOverlay(camera) {
        var overlay = camera && camera.monitor_overlay ?camera.monitor_overlay : null;
        if (!overlay || !overlay.enabled) return "";

        var frameSize = overlayFrameSize(camera);
        var units = overlayUnits(frameSize);
        var roiPoints = Array.isArray(overlay.roi_points)
            ?overlay.roi_points.map(function (point) {
                return normalizeOverlayPoint(point, frameSize);
            }).filter(Boolean)
            : [];
        var line = overlay.line || null;
        var lineStart = line ?overlayLinePoint(line.start, frameSize) : null;
        var lineEnd = line ?overlayLinePoint(line.end, frameSize) : null;

        if (roiPoints.length < 3 && (!lineStart || !lineEnd)) return "";

        var chunks = [
            '<svg class="vms-overlay-svg" viewBox="0 0 ' + frameSize.width + ' ' + frameSize.height + '" preserveAspectRatio="none" aria-hidden="true">'
        ];

        if (roiPoints.length >= 3) {
            chunks.push(
                '<polygon class="vms-overlay-roi" points="' +
                roiPoints.map(function (point) {
                    return point.x.toFixed(2) + ',' + point.y.toFixed(2);
                }).join(' ') +
                '" stroke-width="' + units.stroke.toFixed(3) + '"></polygon>'
            );

            if (overlay.roi_name) {
                chunks.push(
                    '<text class="vms-overlay-label" font-size="' + units.fontSize.toFixed(3) + '" stroke-width="' + units.stroke.toFixed(3) + '" x="' + roiPoints[0].x.toFixed(2) + '" y="' + Math.max(units.fontSize, roiPoints[0].y - units.labelOffset).toFixed(2) + '">' +
                    escapeHtml(overlay.roi_name) +
                    '</text>'
                );
            }
        }

        if (lineStart && lineEnd) {
            chunks.push(
                '<line class="vms-overlay-line" stroke-width="' + units.stroke.toFixed(3) + '" x1="' + lineStart.x.toFixed(2) + '" y1="' + lineStart.y.toFixed(2) + '" x2="' + lineEnd.x.toFixed(2) + '" y2="' + lineEnd.y.toFixed(2) + '"></line>' +
                '<circle class="vms-overlay-line-end" stroke-width="' + units.thinStroke.toFixed(3) + '" cx="' + lineStart.x.toFixed(2) + '" cy="' + lineStart.y.toFixed(2) + '" r="' + units.pointRadius.toFixed(3) + '"></circle>' +
                '<circle class="vms-overlay-line-end" stroke-width="' + units.thinStroke.toFixed(3) + '" cx="' + lineEnd.x.toFixed(2) + '" cy="' + lineEnd.y.toFixed(2) + '" r="' + units.pointRadius.toFixed(3) + '"></circle>'
            );
        }

        chunks.push('</svg>');
        return chunks.join("");
    }

    function renderCameraBoxes(camera) {
        if (!cameraCanRenderBoxes(camera)) return "";

        var boxes = Array.isArray(camera && camera.monitor_boxes) ?camera.monitor_boxes : [];
        if (!boxes.length) return "";

        var frameSize = overlayFrameSize(camera);
        var units = overlayUnits(frameSize);
        var isStale = !!(camera && camera.monitor_boxes_stale);
        var chunks = [
            '<svg class="vms-box-svg' + (isStale ?' vms-box-svg-stale' : '') + '" viewBox="0 0 ' + frameSize.width + ' ' + frameSize.height + '" preserveAspectRatio="none" aria-hidden="true">'
        ];

        boxes.slice(0, 30).forEach(function (box) {
            var bbox = box && Array.isArray(box.bbox) ?box.bbox : null;
            if (!bbox || bbox.length !== 4) return;

            var x1 = Number(bbox[0]);
            var y1 = Number(bbox[1]);
            var x2 = Number(bbox[2]);
            var y2 = Number(bbox[3]);
            if (![x1, y1, x2, y2].every(Number.isFinite)) return;

            x1 = Math.max(0, Math.min(frameSize.width, x1));
            y1 = Math.max(0, Math.min(frameSize.height, y1));
            x2 = Math.max(0, Math.min(frameSize.width, x2));
            y2 = Math.max(0, Math.min(frameSize.height, y2));

            var width = Math.max(1, x2 - x1);
            var height = Math.max(1, y2 - y1);
            var confidence = Number(box.confidence);
            var label = box.label || "person";
            if (box.track_id !== null && box.track_id !== undefined && Number(box.track_id) >= 0) {
                label += " #" + String(box.track_id);
            }
            if (Number.isFinite(confidence)) {
                label += " " + confidence.toFixed(2);
            }
            if (box.visual_status === "revalidated") {
                label += " IA";
            }

            var labelY = Math.max(units.fontSize + units.labelPadX, y1 - units.labelOffset);
            var labelWidth = Math.max(units.fontSize * 4.8, Math.min(frameSize.width * 0.32, units.labelPadX * 2 + label.length * units.fontSize * 0.55));

            chunks.push(
                '<rect class="vms-detection-box" stroke-width="' + units.stroke.toFixed(3) + '" x="' + x1.toFixed(2) + '" y="' + y1.toFixed(2) + '" width="' + width.toFixed(2) + '" height="' + height.toFixed(2) + '"></rect>' +
                '<rect class="vms-detection-label-bg" stroke-width="' + units.thinStroke.toFixed(3) + '" x="' + x1.toFixed(2) + '" y="' + (labelY - units.fontSize - units.labelPadX * 0.45).toFixed(2) + '" width="' + labelWidth.toFixed(2) + '" height="' + units.labelHeight.toFixed(2) + '" rx="' + (units.labelHeight * 0.18).toFixed(2) + '"></rect>' +
                '<text class="vms-detection-label" font-size="' + units.fontSize.toFixed(3) + '" x="' + (x1 + units.labelPadX).toFixed(2) + '" y="' + labelY.toFixed(2) + '">' + escapeHtml(label) + '</text>'
            );
        });

        chunks.push('</svg>');
        if (isStale) {
            chunks.push('<span class="vms-box-stale-badge">IA atrasada</span>');
        }
        return chunks.join("");
    }

    function buildCameraTile(camera, slotIndex) {
        if (webrtcPureMode) {
            return buildPureWebrtcTile(camera, slotIndex);
        }

        var severity = camera.highest_open_severity || "";
        var streamMode = cameraStreamMode(slotIndex);
        var streamUrl = cameraStreamUrl(camera, slotIndex);
        var isWebrtcStream = streamMode === "webrtc";
        var hasWebrtcFrame = isWebrtcStream && isCameraWebrtcReady(camera) && !!streamUrl;
        var detailUrl = camera.detail_url || ("/cameras/" + camera.id);
        var streamState = cameraStreamState(camera, streamMode);
        var gatewayMeta = cameraGatewayMeta(camera);
        var healthStatus = cameraHealthStatus(camera);
        var healthLabel = cameraHealthLabel(camera);
        var opHealthChips = cameraOperationalChips(camera);
        var modeValue = cameraModeValue(camera);
        var alarmTag = severity
            ?'<div class="vms-alarm-tag vms-technical-detail severity-' + escapeHtml(severity) + '">alarme ' + escapeHtml(severity) + '</div>'
            : "";
        var severityClass = severity ?" severity-" + safeString(severity).toLowerCase() : "";
        var liveVideoClass = cameraCanRenderBoxes(camera) ?"" : " vms-no-live-video";
        var slotNumber = slotIndex + 1;
        var paddedSlot = String(slotNumber).padStart(2, "0");

        var fitOverride = localFitOverrides[slotIndex] || "";
        var fitOverrideClass = "";
        if (fitOverride === "cover") {
            fitOverrideClass = " vms-local-fit-cover";
        } else if (fitOverride === "contain") {
            fitOverrideClass = " vms-local-fit-contain";
        }

        return ''
            + '<article'
            + ' class="vms-tile is-clickable' + escapeHtml(severityClass + liveVideoClass + fitOverrideClass) + (cameraConnectionUnavailable(camera) ?" vms-connection-unavailable" : "") + (canControlCamera ?"" : " vms-camera-read-only") + '"'
            + ' draggable="true"'
            + ' data-slot-index="' + slotIndex + '"'
            + ' data-camera-id="' + escapeHtml(camera.id) + '"'
            + ' data-detail-url="' + escapeHtml(detailUrl) + '"'
            + ' data-fit-override="' + escapeHtml(fitOverride) + '"'
            + (gatewayMeta ?' title="' + escapeHtml(gatewayMeta) + '"' : '')
            + '>'
            + '    <div class="vms-tile-head">'
            + '        <div class="vms-tile-head-left">'
            + '            <div class="vms-slot-pill">' + paddedSlot + '</div>'
            + '            <div>'
            + '                <div class="vms-tile-title">' + escapeHtml(camera.name) + '</div>'
            + '                <div class="vms-tile-meta">' + escapeHtml(camera.site_name || "Sem local") + ' · ' + escapeHtml(camera.group_name || "Sem grupo") + '</div>'
            + '            </div>'
            + '        </div>'
            + '        <div class="vms-tile-badges">'
        +              ptzTileBadge(camera)
        +              opHealthChips
        + '            <span class="vms-status-line"><span class="vms-status-dot health-' + escapeHtml(healthStatus) + '"></span><span class="vms-technical-detail">' + escapeHtml(healthLabel) + '</span></span>'
        + '            <span class="vms-technical-detail">' + statusBadge("priority", camera.camera_priority || "medium") + '</span>'
        + '            <button type="button" class="vms-tile-bell-btn" data-camera-id="' + escapeHtml(camera.id) + '" title="Alertas recentes" style="background:none; border:none; color:var(--text-secondary); cursor:pointer; font-size:14px; padding:2px; line-height:1; vertical-align:middle; margin-left:4px;">🔔</button>'
        + '        </div>'
        + '    </div>'
        + '    <div class="vms-mini-events-popover" style="display:none; position:absolute; top:42px; right:10px; z-index:200; width:280px; background:rgba(11, 17, 28, 0.94); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px); border:1px solid rgba(255,255,255,0.12); border-radius:12px; box-shadow:0 12px 30px rgba(0,0,0,0.55); padding:12px; color:var(--text-primary); text-align:left; font-family:sans-serif;">'
        + '        <div style="font-weight:700; font-size:13px; border-bottom:1px solid rgba(255,255,255,0.08); padding-bottom:6px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center; line-height:1.2;">'
        + '            <span>Alertas Recentes</span>'
        + '            <button type="button" class="vms-mini-events-close" style="background:transparent; border:none; color:var(--text-muted); cursor:pointer; font-size:16px; padding:0 4px; line-height:1;">&times;</button>'
        + '        </div>'
        + '        <div class="vms-mini-events-content" style="font-size:12px; display:flex; flex-direction:column; gap:8px; line-height:1.2;">'
        + '            <div style="text-align:center; padding:10px; color:var(--text-muted);">Carregando...</div>'
        + '        </div>'
        + '    </div>'
        + (gatewayMeta ?'    <div class="vms-gateway-meta">' + escapeHtml(gatewayMeta) + '</div>' : '')
            + '    <div class="vms-video-wrap">'
            + '        <div class="vms-tile-hud">'
            + '            <div class="vms-hud-left">'
            + '                <button type="button" class="vms-hud-btn" data-action="toggle-aspect" title="Alternar proporção (Preencher/Enquadrar)">↔</button>'
            + '                <button type="button" class="vms-hud-btn" data-action="maximize-slot" title="Maximizar / Restaurar slot">🔍</button>'
            + (hasWebrtcFrame ?'                <button type="button" class="vms-hud-btn vms-audio-btn' + (String(camera.id) === String(audioCameraId) ?' is-audio-on' : '') + '" data-action="toggle-audio" data-camera-id="' + escapeHtml(camera.id) + '" title="' + (String(camera.id) === String(audioCameraId) ?'Audio ligado - clique para silenciar' : 'Escutar audio desta camera') + '" aria-label="Escutar audio desta camera">' + (String(camera.id) === String(audioCameraId) ?'🔊' : '🔇') + '</button>' : '')
            + '            </div>'
            + '            <div class="vms-hud-right">'
            + '                <button type="button" class="btn btn-small vms-icon-btn vms-ai-play-btn' + (camera.is_running ?' is-running' : '') + '" data-action="motion" data-camera-id="' + escapeHtml(camera.id) + '" title="' + (camera.is_running ?'IA em execução' : 'Iniciar IA') + '" aria-label="' + (camera.is_running ?'IA em execução' : 'Iniciar IA') + '"' + (camera.is_running ?' disabled' : '') + '>▶</button>'
            + '                <button type="button" class="btn btn-danger btn-small vms-icon-btn" data-action="stop" data-camera-id="' + escapeHtml(camera.id) + '" title="Parar" aria-label="Parar">■</button>'
            + '                <a class="btn btn-secondary btn-small vms-icon-btn" href="' + escapeHtml(detailUrl) + '" title="Configurações / Detalhes">⚙</a>'
            + '                <button type="button" class="btn btn-danger btn-small vms-icon-btn" data-action="remove-slot" data-slot-index="' + slotIndex + '" title="Remover da grade">×</button>'
            + '            </div>'
            + '        </div>'
            + (hasWebrtcFrame
                ?'        <iframe'
                    + '            class="vms-webrtc-frame is-active"'
                    + '            src="' + escapeHtml(streamUrl) + '"'
                    + '            data-stream-base="' + escapeHtml(streamUrl) + '"'
                    + '            data-stream-mode="webrtc"'
                    + '            allow="autoplay; fullscreen; microphone; camera"'
                    + '            loading="eager"'
                    + '            referrerpolicy="no-referrer"'
                    + '            scrolling="no"'
                    + '        ></iframe>'
                : ''
            )
            + (!hasWebrtcFrame ?'<div class="vms-webrtc-placeholder">WebRTC indisponivel para esta camera</div>' : '')
            + '        <img class="vms-snapshot-fallback"'
            + ' data-snapshot-base="/cameras/' + escapeHtml(camera.id) + '/stream"'
            + ' alt="" aria-hidden="true" loading="lazy">'
            + '        <div class="vms-overlay-layer">' + renderCameraOverlay(camera) + '</div>'
            + '        <div class="vms-box-layer">' + renderCameraBoxes(camera) + '</div>'
            + '        <div class="vms-video-hitbox" aria-hidden="true"></div>'
            + '        <div class="vms-fps-badge vms-technical-detail"><span>FPS</span><strong class="camera-fps-value">' + escapeHtml(cameraFpsValue(camera)) + '</strong></div>'
            + '        <div class="vms-stream-state vms-technical-detail ' + escapeHtml(streamState.cls) + '">' + escapeHtml(streamState.text) + '</div>'
            +          alarmTag
            + '    </div>'
            + '    <div class="vms-tile-actions">'
            + '        <div class="vms-action-main">'
            + '            <button type="button" class="btn btn-small vms-icon-btn vms-ai-play-btn' + (camera.is_running ?' is-running' : '') + '" data-action="motion" data-camera-id="' + escapeHtml(camera.id) + '" title="' + (camera.is_running ?'IA em execução' : 'Iniciar IA') + '" aria-label="' + (camera.is_running ?'IA em execução' : 'Iniciar IA') + '"' + (camera.is_running ?' disabled' : '') + '>▶</button>'
            + '            <button type="button" class="btn btn-danger btn-small vms-icon-btn" data-action="stop" data-camera-id="' + escapeHtml(camera.id) + '" title="Parar" aria-label="Parar">■</button>'
            + '        </div>'
            + '        <div class="vms-tile-menu">'
            + '            <button type="button" class="btn btn-secondary btn-small vms-icon-btn" data-action="menu-toggle" title="Ações" aria-label="Ações">⋯</button>'
            + '            <div class="vms-tile-menu-list">'
            + '                <a class="vms-menu-item" href="' + escapeHtml(detailUrl) + '">Detalhes</a>'
            + '                <button type="button" class="vms-menu-item" data-action="remove-slot" data-slot-index="' + slotIndex + '">Remover</button>'
            + '            </div>'
            + '        </div>'
            + '    </div>'
            + '</article>';
    }

    function buildPureWebrtcTile(camera, slotIndex) {
        var streamUrl = cameraStreamUrl(camera, slotIndex);
        var hasWebrtcFrame = isCameraWebrtcReady(camera) && !!streamUrl;
        var detailUrl = camera.detail_url || ("/cameras/" + camera.id);
        var slotNumber = slotIndex + 1;
        var paddedSlot = String(slotNumber).padStart(2, "0");
        var healthStatus = cameraHealthStatus(camera);
        var healthLabel = cameraHealthLabel(camera);
        var fitOverride = localFitOverrides[slotIndex] || "";
        var fitOverrideClass = "";

        if (fitOverride === "cover") {
            fitOverrideClass = " vms-local-fit-cover";
        } else if (fitOverride === "contain") {
            fitOverrideClass = " vms-local-fit-contain";
        }

        return ''
            + '<article'
            + ' class="vms-tile vms-pure-webrtc-tile is-clickable' + escapeHtml(fitOverrideClass) + (cameraConnectionUnavailable(camera) ?" vms-connection-unavailable" : "") + (canControlCamera ?"" : " vms-camera-read-only") + '"'
            + ' draggable="true"'
            + ' data-slot-index="' + slotIndex + '"'
            + ' data-camera-id="' + escapeHtml(camera.id) + '"'
            + ' data-detail-url="' + escapeHtml(detailUrl) + '"'
            + ' data-fit-override="' + escapeHtml(fitOverride) + '"'
            + '>'
            + '    <div class="vms-pure-webrtc-head vms-tile-head">'
            + '        <div class="vms-slot-pill">' + paddedSlot + '</div>'
            + '        <div class="vms-pure-webrtc-title-wrap">'
            + '            <div class="vms-tile-title">' + escapeHtml(camera.name) + '</div>'
            + '            <div class="vms-tile-meta">' + escapeHtml(camera.site_name || "Sem local") + ' · ' + escapeHtml(camera.group_name || "Sem grupo") + '</div>'
            + '        </div>'
            + '        <span class="vms-status-line vms-pure-status-line"><span class="vms-status-dot health-' + escapeHtml(healthStatus) + '"></span><span class="vms-technical-detail">' + escapeHtml(healthLabel) + '</span></span>'
            +          ptzTileBadge(camera)
            + '    </div>'
            + '    <div class="vms-pure-webrtc-frame-wrap">'
            + '        <div class="vms-tile-hud">'
            + '            <div class="vms-hud-left">'
            + '                <button type="button" class="vms-hud-btn" data-action="toggle-aspect" title="Alternar proporcao (Preencher/Enquadrar)">↔</button>'
            + '                <button type="button" class="vms-hud-btn" data-action="maximize-slot" title="Maximizar / Restaurar slot">🔍</button>'
            + (hasWebrtcFrame ?'                <button type="button" class="vms-hud-btn vms-audio-btn' + (String(camera.id) === String(audioCameraId) ?' is-audio-on' : '') + '" data-action="toggle-audio" data-camera-id="' + escapeHtml(camera.id) + '" title="' + (String(camera.id) === String(audioCameraId) ?'Audio ligado - clique para silenciar' : 'Escutar audio desta camera') + '" aria-label="Escutar audio desta camera">' + (String(camera.id) === String(audioCameraId) ?'🔊' : '🔇') + '</button>' : '')
            + '            </div>'
            + '            <div class="vms-hud-right">'
            + '                <button type="button" class="btn btn-small vms-icon-btn vms-ai-play-btn' + (camera.is_running ?' is-running' : '') + '" data-action="motion" data-camera-id="' + escapeHtml(camera.id) + '" title="' + (camera.is_running ?'IA em execucao' : 'Iniciar IA') + '" aria-label="' + (camera.is_running ?'IA em execucao' : 'Iniciar IA') + '"' + (camera.is_running ?' disabled' : '') + '>▶</button>'
            + '                <button type="button" class="btn btn-danger btn-small vms-icon-btn" data-action="stop" data-camera-id="' + escapeHtml(camera.id) + '" title="Parar" aria-label="Parar">■</button>'
            + '                <a class="btn btn-secondary btn-small vms-icon-btn" href="' + escapeHtml(detailUrl) + '" title="Configuracoes / Detalhes">⚙</a>'
            + '                <button type="button" class="btn btn-danger btn-small vms-icon-btn" data-action="remove-slot" data-slot-index="' + slotIndex + '" title="Remover da grade">×</button>'
            + '            </div>'
            + '        </div>'
            + (hasWebrtcFrame
                ?'        <iframe'
                    + '            class="vms-webrtc-frame is-active"'
                    + '            src="' + escapeHtml(streamUrl) + '"'
                    + '            data-stream-base="' + escapeHtml(streamUrl) + '"'
                    + '            data-stream-mode="webrtc"'
                    + '            allow="autoplay; fullscreen; microphone; camera; picture-in-picture"'
                    + '            loading="eager"'
                    + '            referrerpolicy="no-referrer"'
                    + '            scrolling="no"'
                    + '        ></iframe>'
                : '<div class="vms-webrtc-placeholder">WebRTC indisponivel para esta camera</div>'
            )
            + '        <div class="vms-overlay-layer">' + renderCameraOverlay(camera) + '</div>'
            + '        <div class="vms-box-layer">' + renderCameraBoxes(camera) + '</div>'
            + '    </div>'
            + '</article>';
    }

    function buildWallFingerprint() {
        return [
            String(selectedGrid),
            wallLayoutClass(),
            effectiveLayoutMode() === "sandbox" ?JSON.stringify(sandboxTileSpans) : "",
            webrtcPureMode ?"pure" : "overlay",
            (assignments || []).map(function (value, index) {
                if (!value) return "_";
                var camera = cameraById.get(String(value));
                if (!camera) return String(value);
                var mode = cameraStreamMode(index);
                return [String(value), mode, cameraStreamUrl(camera, index)].join(":");
            }).join("|")
        ].join("::");
    }

    function findReusableTile(cameraId) {
        if (!wallEl) return null;
        var normalizedId = safeString(cameraId);
        if (!normalizedId) return null;

        var tiles = wallEl.querySelectorAll(".vms-tile[data-camera-id]");
        for (var index = 0; index < tiles.length; index += 1) {
            if (tiles[index].getAttribute("data-camera-id") === normalizedId) {
                return tiles[index];
            }
        }
        return null;
    }

    function updateTileSlotIndex(tile, slotIndex) {
        if (!tile) return;
        tile.setAttribute("data-slot-index", String(slotIndex));
        Array.prototype.forEach.call(tile.querySelectorAll('[data-action="remove-slot"]'), function (button) {
            button.setAttribute("data-slot-index", String(slotIndex));
        });
    }

    function buildSlotChrome(slotIndex, isSandbox) {
        var slotStyle = "";
        var spanControlsHtml = "";
        var resizeHandleHtml = "";

        if (isSandbox) {
            var span = getTileSpan(slotIndex);
            slotStyle = "grid-column: span " + span.colSpan + "; grid-row: span " + span.rowSpan + ";";
            spanControlsHtml = '<div class="vms-sandbox-span-controls">'
                + '<button type="button" class="vms-span-btn" data-slot-index="' + slotIndex + '" data-span-action="col-minus" title="Diminuir largura">-C</button>'
                + '<span class="vms-sandbox-dim-badge">' + span.colSpan + 'x' + span.rowSpan + '</span>'
                + '<button type="button" class="vms-span-btn" data-slot-index="' + slotIndex + '" data-span-action="col-plus" title="Aumentar largura">+C</button>'
                + '<button type="button" class="vms-span-btn" data-slot-index="' + slotIndex + '" data-span-action="row-minus" title="Diminuir altura">-L</button>'
                + '<button type="button" class="vms-span-btn" data-slot-index="' + slotIndex + '" data-span-action="row-plus" title="Aumentar altura">+L</button>'
                + '</div>';
            resizeHandleHtml = '<div class="vms-sandbox-resize-handle" data-slot-index="' + slotIndex + '" title="Arrastar para redimensionar slot"></div>';
        }

        return {
            slotStyle: slotStyle,
            spanControlsHtml: spanControlsHtml,
            resizeHandleHtml: resizeHandleHtml,
        };
    }

    function buildSlotElement(slotIndex, camera, isSandbox) {
        var slot = document.createElement("div");
        slot.className = "vms-slot" + (isSandbox ?" vms-sandbox-slot" : "");
        slot.setAttribute("data-slot-index", String(slotIndex));

        var chrome = buildSlotChrome(slotIndex, isSandbox);
        if (chrome.slotStyle) {
            slot.style.cssText = chrome.slotStyle;
        }

        if (!camera) {
            slot.innerHTML = ''
                + '<div class="vms-slot-label">' + String(slotIndex + 1).padStart(2, "0") + '</div>'
                + '<div class="vms-empty-slot">Slot ' + (slotIndex + 1) + ' · arraste uma câmera aqui</div>'
                + chrome.spanControlsHtml
                + chrome.resizeHandleHtml;
            return slot;
        }

        var existingTile = findReusableTile(camera.id);
        if (existingTile) {
            updateTileSlotIndex(existingTile, slotIndex);
            slot.appendChild(existingTile);
        } else {
            var tempWrap = document.createElement("div");
            tempWrap.innerHTML = buildCameraTile(camera, slotIndex);
            var newTile = tempWrap.firstElementChild;
            if (newTile) {
                updateTileSlotIndex(newTile, slotIndex);
                slot.appendChild(newTile);
            }
        }

        if (chrome.spanControlsHtml || chrome.resizeHandleHtml) {
            var chromeWrap = document.createElement("div");
            chromeWrap.innerHTML = chrome.spanControlsHtml + chrome.resizeHandleHtml;
            while (chromeWrap.firstChild) {
                slot.appendChild(chromeWrap.firstChild);
            }
        }

        return slot;
    }

    function syncWallTileMetadata() {
        if (!wallEl) return;

        var slots = wallEl.querySelectorAll(".vms-slot");
        Array.prototype.forEach.call(slots, function (slot) {
            var slotIndex = Number(slot.getAttribute("data-slot-index"));
            if (Number.isNaN(slotIndex)) return;

            var cameraId = assignments[slotIndex];
            var camera = cameraId ?cameraById.get(String(cameraId)) : null;
            if (!camera) return;

            var tile = slot.querySelector(".vms-tile");
            if (!tile) return;

            var titleEl = tile.querySelector(".vms-tile-title");
            var metaEl = tile.querySelector(".vms-tile-meta");
            var gatewayMetaEl = tile.querySelector(".vms-gateway-meta");
            var slotEl = tile.querySelector(".vms-slot-pill");
            var badgesEl = tile.querySelector(".vms-tile-badges");
            var pureStatusLineEl = tile.querySelector(".vms-pure-status-line");
            var healthEl = tile.querySelector(".vms-health-pill");
            var fpsEl = tile.querySelector(".vms-fps-badge .camera-fps-value");
            var aiPlayButtons = tile.querySelectorAll(".vms-ai-play-btn");
            var webrtcFrameEl = tile.querySelector(".vms-webrtc-frame");
            var videoWrapEl = tile.querySelector(".vms-video-wrap");
            var overlayLayerEl = tile.querySelector(".vms-overlay-layer");
            var boxLayerEl = tile.querySelector(".vms-box-layer");
            var streamStateEl = tile.querySelector(".vms-stream-state");
            var alarmEl = tile.querySelector(".vms-alarm-tag");
            var health = cameraHealthStatus(camera);
            var healthLabel = cameraHealthLabel(camera);
            var severity = camera.highest_open_severity || "";
            var streamMode = cameraStreamMode(slotIndex);
            var streamUrl = cameraStreamUrl(camera, slotIndex);
            var streamState = cameraStreamState(camera, streamMode);
            var gatewayMeta = cameraGatewayMeta(camera);
            var aspectRatio = cameraStreamAspectRatio(camera);
            var opHealthChips = cameraOperationalChips(camera);

            updateTileSlotIndex(tile, slotIndex);
            tile.classList.remove("severity-critical", "severity-high", "severity-warning", "severity-medium", "severity-low", "severity-info");
            if (severity) {
                tile.classList.add("severity-" + safeString(severity).toLowerCase());
            }
            tile.classList.toggle("vms-no-live-video", !cameraCanRenderBoxes(camera));
            tile.classList.toggle("vms-connection-unavailable", cameraConnectionUnavailable(camera));

            // Sync local fit overrides
            var fitOverride = localFitOverrides[slotIndex] || "";
            tile.classList.toggle("vms-local-fit-cover", fitOverride === "cover");
            tile.classList.toggle("vms-local-fit-contain", fitOverride === "contain");
            tile.setAttribute("data-fit-override", fitOverride);

            if (titleEl) {
                titleEl.textContent = safeString(camera.name);
            }

            if (metaEl) {
                metaEl.textContent = safeString(camera.site_name || "Sem local") + " · " + safeString(camera.group_name || "Sem grupo");
            }

            if (gatewayMetaEl) {
                gatewayMetaEl.textContent = gatewayMeta;
                gatewayMetaEl.style.display = gatewayMeta ?"" : "none";
            }

            if (slotEl) {
                slotEl.textContent = String(slotIndex + 1).padStart(2, "0");
            }

            if (badgesEl) {
                badgesEl.innerHTML = ''
                    + opHealthChips
                    + '<span class="vms-status-line"><span class="vms-status-dot health-' + escapeHtml(health) + '"></span><span class="vms-technical-detail">' + escapeHtml(healthLabel) + '</span></span>'
                    + '<span class="vms-technical-detail">' + statusBadge("priority", camera.camera_priority || "medium") + '</span>';
            }

            if (!badgesEl && pureStatusLineEl) {
                pureStatusLineEl.innerHTML = '<span class="vms-status-dot health-' + escapeHtml(health) + '"></span><span class="vms-technical-detail">' + escapeHtml(healthLabel) + '</span>';
            }

            if (healthEl) {
                healthEl.className = "vms-health-pill health-" + escapeHtml(health);
                healthEl.textContent = health;
            }

            if (fpsEl) {
                fpsEl.textContent = cameraFpsValue(camera);
            }

            if (aiPlayButtons && aiPlayButtons.length > 0) {
                var aiRunning = !!camera.is_running;
                Array.prototype.forEach.call(aiPlayButtons, function (aiPlayButton) {
                    aiPlayButton.classList.toggle("is-running", aiRunning);
                    aiPlayButton.disabled = aiRunning;
                    aiPlayButton.title = aiRunning ?"IA em execução" : "Iniciar IA";
                    aiPlayButton.setAttribute("aria-label", aiRunning ?"IA em execução" : "Iniciar IA");
                });
            }

            if (webrtcFrameEl && streamMode === "webrtc") {
                var currentWebrtcBase = webrtcFrameEl.getAttribute("data-stream-base") || "";
                if (currentWebrtcBase !== streamUrl) {
                    webrtcFrameEl.setAttribute("data-stream-base", streamUrl);
                    webrtcFrameEl.setAttribute("src", streamUrl);
                }
            }

            if (videoWrapEl) {
                videoWrapEl.style.aspectRatio = aspectRatio || "16 / 9";
            }

            if (overlayLayerEl) {
                overlayLayerEl.innerHTML = renderCameraOverlay(camera);
            }

            if (boxLayerEl) {
                boxLayerEl.innerHTML = renderCameraBoxes(camera);
            }

            syncTileVisualLayers(tile, camera);

            if (streamStateEl) {
                streamStateEl.className = "vms-stream-state vms-technical-detail " + streamState.cls;
                streamStateEl.textContent = streamState.text;
            }

            if (alarmEl) {
                alarmEl.className = severity ?"vms-alarm-tag vms-technical-detail severity-" + escapeHtml(severity) : "vms-alarm-tag vms-technical-detail";
                alarmEl.textContent = severity ?"alarme " + severity : "";
                alarmEl.style.display = severity ?"" : "none";
            }
        });
    }

    function renderWall() {
        if (!wallEl) return;

        var fingerprint = buildWallFingerprint();
        if (wallEl.dataset.renderFingerprint === fingerprint) {
            syncWallTileMetadata();
            syncSelectedTileHighlight();
            return;
        }

        var isSandbox = effectiveLayoutMode() === "sandbox";
        var currentSlots = wallEl.querySelectorAll(".vms-slot");

        if (currentSlots.length === selectedGrid && wallEl.dataset.renderLayoutMode === effectiveLayoutMode()) {
            for (var slot = 0; slot < selectedGrid; slot += 1) {
                var slotEl = wallEl.querySelector('.vms-slot[data-slot-index="' + slot + '"]');
                if (!slotEl) continue;

                var cameraId = assignments[slot];
                var camera = cameraId ?cameraById.get(String(cameraId)) : null;

                var currentTile = slotEl.querySelector(".vms-tile");
                var currentCameraId = currentTile ?currentTile.getAttribute("data-camera-id") : null;

                if (camera) {
                    if (currentCameraId === String(cameraId)) {
                        continue;
                    }

                    var existingTile = findReusableTile(cameraId);
                    var emptyNotice = slotEl.querySelector(".vms-empty-slot");
                    if (emptyNotice) emptyNotice.remove();

                    if (existingTile) {
                        updateTileSlotIndex(existingTile, slot);
                        slotEl.appendChild(existingTile);
                    } else {
                        if (currentTile) currentTile.remove();
                        var tempWrap = document.createElement("div");
                        tempWrap.innerHTML = buildCameraTile(camera, slot);
                        var newTile = tempWrap.firstElementChild;
                        if (newTile) {
                            updateTileSlotIndex(newTile, slot);
                            slotEl.appendChild(newTile);
                        }
                        hydrateWallStreams({ stagger: false, forceRefresh: true });
                    }
                } else {
                    if (currentTile) currentTile.remove();
                    if (!slotEl.querySelector(".vms-empty-slot")) {
                        var emptyDiv = document.createElement("div");
                        emptyDiv.className = "vms-empty-slot";
                        emptyDiv.textContent = "Slot " + (slot + 1) + " · arraste uma câmera aqui";
                        slotEl.appendChild(emptyDiv);
                    }
                }
            }

            wallEl.dataset.renderFingerprint = fingerprint;
            syncWallTileMetadata();
            syncSelectedTileHighlight();
            return;
        }

        teardownWallStreams();
        wallEl.className = "vms-wall grid-" + selectedGrid + " " + wallLayoutClass();
        wallEl.dataset.renderLayoutMode = effectiveLayoutMode();

        var fragment = document.createDocumentFragment();
        for (var fullSlot = 0; fullSlot < selectedGrid; fullSlot += 1) {
            var fullCameraId = assignments[fullSlot];
            var fullCamera = fullCameraId ?cameraById.get(String(fullCameraId)) : null;
            fragment.appendChild(buildSlotElement(fullSlot, fullCamera, isSandbox));
        }

        wallEl.replaceChildren(fragment);
        if (typeof isTemporalSequenceActive === "function" && isTemporalSequenceActive()) {
            ensureVmsSequenceHud();
            var movedBarContainer = document.getElementById("vmsSequenceProgressBarContainer");
            if (movedBarContainer) movedBarContainer.style.display = "block";
        }
        wallEl.dataset.renderFingerprint = fingerprint;
        hydrateWallStreams({ stagger: true, forceRefresh: true });
        updateSnapshotFallbackNotice();
        syncSelectedTileHighlight();
        if (!webrtcPureMode) {
            scheduleWallVisualLayerSync();
        }
        return;

        var chunks = [];
        for (var slot = 0; slot < selectedGrid; slot += 1) {
            var cameraId = assignments[slot];
            var camera = cameraId ?cameraById.get(String(cameraId)) : null;

            var slotStyle = "";
            var spanControlsHtml = "";
            var resizeHandleHtml = "";

            if (isSandbox) {
                var span = getTileSpan(slot);
                slotStyle = ' style="grid-column: span ' + span.colSpan + '; grid-row: span ' + span.rowSpan + ';"';
                spanControlsHtml = '<div class="vms-sandbox-span-controls">'
                    + '<button type="button" class="vms-span-btn" data-slot-index="' + slot + '" data-span-action="col-minus" title="Diminuir largura">-C</button>'
                    + '<span class="vms-sandbox-dim-badge">' + span.colSpan + 'x' + span.rowSpan + '</span>'
                    + '<button type="button" class="vms-span-btn" data-slot-index="' + slot + '" data-span-action="col-plus" title="Aumentar largura">+C</button>'
                    + '<button type="button" class="vms-span-btn" data-slot-index="' + slot + '" data-span-action="row-minus" title="Diminuir altura">-L</button>'
                    + '<button type="button" class="vms-span-btn" data-slot-index="' + slot + '" data-span-action="row-plus" title="Aumentar altura">+L</button>'
                    + '</div>';
                resizeHandleHtml = '<div class="vms-sandbox-resize-handle" data-slot-index="' + slot + '" title="Arrastar para redimensionar slot"></div>';
            }

            var slotClass = "vms-slot" + (isSandbox ?" vms-sandbox-slot" : "");

            if (!camera) {
                chunks.push(
                    '<div class="' + slotClass + '" data-slot-index="' + slot + '"' + slotStyle + '>'
                    + '    <div class="vms-slot-label">' + String(slot + 1).padStart(2, "0") + '</div>'
                    + '    <div class="vms-empty-slot">Slot ' + (slot + 1) + ' · arraste uma câmera aqui</div>'
                    + spanControlsHtml
                    + resizeHandleHtml
                    + '</div>'
                );
                continue;
            }

            chunks.push(
                '<div class="' + slotClass + '" data-slot-index="' + slot + '"' + slotStyle + '>'
                + buildCameraTile(camera, slot)
                + spanControlsHtml
                + resizeHandleHtml
                + '</div>'
            );
        }

        wallEl.innerHTML = chunks.join("");
        if (typeof isTemporalSequenceActive === "function" && isTemporalSequenceActive()) {
            ensureVmsSequenceHud();
            var barContainer = document.getElementById("vmsSequenceProgressBarContainer");
            if (barContainer) barContainer.style.display = "block";
        }
        wallEl.dataset.renderFingerprint = fingerprint;
        hydrateWallStreams({ stagger: true, forceRefresh: true });
        updateSnapshotFallbackNotice();
        syncSelectedTileHighlight();
        if (!webrtcPureMode) {
            scheduleWallVisualLayerSync();
        }
    }

    function alarmCameraId(alarm) {
        return safeString(alarm && (alarm.camera_id || alarm.cameraId || alarm.camera));
    }

    function alarmCreatedAtMs(alarm) {
        return parseAlarmTimestamp(alarm && (alarm.created_at || alarm.createdAt || alarm.created_at_iso));
    }

    function isAlarmAfterQueueSessionStart(alarm) {
        if (!queueSessionStartedMs) return true;
        var createdAtMs = alarmCreatedAtMs(alarm);
        if (!createdAtMs) return true;
        return createdAtMs > queueSessionStartedMs;
    }

    function alarmVisualStatus(alarm) {
        return safeString(alarm && (alarm.status || alarm.status_display)).toLowerCase();
    }

    function isAlarmHandledStatus(alarm) {
        var status = alarmVisualStatus(alarm);
        return ["closed", "fechado", "resolved", "atendido", "dismissed", "ack", "acknowledged", "reconhecido", "atribuido"].indexOf(status) !== -1;
    }

    function isAlarmVisibleInOperatorQueue(alarm) {
        var eventId = liveAlarmEventId(alarm);
        if (!eventId) return false;
        if (liveAlarmHandledIds.has(eventId) || liveAlarmDismissedIds.has(eventId)) return false;
        if (!isAlarmAfterQueueSessionStart(alarm)) return false;
        if (isAlarmHandledStatus(alarm)) return false;
        return alarm && alarm.is_alarm_active !== false;
    }

    function markAlarmHandledLocally(eventId) {
        var id = safeString(eventId);
        if (!id) return;

        liveAlarmHandledIds.add(id);
        liveAlarmDismissedIds.add(id);
        liveAlarmQueue = liveAlarmQueue.filter(function (alarm) {
            return liveAlarmEventId(alarm) !== id;
        });
        alarms = (alarms || []).filter(function (alarm) {
            return liveAlarmEventId(alarm) !== id;
        });

        if (liveAlarmCurrent && liveAlarmEventId(liveAlarmCurrent) === id) {
            closeLiveAlarmModal({ markHandled: true });
        }
        if (typeof hideAlarmPopup === "function") {
            try { hideAlarmPopup(); } catch (error) {}
        }
        updateLiveAlarmQueueBadge();
        renderAlarms();
        updateAlarmSpotlight();
    }

    function isSpotlightSeverity(alarm) {
        var severity = safeString(alarm && alarm.severity).toLowerCase();
        return severity === "critical" || severity === "high";
    }

    function markSpotlightTile(cameraId, severity) {
        if (!wallEl) return;
        Array.prototype.forEach.call(wallEl.querySelectorAll(".vms-slot"), function (slot) {
            var tile = slot.querySelector(".vms-tile");
            var isMatch = tile && tile.getAttribute("data-camera-id") === String(cameraId);
            slot.classList.toggle("has-critical-alarm", isMatch && severity === "critical");
            slot.classList.toggle("has-warning-alarm", isMatch && severity === "high");
        });
    }

    function resetSpotlight() {
        if (spotlightEl) {
            spotlightEl.hidden = true;
            spotlightEl.classList.remove("severity-critical", "severity-high");
        }
        spotlightAlarmState = null;
        markSpotlightTile("", "");
    }

    async function acknowledgeSpotlightAlarm() {
        if (!spotlightAlarmState || !spotlightAlarmState.eventId || !spotlightAckBtn) return;

        spotlightAckBtn.disabled = true;
        try {
            var response = await fetch("/events/" + encodeURIComponent(spotlightAlarmState.eventId) + "/ack", {
                method: "POST",
                redirect: "follow",
                headers: { "X-Requested-With": "XMLHttpRequest" }
            });
            if (!response.ok) {
                console.warn("Falha ao reconhecer spotlight", response.status);
            } else {
                console.info("Spotlight reconhecido", spotlightAlarmState.eventId);
            }
            await pollAlarmQueue();
        } catch (error) {
            console.warn("Falha ao reconhecer spotlight", error);
        } finally {
            spotlightAckBtn.disabled = false;
        }
    }

    function updateAlarmSpotlight() {
        if (!spotlightEnabled) {
            resetSpotlight();
            return;
        }

        var alarm = (alarms || []).filter(isAlarmVisibleInOperatorQueue).find(isSpotlightSeverity);
        if (!alarm) {
            resetSpotlight();
            return;
        }

        var cameraId = alarmCameraId(alarm);
        var severity = safeString(alarm.severity).toLowerCase();
        if (!cameraId) {
            console.warn("Spotlight ignorado: alarme sem camera_id", alarm.id);
        }

        var pinResult = cameraId
            ?ensureCameraVisibleOnStage(cameraId, { allowAssign: severity === "critical" || severity === "high", scroll: false })
            : { ok: false, reason: "camera_missing" };
        var previousSpotlightEventId = spotlightAlarmState && spotlightAlarmState.eventId;
        if (safeString(alarm.id) !== previousSpotlightEventId) {
            console.info("Spotlight acionado event=" + safeString(alarm.id)
                + " camera=" + cameraId + " severity=" + severity
                + " slot=" + safeString(pinResult.slotIndex) + " reason=" + safeString(pinResult.reason));
        }
        if (cameraId && !pinResult.ok) {
            var spotlightCamera = cameraById.get(String(cameraId));
            var spotlightUrl = spotlightCamera ?resolveWebrtcPlayerUrl(spotlightCamera, "spotlight") : "";
            console.warn("Falha ao abrir camera do spotlight no mosaico"
                + " event=" + safeString(alarm.id) + " camera=" + cameraId
                + " path=" + (cameraWebrtcPath(spotlightCamera) || ("cam_" + cameraId))
                + " url=" + (sanitizedPlayerUrl(spotlightUrl) || "unavailable")
                + " reason=" + safeString(pinResult.reason));
        }

        spotlightAlarmState = {
            active: true,
            eventId: safeString(alarm.id),
            cameraId: cameraId,
            slotId: pinResult.ok ?String(pinResult.slotIndex) : "",
            startedAt: new Date().toISOString(),
            acknowledged: safeString(alarm.status).toLowerCase() === "acknowledged",
            severity: severity,
            pinReason: pinResult.reason
        };

        markSpotlightTile(cameraId, severity);

        if (spotlightTitleEl) {
            spotlightTitleEl.textContent = safeString(alarm.camera_name) || ("Camera " + cameraId);
        }
        if (spotlightMetaEl) {
            spotlightMetaEl.textContent = (safeString(alarm.event_type) || "Evento") + " | " + severity.toUpperCase() + " | " + safeString(alarm.created_at_label);
        }
        if (spotlightOpenEl) {
            spotlightOpenEl.href = alarmEventUrl(alarm);
        }
        if (spotlightAckBtn) {
            spotlightAckBtn.hidden = !alarm.can_ack;
        }
        if (spotlightEl) {
            spotlightEl.classList.toggle("severity-critical", severity === "critical");
            spotlightEl.classList.toggle("severity-high", severity === "high");
            spotlightEl.hidden = false;
        }
    }

    function renderAlarmActions(alarm) {
        var snapshot = alarm.snapshot_url
            ?'<button type="button" class="btn btn-secondary btn-small" data-alarm-evidence="snapshot" data-evidence-url="' + escapeHtml(alarm.snapshot_url) + '" data-evidence-title="' + escapeHtml(alarm.camera_name || "Camera") + '" data-evidence-meta="' + escapeHtml((alarm.event_type || "Evento") + " | " + (alarm.created_at_label || "")) + '">Snapshot</button>'
            : "";
        var clip = alarm.clip_url
            ?'<button type="button" class="btn btn-secondary btn-small" data-alarm-evidence="clip" data-evidence-url="' + escapeHtml(alarm.clip_url) + '" data-evidence-title="' + escapeHtml(alarm.camera_name || "Camera") + '" data-evidence-meta="' + escapeHtml((alarm.event_type || "Evento") + " | " + (alarm.created_at_label || "")) + '">Clipe</button>'
            : "";

        var acknowledge = alarm.can_ack
            ?'<button type="button" class="btn btn-warning btn-small" data-alarm-id="' + escapeHtml(alarm.id) + '" data-alarm-action-type="ack">Reconhecer</button>'
            : "";

        var closeOrReopen = alarm.can_close
            ?'<button type="button" class="btn btn-danger btn-small" data-alarm-id="' + escapeHtml(alarm.id) + '" data-alarm-action-type="close">Fechar</button>'
            : '<button type="button" class="btn btn-secondary btn-small" data-alarm-id="' + escapeHtml(alarm.id) + '" data-alarm-action-type="reopen">Reabrir</button>';

        return ''
            + '<div class="alarm-item-actions">'
            + '    <a class="btn btn-secondary btn-small" href="' + escapeHtml(alarmEventUrl(alarm)) + '">Abrir evento</a>'
            + '    <button type="button" class="btn btn-secondary btn-small" data-alarm-camera-id="' + escapeHtml(alarmCameraId(alarm)) + '">Ver no mosaico</button>'
            +      acknowledge
            +      closeOrReopen
            +      snapshot
            +      clip
            + '</div>';
    }

    async function postAlarmActionById(eventId, action) {
        if (!eventId) return;
        try {
            var headers = { "X-Requested-With": "XMLHttpRequest" };
            var bodyData = null;
            if (action === "close") {
                headers["Content-Type"] = "application/json";
                bodyData = JSON.stringify({ resolution_code: "false_alarm", comment: "Encerrado via monitor VMS" });
            }
            var response = await fetch("/events/" + encodeURIComponent(eventId) + "/" + action, {
                method: "POST",
                redirect: "follow",
                headers: headers,
                body: bodyData
            });
            if (response.ok) {
                markAlarmHandledLocally(eventId);
                pollAlarmQueue();
                pollLibrary(true);
            }
        } catch (error) {
            console.warn("Falha ao executar acao no alarme", eventId, action, error);
        }
    }


    function closeEvidenceModal() {
        if (!evidenceModalEl) return;
        evidenceModalEl.hidden = true;
        if (evidenceBodyEl) {
            evidenceBodyEl.innerHTML = "";
        }
    }

    function openEvidenceModal(kind, url, title, meta) {
        if (!evidenceModalEl || !evidenceBodyEl || !url) return;

        var normalizedKind = safeString(kind).toLowerCase() === "clip" ?"clip" : "snapshot";
        var safeUrl = safeString(url);
        if (evidenceTitleEl) evidenceTitleEl.textContent = title || "Evidencia";
        if (evidenceKindEl) evidenceKindEl.textContent = normalizedKind === "clip" ?"Clipe do evento" : "Snapshot do evento";
        if (evidenceMetaEl) evidenceMetaEl.textContent = meta || "";
        if (evidenceOpenEl) evidenceOpenEl.href = safeUrl;

        evidenceBodyEl.innerHTML = "";
        if (normalizedKind === "clip") {
            var video = document.createElement("video");
            video.controls = true;
            video.autoplay = true;
            video.playsInline = true;
            video.src = safeUrl;
            evidenceBodyEl.appendChild(video);
        } else {
            var image = document.createElement("img");
            image.alt = title || "Snapshot do evento";
            image.src = safeUrl;
            image.onerror = function () {
                evidenceBodyEl.innerHTML = '<div class="sunorus-evidence-empty">Nao foi possivel carregar o snapshot.</div>';
            };
            evidenceBodyEl.appendChild(image);
        }

        evidenceModalEl.hidden = false;
    }

    function isLiveAlarmEligible(alarm) {
        if (!alarm) return false;
        if (!centralPopupEnabled) return false;
        if (liveAlarmDismissedIds.has(liveAlarmEventId(alarm))) return false;
        if (!isAlarmAfterQueueSessionStart(alarm)) return false;
        if (isAlarmHandledStatus(alarm)) return false;
        if (alarm.is_alarm_active === false) return false;
        if (alarm.alarm_popup_enabled === false) return false;
        return !!alarm.id;
    }


    function liveAlarmEventId(alarm) {
        return safeString(alarm && alarm.id);
    }

    function isLiveAlarmQueued(eventId) {
        return liveAlarmQueue.some(function (item) {
            return liveAlarmEventId(item) === eventId;
        });
    }

    function ensureLiveAlarmModalHost() {
        if (!liveAlarmModalEl) return null;
        var fullscreenEl = null;
        if (typeof currentFullscreenElement === "function") {
            fullscreenEl = currentFullscreenElement();
        } else {
            fullscreenEl = document.fullscreenElement || null;
        }
        var targetHost = fullscreenEl || document.body;
        if (liveAlarmModalEl.parentElement !== targetHost) {
            targetHost.appendChild(liveAlarmModalEl);
        }
        return liveAlarmModalEl;
    }

    function clearLiveAlarmVideo() {
        if (liveAlarmSnapshotTimerId) {
            clearInterval(liveAlarmSnapshotTimerId);
            liveAlarmSnapshotTimerId = null;
        }
        closeLiveAlarmEvidence();
        if (liveAlarmVideoEl) {
            liveAlarmVideoEl.innerHTML = "";
        }
        liveAlarmRenderedEventId = "";
    }

    function renderLiveAlarmVisualLayers(camera) {
        if (!liveAlarmVideoEl || !camera) return;
        var overlayLayer = document.createElement("div");
        overlayLayer.className = "vms-overlay-layer sunorus-live-alarm-overlay-layer";
        overlayLayer.innerHTML = renderCameraOverlay(camera);
        liveAlarmVideoEl.appendChild(overlayLayer);

        var boxLayer = document.createElement("div");
        boxLayer.className = "vms-box-layer sunorus-live-alarm-box-layer";
        boxLayer.innerHTML = renderCameraBoxes(camera);
        liveAlarmVideoEl.appendChild(boxLayer);
    }

    function refreshLiveAlarmBoxes(cameraId) {
        if (!liveAlarmCurrent || !liveAlarmVideoEl) return;
        if (alarmCameraId(liveAlarmCurrent) !== String(cameraId)) return;
        var camera = cameraById.get(String(cameraId));
        if (!camera) return;
        var boxLayerEl = liveAlarmVideoEl.querySelector(".sunorus-live-alarm-box-layer");
        if (boxLayerEl) {
            boxLayerEl.innerHTML = renderCameraBoxes(camera);
        }
        var overlayLayerEl = liveAlarmVideoEl.querySelector(".sunorus-live-alarm-overlay-layer");
        if (overlayLayerEl) {
            overlayLayerEl.innerHTML = renderCameraOverlay(camera);
        }
    }

    function liveAlarmWebrtcUrl(alarm, camera) {
        if (camera && camera.webrtc_enabled !== false) {
            var resolved = resolveWebrtcPlayerUrl(camera, "spotlight");
            if (resolved) probeWebrtcPlayer(camera, resolved, "spotlight");
            return resolved;
        }
        var cameraId = alarmCameraId(alarm);
        if (!cameraId || !webrtcMonitorEnabled) return "";
        var publicBase = publicWebrtcBaseUrl();
        if (!publicBase) return "";
        var fallbackCamera = {
            id: cameraId
        };
        var fallbackUrl = appendPlayerParams(publicBase + "/" + encodeURIComponent("cam_" + cameraId), fallbackCamera);
        logWebrtcPlayerResolution(fallbackCamera, fallbackUrl, "spotlight");
        probeWebrtcPlayer(fallbackCamera, fallbackUrl, "spotlight");
        return fallbackUrl;
    }

    function renderLiveAlarmVideo(alarm, camera) {
        if (!liveAlarmVideoEl || !alarm) return;
        var eventId = liveAlarmEventId(alarm);
        if (liveAlarmRenderedEventId === eventId && liveAlarmVideoEl.childNodes.length) return;

        clearLiveAlarmVideo();
        liveAlarmRenderedEventId = eventId;

        var cameraId = alarmCameraId(alarm);
        if (cameraId && videoHelperAvailable && !liveAlarmHelperFailedEvents.has(eventId)) {
            var helperImage = document.createElement("img");
            helperImage.className = "sunorus-live-alarm-helper-stream";
            helperImage.alt = "Video H.265 local - " + (safeString(alarm.camera_name) || "Camera " + cameraId);
            helperImage.src = videoHelperStreamUrl(cameraId);
            helperImage.onerror = function () {
                liveAlarmHelperFailedEvents.add(eventId);
                liveAlarmRenderedEventId = "";
                renderLiveAlarmVideo(alarm, camera);
            };
            liveAlarmVideoEl.appendChild(helperImage);
            renderLiveAlarmVisualLayers(camera);
            return;
        }

        var webrtcUrl = liveAlarmWebrtcUrl(alarm, camera);
        if (webrtcUrl && !(clientHevcSupportKnown && !clientHevcSupported)) {
            var iframe = document.createElement("iframe");
            iframe.className = "sunorus-live-alarm-frame";
            iframe.title = "Video ao vivo - " + (safeString(camera && camera.name) || "Camera " + alarmCameraId(alarm));
            iframe.allow = "autoplay; fullscreen; picture-in-picture";
            iframe.referrerPolicy = "no-referrer";
            iframe.src = webrtcUrl;
            liveAlarmVideoEl.appendChild(iframe);
            renderLiveAlarmVisualLayers(camera);
            return;
        }

        if (cameraId) {
            var image = document.createElement("img");
            image.alt = "Snapshot ao vivo - " + (safeString(alarm.camera_name) || "Camera " + cameraId);
            var baseUrl = "/monitor/gateway/cameras/" + encodeURIComponent(cameraId) + "/snapshot.jpg";
            var refresh = function () {
                image.src = baseUrl + "?_ts=" + Date.now();
            };
            image.onerror = function () {
                if (!liveAlarmVideoEl) return;
                liveAlarmVideoEl.innerHTML = '<div class="sunorus-live-alarm-empty">Video ao vivo indisponivel. Abra a camera no mosaico para verificar o stream.</div>';
            };
            liveAlarmVideoEl.appendChild(image);
            renderLiveAlarmVisualLayers(camera);
            refresh();
            liveAlarmSnapshotTimerId = setInterval(refresh, 1200);
            return;
        }

        liveAlarmVideoEl.innerHTML = '<div class="sunorus-live-alarm-empty">Evento sem camera associada.</div>';
    }

    function liveAlarmSnapshotUrl(alarm) {
        if (!alarm) return "";
        return safeString(alarm.snapshot_url) || (alarm.id ?"/events/" + encodeURIComponent(alarm.id) + "/snapshot" : "");
    }

    function liveAlarmClipUrl(alarm) {
        if (!alarm) return "";
        return safeString(alarm.clip_url);
    }

    function closeLiveAlarmEvidence() {
        if (liveAlarmEvidenceEl) liveAlarmEvidenceEl.hidden = true;
        if (liveAlarmEvidenceBodyEl) liveAlarmEvidenceBodyEl.innerHTML = "";
        if (liveAlarmModalEl) liveAlarmModalEl.classList.remove("has-live-alarm-evidence");
    }

    function openLiveAlarmEvidence(kind) {
        if (!liveAlarmCurrent || !liveAlarmEvidenceEl || !liveAlarmEvidenceBodyEl) return;
        var normalizedKind = safeString(kind).toLowerCase() === "clip" ?"clip" : "snapshot";
        var url = normalizedKind === "clip" ?liveAlarmClipUrl(liveAlarmCurrent) : liveAlarmSnapshotUrl(liveAlarmCurrent);
        if (!url) return;

        if (liveAlarmEvidenceKindEl) {
            liveAlarmEvidenceKindEl.textContent = normalizedKind === "clip" ?"Clipe do evento" : "Snapshot do evento";
        }
        if (liveAlarmEvidenceTitleEl) {
            liveAlarmEvidenceTitleEl.textContent = safeString(liveAlarmCurrent.camera_name) || "Evidencia";
        }

        liveAlarmEvidenceBodyEl.innerHTML = "";
        if (normalizedKind === "clip") {
            var video = document.createElement("video");
            video.controls = true;
            video.autoplay = true;
            video.playsInline = true;
            video.src = url;
            liveAlarmEvidenceBodyEl.appendChild(video);
        } else {
            var image = document.createElement("img");
            image.alt = "Snapshot do evento";
            image.src = url;
            image.onerror = function () {
                liveAlarmEvidenceBodyEl.innerHTML = '<div class="sunorus-live-alarm-evidence-empty">Nao foi possivel carregar o snapshot.</div>';
            };
            liveAlarmEvidenceBodyEl.appendChild(image);
        }
        liveAlarmEvidenceEl.hidden = false;
        if (liveAlarmModalEl) liveAlarmModalEl.classList.add("has-live-alarm-evidence");
    }

    function updateLiveAlarmQueueBadge() {
        if (!liveAlarmQueueEl) return;
        var count = liveAlarmQueue.length;
        liveAlarmQueueEl.hidden = count <= 0;
        if (count > 0) {
            liveAlarmQueueEl.textContent = "+" + count + " na fila";
        }
    }

    function renderLiveAlarmModal() {
        if (!liveAlarmCurrent || !ensureLiveAlarmModalHost()) return;

        var alarm = liveAlarmCurrent;
        var cameraId = alarmCameraId(alarm);
        var camera = cameraById.get(String(cameraId));
        var severity = safeString(alarm.severity_label || alarm.severity || "media");
        var status = safeString(alarm.status_label || alarm.status || "Novo");
        var eventLabel = safeString(alarm.event_type_label || alarm.event_type || "Evento");
        var title = safeString(alarm.camera_name) || (camera ?camera.name : "Camera " + cameraId);

        if (liveAlarmKickerEl) liveAlarmKickerEl.textContent = "Alarme novo";
        if (liveAlarmTitleEl) liveAlarmTitleEl.textContent = title;
        if (liveAlarmMetaEl) liveAlarmMetaEl.textContent = eventLabel + " | " + severity.toUpperCase() + " | " + safeString(alarm.created_at_label);
        if (liveAlarmEventTypeEl) liveAlarmEventTypeEl.textContent = eventLabel;
        if (liveAlarmSeverityEl) liveAlarmSeverityEl.textContent = severity;
        if (liveAlarmStatusEl) liveAlarmStatusEl.textContent = status;
        if (liveAlarmOpenEventEl) liveAlarmOpenEventEl.href = alarmEventUrl(alarm);
        setLiveAlarmActionButtonsDisabled(liveAlarmActionPending, alarm);
        if (liveAlarmSnapshotBtn) liveAlarmSnapshotBtn.disabled = !liveAlarmSnapshotUrl(alarm);
        if (liveAlarmClipBtn) liveAlarmClipBtn.disabled = !liveAlarmClipUrl(alarm);
        var sessionBadgeEl = document.getElementById("sunorusLiveAlarmSessionBadge");
        if (sessionBadgeEl) {
            sessionBadgeEl.hidden = !(alarm.track_id || alarm.correlation_key);
        }
        var slaTimerEl = document.getElementById("sunorusLiveAlarmSlaTimer");
        if (slaTimerEl) {
            slaTimerEl.textContent = alarm.sla_state ?("SLA: " + alarm.sla_state.toUpperCase()) : "";
        }
        updateLiveAlarmQueueBadge();
        renderLiveAlarmVideo(alarm, camera);
        renderCropCanvas(alarm);

        liveAlarmModalEl.hidden = false;
        document.body.classList.add("sunorus-live-alarm-open");
    }

    function renderCropCanvas(alarm) {
        var cropBox = document.getElementById("sunorusLiveAlarmCropBox");
        var canvas = document.getElementById("sunorusLiveAlarmCropCanvas");
        if (!cropBox || !canvas || !alarm) {
            if (cropBox) cropBox.style.display = "none";
            return;
        }

        var snapshotUrl = alarm.snapshot_url || (alarm.id ?("/events/" + alarm.id + "/snapshot") : null);
        if (!snapshotUrl) {
            cropBox.style.display = "none";
            return;
        }

        var bbox = alarm.bbox || null;
        if (!bbox && alarm.bbox_json) {
            try { bbox = JSON.parse(alarm.bbox_json); } catch (e) {}
        }

        if (!bbox || !Array.isArray(bbox) || bbox.length < 4) {
            cropBox.style.display = "none";
            return;
        }

        var img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = function () {
            var ctx = canvas.getContext("2d");
            var x = Number(bbox[0]), y = Number(bbox[1]), w = Number(bbox[2]), h = Number(bbox[3]);
            if (x <= 1.0 && y <= 1.0 && w <= 1.0 && h <= 1.0) {
                x = x * img.width;
                y = y * img.height;
                w = w * img.width;
                h = h * img.height;
            }
            var padX = w * 0.15;
            var padY = h * 0.15;
            var cropX = Math.max(0, x - padX);
            var cropY = Math.max(0, y - padY);
            var cropW = Math.min(img.width - cropX, w + padX * 2);
            var cropH = Math.min(img.height - cropY, h + padY * 2);

            if (cropW <= 0 || cropH <= 0) {
                cropBox.style.display = "none";
                return;
            }

            canvas.width = 300;
            canvas.height = Math.max(100, Math.round(300 * (cropH / cropW)));

            ctx.drawImage(img, cropX, cropY, cropW, cropH, 0, 0, canvas.width, canvas.height);
            cropBox.style.display = "block";
        };
        img.onerror = function () {
            cropBox.style.display = "none";
        };
        img.src = snapshotUrl;
    }

    function closeLiveAlarmModal(options) {
        var opts = options || {};
        if (opts.markHandled && liveAlarmCurrent) {
            liveAlarmHandledIds.add(liveAlarmEventId(liveAlarmCurrent));
        }
        liveAlarmCurrent = null;
        clearLiveAlarmVideo();
        if (liveAlarmModalEl) {
            liveAlarmModalEl.hidden = true;
        }
        document.body.classList.remove("sunorus-live-alarm-open");
        updateLiveAlarmQueueBadge();
    }

    function showNextLiveAlarm() {
        if (liveAlarmCurrent || !liveAlarmModalEl) return;
        while (liveAlarmQueue.length) {
            var next = liveAlarmQueue.shift();
            if (!isLiveAlarmEligible(next)) continue;
            if (liveAlarmHandledIds.has(liveAlarmEventId(next))) continue;
            liveAlarmCurrent = next;
            renderLiveAlarmModal();
            return;
        }
        updateLiveAlarmQueueBadge();
    }

    function enqueueLiveAlarm(alarm) {
        if (!isLiveAlarmEligible(alarm)) return false;
        var eventId = liveAlarmEventId(alarm);
        if (!eventId || liveAlarmHandledIds.has(eventId)) return false;
        liveAlarmKnownIds.add(eventId);
        if (liveAlarmCurrent && (liveAlarmEventId(liveAlarmCurrent) === eventId || (alarm.track_id && liveAlarmCurrent.track_id === alarm.track_id))) {
            liveAlarmCurrent = alarm;
            renderLiveAlarmModal();
            return true;
        }
        if (isLiveAlarmQueued(eventId)) return false;
        liveAlarmQueue.push(alarm);
        updateLiveAlarmQueueBadge();
        showNextLiveAlarm();
        return true;
    }

    function openLiveAlarmModalNow(alarm, options) {
        if (!isLiveAlarmEligible(alarm)) return false;
        var opts = options || {};
        var eventId = liveAlarmEventId(alarm);
        if (!eventId) return false;

        if ((liveAlarmHandledIds.has(eventId) || liveAlarmDismissedIds.has(eventId)) && !opts.force) {
            return false;
        }
        if (opts.force) {
            liveAlarmHandledIds.delete(eventId);
            liveAlarmDismissedIds.delete(eventId);
        }
        liveAlarmKnownIds.add(eventId);

        if (liveAlarmCurrent && liveAlarmEventId(liveAlarmCurrent) === eventId) {
            liveAlarmCurrent = alarm;
            renderLiveAlarmModal();
            return true;
        }

        liveAlarmQueue = liveAlarmQueue.filter(function (queued) {
            return liveAlarmEventId(queued) !== eventId;
        });

        if (liveAlarmCurrent && !opts.replace) {
            liveAlarmQueue.unshift(alarm);
            updateLiveAlarmQueueBadge();
            return true;
        }

        if (liveAlarmCurrent && opts.replace) {
            closeLiveAlarmModal();
        }

        liveAlarmCurrent = alarm;
        renderLiveAlarmModal();
        return true;
    }

    function buildDevCentralPopupAlarm() {
        var cameraId = selectedCameraId || assignedCameraIds()[0] || "";
        var camera = cameraId ?cameraById.get(String(cameraId)) : null;
        var now = new Date();
        return {
            id: "dev-popup-" + now.getTime(),
            camera_id: cameraId || "",
            camera_name: camera ?safeString(camera.name) : "Camera de teste",
            event_type: "intrusion",
            event_type_label: "Evento falso DEV",
            severity: "critical",
            severity_label: "critico",
            status: "new",
            status_label: "Novo",
            created_at_label: now.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
            is_alarm_active: true,
            alarm_popup_enabled: true,
            can_ack: false,
            can_close: false,
            event_url: "#",
            snapshot_url: "",
            clip_url: "",
            track_id: "dev-test",
            correlation_key: "dev-popup-test"
        };
    }

    function triggerDevCentralPopup() {
        centralPopupEnabled = true;
        persistCentralPopupPreference();
        applyCentralPopupPreference();
        openLiveAlarmModalNow(buildDevCentralPopupAlarm(), { replace: true, force: true });
    }

    function syncLiveAlarmQueue(nextAlarms) {
        var eligible = (Array.isArray(nextAlarms) ?nextAlarms : []).filter(isLiveAlarmEligible);
        var eligibleById = new Map();
        eligible.forEach(function (alarm) {
            var eventId = liveAlarmEventId(alarm);
            eligibleById.set(eventId, alarm);
            if (!liveAlarmKnownIds.has(eventId)) {
                enqueueLiveAlarm(alarm);
            }
            liveAlarmKnownIds.add(eventId);
        });

        if (liveAlarmCurrent) {
            var currentId = liveAlarmEventId(liveAlarmCurrent);
            var updated = eligibleById.get(currentId);
            if (updated) {
                liveAlarmCurrent = updated;
                renderLiveAlarmModal();
            } else {
                closeLiveAlarmModal({ markHandled: true });
                showNextLiveAlarm();
            }
        } else {
            showNextLiveAlarm();
        }
    }

    function clearLiveAlarmQueue() {
        (alarms || []).forEach(function (alarm) {
            var eventId = liveAlarmEventId(alarm);
            if (eventId) {
                liveAlarmKnownIds.add(eventId);
                liveAlarmHandledIds.add(eventId);
                liveAlarmDismissedIds.add(eventId);
            }
        });

        liveAlarmQueue = [];
        closeLiveAlarmModal();
        updateLiveAlarmQueueBadge();
        renderAlarms();
        updateAlarmSpotlight();
        setAlarmRefreshMeta("ok", "Fila visual limpa nesta sessao");
    }

    function setLiveAlarmActionButtonsDisabled(disabled, alarm) {
        var current = alarm || liveAlarmCurrent || {};
        if (liveAlarmAckBtn) {
            liveAlarmAckBtn.disabled = disabled || !current.can_ack;
        }
        if (liveAlarmAuthorizeBtn) {
            liveAlarmAuthorizeBtn.disabled = disabled || !current.can_close;
        }
        if (liveAlarmCloseEventBtn) {
            liveAlarmCloseEventBtn.disabled = disabled || !current.can_close;
        }
    }

    async function requestLiveAlarmAction(eventId, action, resolutionCode) {
        var headers = { "X-Requested-With": "XMLHttpRequest" };
        var bodyData = null;
        if (action === "close") {
            headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8";
            bodyData = new URLSearchParams({
                resolution_code: resolutionCode,
                resolution_comment: "Classificado via popup do monitor VMS"
            }).toString();
        }
        return fetch("/events/" + encodeURIComponent(eventId) + "/" + action, {
            method: "POST",
            redirect: "follow",
            headers: headers,
            body: bodyData
        });
    }

    async function postLiveAlarmAction(action, resolutionCode) {
        if (liveAlarmActionPending || !liveAlarmCurrent || !liveAlarmCurrent.id) return;
        var alarm = liveAlarmCurrent;
        var eventId = liveAlarmEventId(alarm);
        liveAlarmActionPending = true;
        setLiveAlarmActionButtonsDisabled(true, alarm);
        try {
            var response = await requestLiveAlarmAction(eventId, action, resolutionCode || "");
            if (!response.ok) {
                throw new Error("Falha HTTP " + response.status + " na ação " + action);
            }
            markAlarmHandledLocally(eventId);
            await pollAlarmQueue();
            showNextLiveAlarm();
        } catch (error) {
            console.warn("Falha na acao do alarme ao vivo", error);
            setAlarmRefreshMeta("error", "Não foi possível concluir a ação do alarme");
        } finally {
            liveAlarmActionPending = false;
            if (liveAlarmCurrent && liveAlarmEventId(liveAlarmCurrent) === eventId) {
                setLiveAlarmActionButtonsDisabled(false, liveAlarmCurrent);
            }
        }
    }

    function renderAlarms() {
        if (!alarmSidebarEl) return;

        var visibleAlarms = (alarms || []).filter(isAlarmVisibleInOperatorQueue);

        if (!visibleAlarms.length) {
            alarmSidebarEl.innerHTML = '<div class="muted">Nenhum alarme aberto nas câmeras exibidas.</div>';
            return;
        }

        alarmSidebarEl.innerHTML = visibleAlarms.map(function (alarm) {
            return ''
                + '<div class="alarm-item severity-' + escapeHtml(alarm.severity || "medium") + '">'
                + '    <div class="alarm-item-head">'
                + '        <div>'
                + '            <div class="alarm-camera">' + escapeHtml(alarm.camera_name) + '</div>'
                + '            <div class="alarm-meta">' + escapeHtml(alarm.event_type) + ' · ' + escapeHtml(alarm.created_at_label) + '</div>'
                + '        </div>'
                + '        <div class="alarm-badges">'
                +              statusBadge("priority", alarm.severity)
                +              statusBadge("status", alarm.status)
                + '        </div>'
                + '    </div>'
                +      renderAlarmActions(alarm)
                + '</div>';
        }).join("");
    }

    function refreshAll() {
        ensureAssignmentsMatchAvailable();
        renderStats();
        renderGatewayHealth();
        updateLibraryFilterUi();
        renderLibrary();
        renderMapPins();
        renderWall();
        ensureSelectedCameraSelection();
        renderAlarms();
        updateAlarmSpotlight();
    }

    function assignedCameraIds() {
        var ids = [];
        var seen = new Set();
        (assignments || []).forEach(function (cameraId) {
            if (!cameraId || seen.has(String(cameraId))) return;
            seen.add(String(cameraId));
            ids.push(String(cameraId));
        });
        return ids;
    }

    function assignedCameraIdsForBoxes() {
        var ids = assignedCameraIds();
        var liveCameraId = liveAlarmCurrent ?alarmCameraId(liveAlarmCurrent) : "";
        if (liveCameraId && ids.indexOf(String(liveCameraId)) === -1) {
            ids.push(String(liveCameraId));
        }
        return ids.filter(function (cameraId) {
            var camera = cameraById.get(String(cameraId));
            return cameraCanRenderBoxes(camera);
        });
    }

    function updateCameraBoxes(cameraId, payload) {
        var camera = cameraById.get(String(cameraId));
        if (!camera) return;

        if (
            payload
            && payload.camera_id != null
            && String(payload.camera_id) !== String(cameraId)
        ) {
            trackTransportMetrics.visual_updates_identity_rejected_total += 1;
            return;
        }
        var incomingFrameId = Number(payload && payload.frame_id);
        var incomingGenerationId = payload && payload.generation_id != null
            ?String(payload.generation_id)
            :"";
        var hasFrameIdentity = Number.isFinite(incomingFrameId) && incomingFrameId > 0;
        if (
            hasFrameIdentity
            && incomingGenerationId
            && camera.monitor_boxes_generation_id === incomingGenerationId
            && Number(camera.monitor_boxes_frame_id || 0) >= incomingFrameId
        ) {
            trackTransportMetrics.visual_updates_out_of_order_total += 1;
            return;
        }

        var clientReceivedAtNs = Date.now() * 1000000;
        if (hasFrameIdentity) {
            camera.monitor_boxes_frame_id = incomingFrameId;
            camera.monitor_boxes_generation_id = incomingGenerationId;
        }
        camera.monitor_boxes_client_received_at_ns = clientReceivedAtNs;
        var tracksPublishedAtNs = Number(payload && payload.tracks_published_at_ns);
        camera.monitor_boxes_backend_to_client_ms = (
            Number.isFinite(tracksPublishedAtNs) && tracksPublishedAtNs > 0
        ) ?Math.max(0, (clientReceivedAtNs - tracksPublishedAtNs) / 1000000) :null;
        recordClientLatency(
            "backend_to_client_ms",
            camera.monitor_boxes_backend_to_client_ms
        );

        var nextTracks = cameraCanRenderBoxes(camera) && Array.isArray(payload && payload.tracks)
            ?filterTrackBoxes(payload.tracks)
            : [];
        var now = Date.now();

        if (!cameraCanRenderBoxes(camera)) {
            camera.monitor_boxes = [];
            camera.monitor_boxes_updated_at = 0;
            camera.monitor_boxes_stale = false;
        } else if (nextTracks.length) {
            camera.monitor_boxes = nextTracks;
            camera.monitor_boxes_updated_at = now;
            camera.monitor_boxes_stale = (
                Number(camera.monitor_boxes_backend_to_client_ms || 0)
                > visualTrackFreshMs
            );
            if (camera.monitor_boxes_stale) {
                trackTransportMetrics.visual_updates_stale_total += 1;
            }
        } else if (hasFrameIdentity && payload && payload.stale === false) {
            trackTransportMetrics.visual_empty_results_total += 1;
            camera.monitor_boxes = [];
            camera.monitor_boxes_updated_at = 0;
            camera.monitor_boxes_stale = false;
        } else if (
            camera.monitor_boxes_updated_at
            && Array.isArray(camera.monitor_boxes)
            && camera.monitor_boxes.length
            && now - camera.monitor_boxes_updated_at <= tracksMaxAgeSeconds * 1000
        ) {
            // Keep the last valid result briefly so a slow inference cycle does not
            // make a person visually disappear. The badge marks it as stale.
            camera.monitor_boxes_stale = true;
        } else {
            camera.monitor_boxes = [];
            camera.monitor_boxes_updated_at = 0;
            camera.monitor_boxes_stale = false;
        }
        if (payload && Number(payload.source_frame_width) > 0) {
            camera.source_frame_width = Number(payload.source_frame_width);
        }
        if (payload && Number(payload.source_frame_height) > 0) {
            camera.source_frame_height = Number(payload.source_frame_height);
        }

        var tiles = wallEl ?wallEl.querySelectorAll(".vms-tile") : [];
        Array.prototype.forEach.call(tiles, function (tile) {
            if (tile.getAttribute("data-camera-id") !== String(cameraId)) return;
            tile.classList.toggle("vms-no-live-video", !cameraCanRenderBoxes(camera));
            var boxLayerEl = tile.querySelector(".vms-box-layer");
            if (boxLayerEl) {
                boxLayerEl.innerHTML = renderCameraBoxes(camera);
            }
            syncTileVisualLayers(tile, camera);
        });
        refreshLiveAlarmBoxes(cameraId);
        window.requestAnimationFrame(function () {
            camera.monitor_boxes_client_rendered_at_ns = Date.now() * 1000000;
            camera.monitor_boxes_client_render_ms = Math.max(
                0,
                (
                    camera.monitor_boxes_client_rendered_at_ns
                    - clientReceivedAtNs
                )
                / 1000000
            );
            recordClientLatency(
                "client_render_ms",
                camera.monitor_boxes_client_render_ms
            );
        });
    }

    function recordClientLatency(name, value) {
        if (!Number.isFinite(Number(value))) return;
        var samples = clientLatencySamples[name];
        if (!Array.isArray(samples)) return;
        samples.push(Math.max(0, Number(value)));
        if (samples.length > 300) samples.shift();
    }

    function summarizeClientLatency(samples) {
        if (!Array.isArray(samples) || !samples.length) {
            return { count: 0, mean: null, p50: null, p95: null, max: null };
        }
        var sorted = samples.slice().sort(function (a, b) { return a - b; });
        function percentile(value) {
            return sorted[Math.min(
                sorted.length - 1,
                Math.round((sorted.length - 1) * value)
            )];
        }
        return {
            count: sorted.length,
            mean: sorted.reduce(function (total, item) {
                return total + item;
            }, 0) / sorted.length,
            p50: percentile(0.50),
            p95: percentile(0.95),
            max: sorted[sorted.length - 1]
        };
    }

    function expireStaleCameraBoxes() {
        var now = Date.now();
        var changedIds = [];
        cameraById.forEach(function (camera, cameraId) {
            if (!camera || !camera.monitor_boxes_updated_at || !Array.isArray(camera.monitor_boxes) || !camera.monitor_boxes.length) return;
            if (now - camera.monitor_boxes_updated_at <= tracksMaxAgeSeconds * 1000) return;
            camera.monitor_boxes = [];
            camera.monitor_boxes_updated_at = 0;
            camera.monitor_boxes_stale = false;
            trackTransportMetrics.visual_boxes_expired_total += 1;
            changedIds.push(cameraId);
        });

        if (!changedIds.length || !wallEl) return;
        changedIds.forEach(function (cameraId) {
            var camera = cameraById.get(String(cameraId));
            if (!camera) return;
            Array.prototype.forEach.call(wallEl.querySelectorAll(".vms-tile"), function (tile) {
                if (tile.getAttribute("data-camera-id") !== String(cameraId)) return;
                var boxLayerEl = tile.querySelector(".vms-box-layer");
                if (boxLayerEl) boxLayerEl.innerHTML = renderCameraBoxes(camera);
            });
            refreshLiveAlarmBoxes(cameraId);
        });
    }

    function updateCameraPlayerGeometry(camera, diagnostics) {
        if (!camera) return;

        var runtime = pickWebrtcRuntime(diagnostics);
        var geometry = extractWebrtcVideoGeometry(runtime);
        if (geometry) {
            camera.player_frame_width = geometry.width;
            camera.player_frame_height = geometry.height;
        }
    }

    function extractWebrtcVideoGeometry(runtime) {
        if (!runtime || typeof runtime !== "object") return null;

        var videoCodec = /H264|AVC|H265|HEVC/i;
        var candidates = [];

        function collect(value, codecHint) {
            if (!value) return;

            if (typeof value === "string") {
                var match = value.match(/(\d+)\s*[x×]\s*(\d+)/i);
                if (match && (!codecHint || codecHint === "video" || videoCodec.test(codecHint))) {
                    candidates.push({ width: Number(match[1]), height: Number(match[2]) });
                }
                return;
            }

            if (typeof value !== "object") return;

            var codec = safeString(value.codec || value.codec_name || value.type || codecHint);
            var props = value.codecProps || value.codec_props || value.properties || value;
            var width = Number(props.width || props.frameWidth || props.frame_width || 0);
            var height = Number(props.height || props.frameHeight || props.frame_height || 0);
            if (width > 0 && height > 0 && (!codec || codec === "video" || videoCodec.test(codec))) {
                candidates.push({ width: width, height: height });
            }

            [value.tracks2, value.tracks, value.source, value.stream, value.video].forEach(function (nested) {
                if (Array.isArray(nested)) {
                    nested.forEach(function (item) { collect(item, codec); });
                } else if (nested && nested !== value) {
                    collect(nested, codec);
                }
            });
        }

        collect(runtime, "video");
        return candidates.find(function (candidate) {
            return Number.isFinite(candidate.width)
                && Number.isFinite(candidate.height)
                && candidate.width > 0
                && candidate.height > 0;
        }) || null;
    }

    function pickWebrtcRuntime(diagnostics) {
        var runtime = diagnostics && diagnostics.runtime ?diagnostics.runtime : {};
        return runtime.item || runtime.data || runtime;
    }

    function summarizePlayerFromDiagnostics(camera, diagnostics) {
        if (!camera || !camera.webrtc_enabled) {
            return { status: "offline", label: "WebRTC off", detail: "mosaico exige WebRTC", ok: false };
        }
        if (!camera.webrtc_registration_ok) {
            return {
                status: "offline",
                label: "Player sem rota",
                detail: safeString(camera.webrtc_registration_reason || "registro WebRTC indisponivel"),
                ok: false
            };
        }

        var runtime = pickWebrtcRuntime(diagnostics);
        var ready = runtime.ready;
        if (typeof ready === "undefined") ready = runtime.sourceReady;
        if (typeof ready === "undefined") ready = runtime.readyTime ?true : null;

        var readers = Array.isArray(runtime.readers) ?runtime.readers.length : 0;
        var tracks = Array.isArray(runtime.tracks) ?runtime.tracks : [];
        var tracks2 = Array.isArray(runtime.tracks2) ?runtime.tracks2 : [];
        var bytesReceived = Number(runtime.bytesReceived || runtime.bytes_received || runtime.inboundBytes || 0);
        var error = safeString(runtime.error || runtime.lastError || runtime.sourceError || (diagnostics && diagnostics.error) || "");

        var hasVideoTrack = false;
        var invalidVideoGeometry = false;
        var hasH264 = false;
        var hasHevc = false;
        var codecs = [];
        tracks2.forEach(function (track) {
            var codec = safeString(track && track.codec).toUpperCase();
            var props = track && track.codecProps ?track.codecProps : {};
            var width = Number(props.width || 0);
            var height = Number(props.height || 0);
            if (codec) codecs.push(codec + (width > 0 && height > 0 ?" " + width + "x" + height : ""));
            if (codec.indexOf("H264") >= 0 || codec.indexOf("AVC") >= 0) hasH264 = true;
            if (codec.indexOf("H265") >= 0 || codec.indexOf("HEVC") >= 0) hasHevc = true;
            if (codec.indexOf("H264") >= 0 || codec.indexOf("AVC") >= 0 || codec.indexOf("H265") >= 0 || codec.indexOf("HEVC") >= 0) {
                hasVideoTrack = true;
                if (width <= 0 || height <= 0) invalidVideoGeometry = true;
            }
        });
        if (!hasVideoTrack && tracks.length) {
            hasVideoTrack = tracks.some(function (track) {
                var name = safeString(track).toUpperCase();
                if (name.indexOf("H264") >= 0 || name.indexOf("AVC") >= 0) hasH264 = true;
                if (name.indexOf("H265") >= 0 || name.indexOf("HEVC") >= 0) hasHevc = true;
                return name.indexOf("H264") >= 0 || name.indexOf("AVC") >= 0 || name.indexOf("H265") >= 0 || name.indexOf("HEVC") >= 0;
            });
        }

        if (diagnostics && diagnostics.ok === false) {
            return { status: "offline", label: "Player sem API", detail: "MediaMTX nao respondeu", ok: false };
        }
        if (error) {
            return { status: "offline", label: "Player erro", detail: error, ok: false };
        }
        if (ready === false) {
            return { status: "offline", label: "Player sem fonte", detail: "MediaMTX source nao pronto", ok: false };
        }
        if (!hasVideoTrack || tracks.length === 0) {
            return { status: "offline", label: "Player sem video", detail: "sem tracks de video no MediaMTX", ok: false };
        }
        if (invalidVideoGeometry) {
            return { status: "offline", label: "Player sem resolucao", detail: codecs.join(", ") || "track sem dimensoes", ok: false };
        }
        if (hasHevc && !hasH264 && clientHevcSupportKnown && !clientHevcSupported) {
            return {
                status: "warn",
                label: "WebRTC",
                detail: "cliente sem HEVC; fallback MJPEG",
                ok: true,
                fallback: "snapshot"
            };
        }
        if (bytesReceived > 0 && bytesReceived <= 512) {
            return { status: "stale", label: "Player sem bytes", detail: "rx muito baixo: " + bytesReceived, ok: false };
        }
        if (!readers) {
            return { status: "checking", label: "Player sem leitor", detail: "sem tile WebRTC conectado", ok: false };
        }

        return {
            status: "ok",
            label: "Player OK",
            detail: (codecs.join(", ") || "MediaMTX pronto") + " | readers " + readers + " | rx " + bytesReceived,
            ok: true
        };
    }

    function updateCameraOperationalBadges(cameraId) {
        var camera = cameraById.get(String(cameraId));
        if (!camera || !wallEl) return;
        var player = cameraOperationalHealth(camera).player || {};

        var tiles = wallEl.querySelectorAll('.vms-tile[data-camera-id="' + String(cameraId) + '"]');
        Array.prototype.forEach.call(tiles, function (tile) {
            tile.classList.toggle("vms-connection-unavailable", cameraConnectionUnavailable(camera));
            var badgesEl = tile.querySelector(".vms-tile-badges");
            if (!badgesEl) return;
            var health = cameraHealthStatus(camera);
            badgesEl.innerHTML = ''
                + cameraOperationalChips(camera)
                + '<span class="vms-status-line"><span class="vms-status-dot health-' + escapeHtml(health) + '"></span><span class="vms-technical-detail">' + escapeHtml(cameraHealthLabel(camera)) + '</span></span>'
                + '<span class="vms-technical-detail">' + statusBadge("priority", camera.camera_priority || "medium") + '</span>';
            var streamMode = cameraStreamMode(Number(tile.getAttribute("data-slot-index")));
            var streamState = cameraStreamState(camera, streamMode);
            var streamStateEl = tile.querySelector(".vms-stream-state");
            if (streamStateEl) {
                streamStateEl.className = "vms-stream-state vms-technical-detail " + streamState.cls;
                streamStateEl.textContent = streamState.text;
            }
            if (!cameraCanRenderBoxes(camera)) {
                camera.monitor_boxes = [];
                tile.classList.add("vms-no-live-video");
                var boxLayerEl = tile.querySelector(".vms-box-layer");
                if (boxLayerEl) {
                    boxLayerEl.innerHTML = "";
                }
            } else {
                tile.classList.remove("vms-no-live-video");
            }
            // Evidencia vale mais que capacidade declarada: se o player WebRTC nao
            // esta entregando video (path on-demand frio, sem tracks, codec que o
            // browser anuncia mas nao renderiza), o helper local assume. Ele abre o
            // RTSP e de quebra acorda o path sourceOnDemand no MediaMTX.
            var playerSemVideo = videoHelperAvailable && player && player.ok === false;
            setTileSnapshotFallback(
                tile,
                shouldDefaultToVideoHelper()
                    || playerSemVideo
                    || (!webrtcPureMode && player.fallback === "snapshot")
            );
        });
    }

    function setTileSnapshotFallback(tile, enabled) {
        if (!tile) return;
        tile.classList.toggle("vms-using-snapshot-fallback", !!enabled);
        if (enabled) {
            refreshTileSnapshotFallback(tile);
        } else {
            tile.classList.remove("vms-helper-stream");
            tile.removeAttribute("data-fonte");
            var image = tile.querySelector(".vms-snapshot-fallback");
            if (image) {
                if (image._sunorusHelperRetryTimer) {
                    clearTimeout(image._sunorusHelperRetryTimer);
                    image._sunorusHelperRetryTimer = null;
                }
                clearSnapshotPollTimer(image);
                image.onerror = null;
                image.onload = null;
                image.removeAttribute("src");
            }
        }
        updateSnapshotFallbackNotice();
    }

    var rotulosFonteImagem = {
        "helper": "Decoder local",
        "stream-raw": "Preview",
        "snapshot-poll": "Snapshot"
    };

    // O selo do canto descreve o estado do WebRTC. Quando a imagem chega por outro
    // caminho ele vira alarme falso - marca "player pendente" com video na tela.
    // Passa a informar a fonte real, em tom neutro em vez do amarelo de alerta.
    function sincronizarSeloFonteImagem(tile) {
        if (!tile || !tile.classList.contains("vms-using-snapshot-fallback")) return;
        var rotulo = rotulosFonteImagem[tile.getAttribute("data-fonte") || ""];
        if (!rotulo) return;
        var seloEl = tile.querySelector(".vms-stream-state");
        if (!seloEl) return;
        seloEl.textContent = rotulo;
        seloEl.className = "vms-stream-state vms-technical-detail stream-ok";
    }

    function clearSnapshotPollTimer(image) {
        if (image && image._sunorusPollTimer) {
            clearTimeout(image._sunorusPollTimer);
            image._sunorusPollTimer = null;
        }
    }

    function snapshotPollUrl(cameraId) {
        return "/cameras/" + encodeURIComponent(cameraId) + "/snapshot?_ts=" + Date.now();
    }

    // MJPEG mantem a conexao aberta enquanto o tile existir, e o navegador so
    // permite 6 conexoes simultaneas por origem. Acima disso um tile fica
    // esperando slot para sempre - preto. Os tiles alem do orcamento passam a
    // buscar snapshots avulsos: conexao curta, devolvida ao pool a cada frame.
    function refreshTileSnapshotFallbackPolling(tile, image, cameraId) {
        tile.classList.remove("vms-helper-stream");
        tile.setAttribute("data-fonte", "snapshot-poll");
        sincronizarSeloFonteImagem(tile);
        image.onerror = function () {
            clearSnapshotPollTimer(image);
            image._sunorusPollTimer = setTimeout(function () {
                image._sunorusPollTimer = null;
                if (!document.documentElement.contains(image)) return;
                if (!tile.classList.contains("vms-using-snapshot-fallback")) return;
                image.setAttribute("src", snapshotPollUrl(cameraId));
            }, 4000);
        };
        image.onload = function () {
            clearSnapshotPollTimer(image);
            image._sunorusPollTimer = setTimeout(function () {
                image._sunorusPollTimer = null;
                if (!document.documentElement.contains(image)) return;
                if (!tile.classList.contains("vms-using-snapshot-fallback")) return;
                image.setAttribute("src", snapshotPollUrl(cameraId));
            }, snapshotPollIntervalMs);
        };
        if (!image._sunorusPollTimer && (image.getAttribute("src") || "").indexOf("/snapshot?") < 0) {
            image.setAttribute("src", snapshotPollUrl(cameraId));
        }
    }

    // Com o helper em varias portas o orcamento cresce junto: cada origem tem seu
    // proprio limite de conexoes no navegador.
    function streamSlotBudget() {
        var origens = videoHelperAvailable ?Math.max(1, videoHelperPorts.length) : 1;
        return origens * streamSlotsPerOrigin;
    }

    // Posicao do tile entre os que estao em fallback decide se ele cabe no
    // orcamento de conexoes persistentes ou se vai para o polling.
    function tilePodeUsarStream(tile) {
        if (!wallEl || !tile) return true;
        var tiles = wallEl.querySelectorAll(".vms-tile.vms-using-snapshot-fallback");
        var index = Array.prototype.indexOf.call(tiles, tile);
        return index < 0 || index < streamSlotBudget();
    }

    function refreshTileSnapshotFallback(tile, podeUsarStream) {
        var image = tile ?tile.querySelector(".vms-snapshot-fallback") : null;
        if (!image) return;
        var base = image.getAttribute("data-snapshot-base") || "";
        if (!base) return;
        if (typeof podeUsarStream === "undefined") {
            podeUsarStream = tilePodeUsarStream(tile);
        }
        var cameraId = tile.getAttribute("data-camera-id") || "";
        if (cameraId && podeUsarStream === false) {
            refreshTileSnapshotFallbackPolling(tile, image, cameraId);
            return;
        }
        clearSnapshotPollTimer(image);
        image.onload = null;
        tile.setAttribute("data-fonte", videoHelperAvailable ?"helper" : "stream-raw");
        // /cameras/{id}/stream serve o frame PROCESSADO pela IA. Com a camera parada
        // nao existe frame processado e o backend devolve o cartaz "SEM FRAME
        // PROCESSADO" - a tela preta. O preview cru le o RTSP direto e funciona
        // independente do runtime, entao e ele quem vale enquanto a IA nao roda.
        var camera = cameraById.get(String(cameraId));
        if (cameraId && camera && !camera.is_running) {
            base = "/cameras/" + encodeURIComponent(cameraId) + "/stream/raw";
        }
        var useHelper = videoHelperAvailable && cameraId && image.getAttribute("data-helper-failed") !== cameraId;
        var target = useHelper ?videoHelperStreamUrl(cameraId) : base;
        // Com o helper ativo o tile tem video de verdade, nao modo degradado:
        // a classe abaixo desliga o aviso amarelo de compatibilidade.
        tile.classList.toggle("vms-helper-stream", !!useHelper);
        if (useHelper) {
            image.onerror = function () {
                image.setAttribute("data-helper-failed", cameraId);
                image.onerror = null;
                tile.classList.remove("vms-helper-stream");
                image.setAttribute("src", base);
                if (image._sunorusHelperRetryTimer) {
                    clearTimeout(image._sunorusHelperRetryTimer);
                }
                image._sunorusHelperRetryTimer = setTimeout(function () {
                    image._sunorusHelperRetryTimer = null;
                    if (!document.documentElement.contains(image)
                        || !tile.classList.contains("vms-using-snapshot-fallback")) {
                        return;
                    }
                    image.removeAttribute("data-helper-failed");
                    refreshTileSnapshotFallback(tile);
                }, 5000);
            };
        } else {
            image.onerror = null;
        }
        if (image.getAttribute("src") !== target) {
            image.setAttribute("src", target);
        }
        sincronizarSeloFonteImagem(tile);
    }

    function refreshSnapshotFallbacks() {
        if (!wallEl || disposed || document.hidden) return;
        var tiles = wallEl.querySelectorAll(".vms-tile.vms-using-snapshot-fallback");
        // Os primeiros tiles ficam com stream continuo (fluido); o excedente cai
        // no polling de snapshot para nao estourar o teto de conexoes do browser.
        Array.prototype.forEach.call(tiles, function (tile, index) {
            refreshTileSnapshotFallback(tile, index < streamSlotBudget());
        });
    }

    function updateSnapshotFallbackNotice() {
        if (!codecFallbackNoticeEl || !wallEl) return;
        if (webrtcPureMode) {
            codecFallbackNoticeEl.hidden = true;
            return;
        }
        var count = wallEl.querySelectorAll(".vms-tile.vms-using-snapshot-fallback").length;
        var precisaHelper = precisaInstalarVideoHelper();
        // Sem HEVC e sem helper o aviso aparece mesmo antes de algum tile cair no
        // MJPEG: e justamente ai que o operador precisa saber o que instalar.
        codecFallbackNoticeEl.hidden = count <= 0 && !precisaHelper;
        var titleEl = codecFallbackNoticeEl.querySelector("[data-fallback-title]");
        var messageEl = codecFallbackNoticeEl.querySelector("[data-fallback-message]");
        var detailEl = codecFallbackNoticeEl.querySelector("[data-fallback-detail]");
        var installLinkEl = codecFallbackNoticeEl.querySelector("[data-hevc-install-link]");
        var downloadEl = codecFallbackNoticeEl.querySelector("[data-helper-download]");
        var helperActiveCount = Array.prototype.filter.call(
            wallEl.querySelectorAll(".vms-tile.vms-using-snapshot-fallback .vms-snapshot-fallback"),
            function (image) {
                return isVideoHelperUrl(image.getAttribute("src") || "");
            }
        ).length;
        var helperActive = videoHelperAvailable && helperActiveCount > 0;
        if (precisaHelper) loadVideoHelperDownload();
        if (titleEl) {
            titleEl.textContent = helperActive
                ?"SunOrus Video Helper ativo."
                : "Mosaico em modo de compatibilidade.";
        }
        if (installLinkEl) {
            installLinkEl.hidden = helperActive;
        }
        if (detailEl) {
            detailEl.hidden = helperActive;
        }
        if (downloadEl) {
            var podeBaixar = precisaHelper && !!videoHelperDownload;
            downloadEl.hidden = !podeBaixar;
            if (podeBaixar) {
                downloadEl.setAttribute("href", videoHelperDownload.url || "/downloads/video-helper/setup");
                downloadEl.textContent = "Baixar o Video Helper ("
                    + videoHelperDownload.tamanho_mb + " MB)";
                downloadEl.title = "Versao " + videoHelperDownload.versao
                    + " - execute o arquivo baixado e atualize esta pagina.";
            }
        }
        if (messageEl) {
            messageEl.textContent = helperActive
                ?(helperActiveCount === 1
                    ?"1 camera H.265 esta sendo reproduzida pelo decoder local."
                    : helperActiveCount + " cameras H.265 estao sendo reproduzidas pelo decoder local.")
                :(count === 0
                    ?"Cameras H.265 vao cair em compatibilidade MJPEG neste navegador."
                    : count === 1
                        ?"1 camera esta usando compatibilidade MJPEG porque o navegador nao suporta HEVC."
                        : count + " cameras estao usando compatibilidade MJPEG porque o navegador nao suporta HEVC.");
        }
    }

    var webrtcDiagnosticsRunning = false;

    async function pollWebrtcDiagnostics() {
        if (webrtcPureMode) return;
        if (webrtcDiagnosticsRunning || disposed || document.hidden) return;
        var ids = [];
        (assignments || []).forEach(function (cameraId, slotIndex) {
            if (!cameraId || cameraStreamMode(slotIndex) !== "webrtc") return;
            var camera = cameraById.get(String(cameraId));
            if (isCameraWebrtcReady(camera) && ids.indexOf(String(cameraId)) === -1) {
                ids.push(String(cameraId));
            }
        });
        if (!ids.length) return;

        webrtcDiagnosticsRunning = true;
        try {
            var params = new URLSearchParams();
            params.set("camera_ids", ids.join(","));
            params.set("_ts", String(Date.now()));
            var response = await fetch("/monitor/webrtc/diagnostics?" + params.toString(), {
                cache: "no-store",
                headers: { "Cache-Control": "no-cache" }
            });
            if (!response.ok) return;
            var data = await response.json();
            var items = Array.isArray(data.items) ?data.items : [];
            items.forEach(function (item) {
                var camera = cameraById.get(String(item.id));
                if (!camera) return;
                updateCameraPlayerGeometry(camera, item.diagnostics || {});
                var health = cameraOperationalHealth(camera);
                health.player = summarizePlayerFromDiagnostics(camera, item.diagnostics || {});
                camera.operational_health = health;
                updateCameraOperationalBadges(camera.id);
            });
            scheduleWallVisualLayerSync();
        } catch (error) {
            console.warn("Falha no diagnostico WebRTC", error);
        } finally {
            webrtcDiagnosticsRunning = false;
        }
    }

    function setAlarmRefreshMeta(kind, text) {
        if (!alarmRefreshMetaEl) return;

        alarmRefreshMetaEl.classList.remove("alarm-refresh-ok", "alarm-refresh-warn", "alarm-refresh-error");

        if (kind === "ok") alarmRefreshMetaEl.classList.add("alarm-refresh-ok");
        if (kind === "warn") alarmRefreshMetaEl.classList.add("alarm-refresh-warn");
        if (kind === "error") alarmRefreshMetaEl.classList.add("alarm-refresh-error");

        alarmRefreshMetaEl.textContent = text;
    }

    function formatNowLabel() {
        var now = new Date();
        var hh = String(now.getHours()).padStart(2, "0");
        var mm = String(now.getMinutes()).padStart(2, "0");
        var ss = String(now.getSeconds()).padStart(2, "0");
        return hh + ":" + mm + ":" + ss;
    }

    function clearStreamBootTimeouts() {
        while (streamBootTimeouts.length) {
            clearTimeout(streamBootTimeouts.pop());
        }
    }

    function setWebrtcFrameSrc(frame, options) {
        if (!frame) return;
        var base = frame.getAttribute("data-stream-base") || "";
        if (!base) return;

        var src = base;
        if (options && options.reload) {
            src += (base.indexOf("?") >= 0 ?"&" : "?") + "_reload=" + Date.now();
        }

        if (!(options && options.force) && frame.getAttribute("src") === src) {
            frame.removeAttribute("data-stream-pending");
            return;
        }

        frame.setAttribute("src", src);
        frame.removeAttribute("data-stream-pending");
    }

    function teardownWallStreams() {
        clearStreamBootTimeouts();
    }

    function hydrateWallStreams(options) {
        clearStreamBootTimeouts();
        if (!wallEl) return;

        var stagger = !!(options && options.stagger);
        var frames = wallEl.querySelectorAll(".vms-webrtc-frame[data-stream-base]");
        Array.prototype.forEach.call(frames, function (frame, index) {
            var pending = frame.getAttribute("data-stream-pending") === "1";
            if (!pending) return;

            var delay = stagger ?Math.min(index * 25, 400) : 0;
            var timerId = setTimeout(function () {
                setWebrtcFrameSrc(frame, { force: pending });
            }, delay);
            streamBootTimeouts.push(timerId);

        });
    }

    function replaceCameraInSlot(targetIndex, cameraId, sourceSlotIndex) {
        if (layoutLocked) return;
        stopAllSequences(true);

        var normalizedId = String(cameraId);

        if (!cameraById.has(normalizedId)) return;

        if (sourceSlotIndex !== null && sourceSlotIndex !== undefined && !Number.isNaN(sourceSlotIndex)) {
            if (sourceSlotIndex === targetIndex) return;

            var targetCamera = assignments[targetIndex] || null;
            assignments[targetIndex] = normalizedId;
            assignments[sourceSlotIndex] = targetCamera === normalizedId ?null : targetCamera;
        } else {
            var existingIndex = assignments.findIndex(function (id) {
                return id === normalizedId;
            });

            if (existingIndex >= 0 && existingIndex !== targetIndex) {
                assignments[existingIndex] = null;
            }

            assignments[targetIndex] = normalizedId;
        }

        assignments = clampAssignments(assignments, { keepUnknown: true });
        persistAssignments();
        setSelectedCamera(normalizedId, { fetch: true });
        renderWall();
    }

    function safePlayAlarmTone() {
        try {
            if (typeof playAlarmTone === "function") {
                playAlarmTone();
            }
        } catch (error) {
            console.warn("Falha ao tocar alarme", error);
        }
    }

    function safeShowAlarmPopup(alarm) {
        try {
            if (!isLiveAlarmEligible(alarm)) {
                return;
            }
            if (openLiveAlarmModalNow(alarm)) {
                return;
            }
            if (alarm && typeof showAlarmPopup === "function") {
                showAlarmPopup(alarm);
            }
        } catch (error) {
            console.warn("Falha ao abrir popup de alarme", error);
        }
    }

    // Erro de rede (fetch rejeitado) nao tem status HTTP: o browser so devolve
    // "Failed to fetch". Sem esta distincao fica impossivel saber se o backend
    // recusou a acao ou se a requisicao nem saiu do navegador.
    var API_NETWORK_HINT = "servidor inacessivel (backend fora do ar, reiniciando ou rede/proxy bloqueando).";

    async function readResponseDetail(response) {
        var raw = "";
        try {
            raw = await response.text();
        } catch (error) {
            return "";
        }
        if (!raw) return "";
        try {
            var payload = JSON.parse(raw);
            var detail = payload && payload.detail;
            if (Array.isArray(detail)) {
                return detail.map(function (item) {
                    return safeString(item && item.msg) || JSON.stringify(item);
                }).join("; ");
            }
            if (detail && typeof detail === "object") return JSON.stringify(detail);
            return safeString(detail) || safeString(payload && payload.error) || raw.slice(0, 300);
        } catch (error) {
            return raw.slice(0, 300);
        }
    }

    function httpFailureMessage(status, detail) {
        var base;
        if (status === 401) base = "sessao expirada, faca login novamente.";
        else if (status === 403) base = "sem permissao para esta acao (requer admin ou supervisor).";
        else if (status === 404) base = "recurso nao encontrado no servidor.";
        else if (status >= 500) base = "erro interno no servidor (consulte os logs do backend).";
        else base = "o servidor recusou a requisicao.";
        return base + " [HTTP " + status + "]" + (detail ?" " + detail : "");
    }

    async function requestApi(url, options, actionLabel, tolerateStatuses) {
        var method = (options && options.method) || "GET";
        var startedAt = Date.now();
        var response;

        try {
            response = await fetch(url, options || {});
        } catch (error) {
            console.error("[monitor] " + actionLabel + ": sem resposta do servidor", {
                url: url,
                method: method,
                elapsedMs: Date.now() - startedAt,
                online: navigator.onLine,
                error: error
            });
            var networkError = new Error(actionLabel + ": " + API_NETWORK_HINT);
            networkError.isNetworkFailure = true;
            throw networkError;
        }

        var elapsedMs = Date.now() - startedAt;
        var tolerated = Array.isArray(tolerateStatuses) && tolerateStatuses.indexOf(response.status) !== -1;

        if (!response.ok && !tolerated) {
            var detail = await readResponseDetail(response);
            console.error("[monitor] " + actionLabel + ": erro HTTP", {
                url: url,
                method: method,
                status: response.status,
                detail: detail,
                elapsedMs: elapsedMs
            });
            var httpError = new Error(actionLabel + ": " + httpFailureMessage(response.status, detail));
            httpError.status = response.status;
            httpError.detail = detail;
            throw httpError;
        }

        console.debug("[monitor] " + actionLabel + ": ok", {
            url: url,
            method: method,
            status: response.status,
            elapsedMs: elapsedMs
        });
        return response;
    }

    async function controlCamera(cameraId, mode) {
        if (!canControlCamera) {
            return;
        }

        var isStop = mode === "stop";
        var actionLabel = (isStop ?"parar camera " : "iniciar camera ") + safeString(cameraId);
        var url = isStop
            ? "/api/cameras/" + encodeURIComponent(cameraId) + "/stop"
            : "/api/cameras/" + encodeURIComponent(cameraId) + "/start?use_motion_test=true";

        try {
            // Parar uma camera que ja nao esta rodando (404) segue sendo sucesso.
            await requestApi(url, { method: "POST" }, actionLabel, isStop ?[404] : []);

            setTimeout(function () {
                pollMonitor(true);
            }, 900);
        } catch (error) {
            alert(safeString(error && error.message) || "Nao foi possivel controlar a camera.");
        }
    }

    function closeTileMenus() {
        document.querySelectorAll(".vms-tile-menu.is-open").forEach(function (menu) {
            menu.classList.remove("is-open");
        });
    }

    function cleanupNavigationResources() {
        if (pageCleanupDone) return;
        pageCleanupDone = true;
        disposed = true;
        if (pendingTrackAnimationFrame != null) {
            window.cancelAnimationFrame(pendingTrackAnimationFrame);
            pendingTrackAnimationFrame = null;
        }
        pendingTrackUpdates.clear();

        if (tileClickTimerId) {
            clearTimeout(tileClickTimerId);
            tileClickTimerId = null;
        }

        teardownWallStreams();
        clearLiveAlarmVideo();

        if (videoHelperRetryTimerId) {
            clearTimeout(videoHelperRetryTimerId);
            videoHelperRetryTimerId = null;
        }

        if (wallEl) {
            Array.prototype.forEach.call(
                wallEl.querySelectorAll(".vms-snapshot-fallback"),
                function (image) {
                    clearSnapshotPollTimer(image);
                    image.onload = null;
                    image.onerror = null;
                }
            );
        }

        if (libraryPollTimerId) {
            clearInterval(libraryPollTimerId);
            libraryPollTimerId = null;
        }

        if (monitorPollTimerId) {
            clearInterval(monitorPollTimerId);
            monitorPollTimerId = null;
        }

        if (alarmPollTimerId) {
            clearInterval(alarmPollTimerId);
            alarmPollTimerId = null;
        }

        if (tracksPollTimerId) {
            clearInterval(tracksPollTimerId);
            tracksPollTimerId = null;
        }
        closeTrackSse();
        if (tracksSseReconnectTimerId) {
            clearTimeout(tracksSseReconnectTimerId);
            tracksSseReconnectTimerId = null;
        }

        if (webrtcDiagnosticsTimerId) {
            clearInterval(webrtcDiagnosticsTimerId);
            webrtcDiagnosticsTimerId = null;
        }

        if (snapshotFallbackTimerId) {
            clearInterval(snapshotFallbackTimerId);
            snapshotFallbackTimerId = null;
        }

        if (sequenceTimerId) {
            clearTimeout(sequenceTimerId);
            sequenceTimerId = null;
        }

    }

    function stopPollingTimers() {
        if (libraryPollTimerId) {
            clearInterval(libraryPollTimerId);
            libraryPollTimerId = null;
        }

        if (monitorPollTimerId) {
            clearInterval(monitorPollTimerId);
            monitorPollTimerId = null;
        }

        if (alarmPollTimerId) {
            clearInterval(alarmPollTimerId);
            alarmPollTimerId = null;
        }

        if (tracksPollTimerId) {
            clearInterval(tracksPollTimerId);
            tracksPollTimerId = null;
        }
        closeTrackSse();
        if (tracksSseReconnectTimerId) {
            clearTimeout(tracksSseReconnectTimerId);
            tracksSseReconnectTimerId = null;
        }

        if (webrtcDiagnosticsTimerId) {
            clearInterval(webrtcDiagnosticsTimerId);
            webrtcDiagnosticsTimerId = null;
        }

        if (snapshotFallbackTimerId) {
            clearInterval(snapshotFallbackTimerId);
            snapshotFallbackTimerId = null;
        }

        if (sequenceTimerId) {
            clearTimeout(sequenceTimerId);
            sequenceTimerId = null;
        }
    }

    function startPollingTimers() {
        if (disposed || document.hidden) return;
        ensureTrackSse();

        if (!libraryPollTimerId) {
            libraryPollTimerId = setInterval(function () {
                pollLibrary(false);
            }, libraryPollIntervalMs);
        }

        if (!monitorPollTimerId) {
            monitorPollTimerId = setInterval(function () {
                pollMonitor(false);
            }, pollIntervalMs);
        }

        if (!alarmPollTimerId) {
            alarmPollTimerId = setInterval(function () {
                pollAlarmQueue();
            }, alarmPollIntervalMs);
        }

        if (!tracksPollTimerId) {
            tracksPollTimerId = setInterval(function () {
                pollTrackBoxes();
            }, tracksPollIntervalMs);
        }

        if (!webrtcPureMode && !webrtcDiagnosticsTimerId) {
            webrtcDiagnosticsTimerId = setInterval(function () {
                pollWebrtcDiagnostics();
            }, webrtcDiagnosticsPollIntervalMs);
        }

        if (!webrtcPureMode && !snapshotFallbackTimerId) {
            snapshotFallbackTimerId = setInterval(function () {
                // Tiles sao re-renderizados a cada ciclo do monitor; sem reavaliar,
                // a marcacao de fallback do helper se perderia.
                applyDefaultVideoHelperFallback();
                refreshSnapshotFallbacks();
            }, snapshotFallbackIntervalMs);
        }

        scheduleSequencePage();
    }

    function updateFullscreenButtonLabel() {
        if (!fullscreenBtn) return;
        fullscreenBtn.textContent = isWallFullscreen() ?"Sair da tela cheia" : "Tela cheia";
    }

    function handleFullscreenChange() {
        updateFullscreenButtonLabel();
        if (liveAlarmCurrent && liveAlarmModalEl && !liveAlarmModalEl.hidden) {
            ensureLiveAlarmModalHost();
        }
        if (!isWallFullscreen() && focusRestoreState) {
            restoreFocusedCamera();
        }
    }

    async function toggleFullscreen() {
        try {
            if (isWallFullscreen()) {
                if (document.exitFullscreen) {
                    await document.exitFullscreen();
                }
                return;
            }

            if (!wallEl) return;
            if (document.fullscreenElement && document.exitFullscreen) {
                await document.exitFullscreen();
            }

            await requestWallFullscreen();
        } catch (error) {}
    }

    function syncMuteButtonState() {
        if (!globalMuteToggle) return;
        var isMuted = localStorage.getItem("alarmAudioMuted") === "true";
        globalMuteToggle.textContent = isMuted ? "🔇 Aviso sonoro: Desativado" : "🔊 Aviso sonoro: Ativo";
        globalMuteToggle.classList.toggle("btn-danger", isMuted);
        globalMuteToggle.classList.toggle("btn-primary", !isMuted);
        globalMuteToggle.classList.toggle("btn-secondary", false);
    }

    function bindInteractions() {
        if (moreOptionsEl) {
            moreOptionsEl.addEventListener("toggle", syncMoreOptionsOpenState);
            syncMoreOptionsOpenState();
        }

        if (searchInputEl) {
            searchInputEl.addEventListener("input", function (event) {
                cameraSearch = event.target.value || "";
                renderLibrary();
            });
        }

        if (autoFillBtn) {
            autoFillBtn.addEventListener("click", autoFillAssignments);
        }

        if (autoFillVisibleBtn) {
            autoFillVisibleBtn.addEventListener("click", autoFillVisibleAssignments);
        }

        if (autoFillAlarmBtn) {
            autoFillAlarmBtn.addEventListener("click", autoFillAlarmAssignments);
        }

        if (testCentralPopupBtn) {
            testCentralPopupBtn.addEventListener("click", triggerDevCentralPopup);
        }

        if (liveAlarmSnapshotBtn) {
            liveAlarmSnapshotBtn.addEventListener("click", function () {
                openLiveAlarmEvidence("snapshot");
            });
        }

        if (liveAlarmClipBtn) {
            liveAlarmClipBtn.addEventListener("click", function () {
                openLiveAlarmEvidence("clip");
            });
        }

        if (liveAlarmEvidenceCloseBtn) {
            liveAlarmEvidenceCloseBtn.addEventListener("click", closeLiveAlarmEvidence);
        }

        Array.prototype.forEach.call(libraryFilterButtons || [], function (button) {
            button.addEventListener("click", function () {
                libraryFilter = button.getAttribute("data-library-filter") || "all";
                updateLibraryFilterUi();
                renderLibrary();
            });
        });

        if (sequenceToggle) {
            sequenceToggle.addEventListener("click", function () {
                if (sequenceEnabled || isTemporalSequenceActive()) {
                    stopAllSequences(true);
                } else {
                    setSequenceEnabled(true);
                }
            });
        }

        if (classicSeqPlay) {
            classicSeqPlay.addEventListener("click", function () {
                if (temporalSequenceSelect && temporalSequenceSelect.value) {
                    applySelectedTemporalSequence();
                } else {
                    setSequenceEnabled(true);
                }
            });
        }

        if (classicSeqPause) {
            classicSeqPause.addEventListener("click", function () {
                stopAllSequences(true);
            });
        }

        if (classicSeqNext) {
            classicSeqNext.addEventListener("click", function () {
                if (isTemporalSequenceActive()) {
                    advanceTemporalSequenceNext();
                } else {
                    stopTemporalSequence();
                    applySequencePage();
                    scheduleSequencePage();
                }
            });
        }

        if (layoutLockToggle) {
            layoutLockToggle.addEventListener("click", function () {
                setLayoutLocked(!layoutLocked);
            });
        }

        if (clearLayoutBtn) {
            clearLayoutBtn.addEventListener("click", clearAssignments);
        }

        document.addEventListener("click", function (event) {
            var btn = event.target.closest("[data-alarm-action-type]");
            if (btn) {
                event.preventDefault();
                var eventId = btn.getAttribute("data-alarm-id");
                var action = btn.getAttribute("data-alarm-action-type");
                if (eventId && action) {
                    btn.disabled = true;
                    postAlarmActionById(eventId, action);
                }
            }
        });


        if (fullscreenBtn) {
            fullscreenBtn.addEventListener("click", toggleFullscreen);
        }

        if (globalMuteToggle) {
            globalMuteToggle.addEventListener("click", function () {
                var isMuted = localStorage.getItem("alarmAudioMuted") === "true";
                localStorage.setItem("alarmAudioMuted", isMuted ?"false" : "true");
                syncMuteButtonState();
            });
        }

        if (operatorModeToggle) {
            operatorModeToggle.addEventListener("click", function () {
                setOperatorMode(!operatorModeEnabled);
            });
        }

        if (tileDetailsToggle) {
            tileDetailsToggle.addEventListener("click", function () {
                setTileDetailsEnabled(!tileDetailsEnabled);
            });
        }

        if (tileHeadersToggle) {
            tileHeadersToggle.addEventListener("click", function () {
                setTileHeadersFixed(!tileHeadersFixed);
            });
        }

        if (webrtcPureToggle) {
            webrtcPureToggle.addEventListener("click", function () {
                setWebrtcPureMode(!webrtcPureMode);
            });
        }

        if (overlayToggleBtn) {
            overlayToggleBtn.addEventListener("click", function () {
                setOverlaysEnabled(!overlaysEnabled);
            });
        }

        if (boxesToggleBtn) {
            boxesToggleBtn.addEventListener("click", function () {
                setBoxesEnabled(!boxesEnabled);
            });
        }

        var activeSandboxResizeState = null;

        if (wallEl) {
            wallEl.addEventListener("click", function (e) {
                var btn = e.target.closest(".vms-span-btn");
                if (!btn) return;
                var slotIndex = Number(btn.getAttribute("data-slot-index"));
                var action = btn.getAttribute("data-span-action");
                var span = getTileSpan(slotIndex);
                if (action === "col-minus") span.colSpan = Math.max(1, span.colSpan - 1);
                if (action === "col-plus") span.colSpan = Math.min(12, span.colSpan + 1);
                if (action === "row-minus") span.rowSpan = Math.max(1, span.rowSpan - 1);
                if (action === "row-plus") span.rowSpan = Math.min(12, span.rowSpan + 1);
                setTileSpan(slotIndex, span.colSpan, span.rowSpan);
                renderWall();
            });

            wallEl.addEventListener("mousedown", function (e) {
                var handle = e.target.closest(".vms-sandbox-resize-handle");
                if (!handle) return;
                e.preventDefault();
                e.stopPropagation();

                var slotIndex = Number(handle.getAttribute("data-slot-index"));
                var slotEl = handle.closest(".vms-slot");
                if (!slotEl) return;

                var startSpan = getTileSpan(slotIndex);
                var colWidth = Math.max(40, (wallEl.clientWidth - 12) / 12);
                var rowHeight = Math.max(60, slotEl.clientHeight / startSpan.rowSpan);

                activeSandboxResizeState = {
                    slotIndex: slotIndex,
                    slotEl: slotEl,
                    handle: handle,
                    startX: e.clientX,
                    startY: e.clientY,
                    startColSpan: startSpan.colSpan,
                    startRowSpan: startSpan.rowSpan,
                    colWidth: colWidth,
                    rowHeight: rowHeight
                };

                handle.classList.add("is-dragging");
                document.body.style.cursor = "se-resize";
                document.body.style.userSelect = "none";
            });
        }

        document.addEventListener("mousemove", function (e) {
            if (!activeSandboxResizeState) return;
            var state = activeSandboxResizeState;
            var deltaX = e.clientX - state.startX;
            var deltaY = e.clientY - state.startY;

            var colDelta = Math.round(deltaX / state.colWidth);
            var rowDelta = Math.round(deltaY / state.rowHeight);

            var newCol = Math.max(1, Math.min(12, state.startColSpan + colDelta));
            var newRow = Math.max(1, Math.min(12, state.startRowSpan + rowDelta));

            state.slotEl.style.gridColumn = "span " + newCol;
            state.slotEl.style.gridRow = "span " + newRow;

            var badge = state.slotEl.querySelector(".vms-sandbox-dim-badge");
            if (badge) badge.textContent = newCol + "x" + newRow;
        });

        document.addEventListener("mouseup", function () {
            if (!activeSandboxResizeState) return;
            var state = activeSandboxResizeState;
            state.handle.classList.remove("is-dragging");
            document.body.style.cursor = "";
            document.body.style.userSelect = "";

            var styleCol = state.slotEl.style.gridColumn;
            var styleRow = state.slotEl.style.gridRow;
            var colMatch = styleCol.match(/span\s+(\d+)/);
            var rowMatch = styleRow.match(/span\s+(\d+)/);

            var finalCol = colMatch ?Number(colMatch[1]) : state.startColSpan;
            var finalRow = rowMatch ?Number(rowMatch[1]) : state.startRowSpan;

            setTileSpan(state.slotIndex, finalCol, finalRow);
            activeSandboxResizeState = null;
            renderWall();
        });

        if (boxConfidenceInput) {
            boxConfidenceInput.addEventListener("change", function () {
                setBoxConfidencePercent(boxConfidenceInput.value, true);
            });
            boxConfidenceInput.addEventListener("keydown", function (event) {
                event.stopPropagation();
            });
        }

        if (layoutModeSelect) {
            layoutModeSelect.addEventListener("change", function () {
                setLayoutMode(layoutModeSelect.value);
            });
        }

        if (sandboxAddTileBtn) {
            sandboxAddTileBtn.addEventListener("click", function () {
                var nextGrid = selectedGrid + 1;
                selectedGrid = nextGrid;
                persistGridMemory(nextGrid);
                renderWall();
            });
        }

        if (sandboxRemoveTileBtn) {
            sandboxRemoveTileBtn.addEventListener("click", function () {
                if (selectedGrid <= 1) return;
                var nextGrid = selectedGrid - 1;
                selectedGrid = nextGrid;
                assignments[selectedGrid] = "";
                persistAssignments();
                persistGridMemory(nextGrid);
                renderWall();
            });
        }

        if (videoFitToggle) {
            videoFitToggle.addEventListener("click", function () {
                setVideoFitMode(videoFitMode === "fill" ?"fit" : "fill");
            });
        }

        if (densityToggle) {
            densityToggle.addEventListener("click", function () {
                setDensityMode(densityMode === "compact" ?"normal" : "compact");
            });
        }

        if (spotlightToggle) {
            spotlightToggle.addEventListener("click", function () {
                setSpotlightEnabled(!spotlightEnabled);
            });
        }

        if (saveViewBtn) {
            saveViewBtn.addEventListener("click", saveCurrentView);
        }

        if (savedViewSelect) {
            savedViewSelect.addEventListener("change", function() {
                stopAllSequences(true);
                applySelectedSavedView();
                updateSavedViewActions();
            });
        }

        if (temporalSequenceSelect) {
            temporalSequenceSelect.addEventListener("change", applySelectedTemporalSequence);
        }

        if (stopTemporalSequenceBtn) {
            stopTemporalSequenceBtn.addEventListener("click", function () {
                stopAllSequences(true);
            });
        }

        if (deleteViewBtn) {
            deleteViewBtn.addEventListener("click", deleteSelectedSavedView);
        }

        document.addEventListener("fullscreenchange", handleFullscreenChange);
        window.addEventListener("resize", scheduleWallVisualLayerSync);

        document.addEventListener("visibilitychange", function () {
            if (document.hidden) {
                stopPollingTimers();
                teardownWallStreams();
                return;
            }

            disposed = false;
            pageCleanupDone = false;
            hydrateWallStreams({ stagger: true, forceRefresh: true });
            startPollingTimers();
            pollLibrary(true);
            pollMonitor(true);
            pollAlarmQueue();
            pollTrackBoxes();
            if (isTemporalSequenceActive()) {
                initTemporalSequencePlayback();
            } else {
                scheduleSequencePage();
            }
        });

        window.addEventListener("pagehide", cleanupNavigationResources);
        window.addEventListener("beforeunload", cleanupNavigationResources);

        document.addEventListener("click", function (event) {
            var link = event.target.closest("a[href]");
            if (!link) return;
            if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            if (link.target === "_blank" || link.hasAttribute("download")) return;

            var href = link.getAttribute("href") || "";
            if (!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0) return;

            try {
                var url = new URL(link.href, window.location.href);
                if (url.origin !== window.location.origin) return;
            } catch (error) {
                return;
            }

            cleanupNavigationResources();
        }, true);

        document.addEventListener("click", function (event) {
            if (!event.target.closest(".vms-tile-menu")) {
                closeTileMenus();
            }
            if (moreOptionsEl && moreOptionsEl.open && !moreOptionsEl.contains(event.target)) {
                moreOptionsEl.open = false;
                syncMoreOptionsOpenState();
            }
        });

        document.addEventListener("submit", function () {
            cleanupNavigationResources();
        }, true);

        Array.prototype.forEach.call(gridButtons, function (button) {
            button.addEventListener("click", function () {
                applyGridPreset(Number(button.dataset.gridPreset || selectedGrid));
            });
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && evidenceModalEl && !evidenceModalEl.hidden) {
                event.preventDefault();
                closeEvidenceModal();
                return;
            }

            if (event.key === "Escape" && shortcutModalEl && !shortcutModalEl.hidden) {
                event.preventDefault();
                shortcutModalEl.hidden = true;
                return;
            }

            if (event.key === "Escape" && moreOptionsEl && moreOptionsEl.open) {
                event.preventDefault();
                moreOptionsEl.open = false;
                syncMoreOptionsOpenState();
                var moreOptionsSummary = moreOptionsEl.querySelector("summary");
                if (moreOptionsSummary) moreOptionsSummary.focus();
                return;
            }

            if (isEditableShortcutTarget(event.target)) {
                return;
            }

            if (event.altKey && ["1", "2", "3", "4", "5", "6", "7", "8", "9"].indexOf(event.key) >= 0) {
                event.preventDefault();
                var slotIdx = Number(event.key) - 1;
                if (slotIdx >= 0 && slotIdx < assignments.length) {
                    var camId = assignments[slotIdx];
                    if (camId) {
                        if (focusRestoreState) {
                            if (assignments[0] === String(camId)) {
                                restoreFocusedCamera();
                            } else {
                                var oldState = focusRestoreState;
                                focusRestoreState = null;
                                enterFocusedCamera(camId, oldState.wasFullscreen);
                                focusRestoreState.grid = oldState.grid;
                                focusRestoreState.assignments = oldState.assignments;
                            }
                        } else {
                            enterFocusedCamera(camId, false);
                        }
                    }
                }
                return;
            }

            if (!event.ctrlKey && !event.altKey && (event.key === "s" || event.key === "S")) {
                event.preventDefault();
                var isAudioMuted = localStorage.getItem("alarmAudioMuted") === "true";
                localStorage.setItem("alarmAudioMuted", isAudioMuted ?"false" : "true");
                syncMuteButtonState();
                return;
            }

            if (!event.ctrlKey && (event.key === "m" || event.key === "M")) {
                event.preventDefault();
                setLeftPanel(currentLeftPanel === "map" ?"library" : "map");
                return;
            }

            if (!event.ctrlKey && (event.key === "b" || event.key === "B")) {
                event.preventDefault();
                setBoxesEnabled(!boxesEnabled);
                return;
            }

            if (!event.ctrlKey && (event.key === "l" || event.key === "L")) {
                event.preventDefault();
                setLayoutLocked(!layoutLocked);
                return;
            }

            if (!event.ctrlKey && (event.key === "a" || event.key === "A")) {
                event.preventDefault();
                if (alarmSidebarEl) alarmSidebarEl.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
                return;
            }

            if (!event.ctrlKey && event.key === " ") {
                event.preventDefault();
                if (sequenceEnabled || isTemporalSequenceActive()) {
                    stopAllSequences(true);
                } else {
                    setSequenceEnabled(true);
                }
                return;
            }

            if (!event.ctrlKey && event.key === "Delete") {
                var selectedSlotIndex = Number(wallEl && wallEl.dataset.selectedSlotIndex);
                if (!layoutLocked && Number.isFinite(selectedSlotIndex) && assignments[selectedSlotIndex]) {
                    event.preventDefault();
                    assignments[selectedSlotIndex] = null;
                    persistAssignments();
                    renderWall();
                }
                return;
            }

            if (event.key === "Escape" && focusRestoreState && isWallFullscreen() && focusRestoreState.wasFullscreen) {
                event.preventDefault();
                event.stopPropagation();
                restoreFocusedCamera();
                return;
            }

            if (!event.ctrlKey) return;
            if (["1", "2", "4", "6", "8", "9", "0"].indexOf(event.key) >= 0) {
                event.preventDefault();
            }
            if (event.key === "1") applyGridPreset(1);
            if (event.key === "2") applyGridPreset(2);
            if (event.key === "4") applyGridPreset(4);
            if (event.key === "6") applyGridPreset(6);
            if (event.key === "8") applyGridPreset(8);
            if (event.key === "9") applyGridPreset(9);
            if (event.key === "0") applyGridPreset(16);
        });

        Array.prototype.forEach.call(leftPanelButtons || [], function (button) {
            button.addEventListener("click", function () {
                setLeftPanel(button.getAttribute("data-left-panel"));
            });
        });

        if (keyboardHelpBtn && shortcutModalEl) {
            keyboardHelpBtn.addEventListener("click", function () {
                shortcutModalEl.hidden = false;
            });
        }

        if (shortcutCloseBtn && shortcutModalEl) {
            shortcutCloseBtn.addEventListener("click", function () {
                shortcutModalEl.hidden = true;
            });
        }

        if (shortcutModalEl) {
            shortcutModalEl.addEventListener("click", function (event) {
                if (event.target === shortcutModalEl) {
                    shortcutModalEl.hidden = true;
                }
            });
        }

        if (evidenceCloseBtn) {
            evidenceCloseBtn.addEventListener("click", closeEvidenceModal);
        }

        if (evidenceModalEl) {
            evidenceModalEl.addEventListener("click", function (event) {
                if (event.target === evidenceModalEl) {
                    closeEvidenceModal();
                }
            });
        }

        if (liveAlarmAckBtn) {
            liveAlarmAckBtn.addEventListener("click", function () {
                postLiveAlarmAction("ack");
            });
        }

        if (liveAlarmAuthorizeBtn) {
            liveAlarmAuthorizeBtn.addEventListener("click", function () {
                postLiveAlarmAction("close", "authorized_activity");
            });
        }

        if (liveAlarmCloseEventBtn) {
            liveAlarmCloseEventBtn.addEventListener("click", function () {
                postLiveAlarmAction("close", "false_alarm");
            });
        }

    function initSseStream() {
        if (typeof EventSource === "undefined") return;
        try {
            var sse = new EventSource("/api/events/stream");
            sse.addEventListener("alarm", function (e) {
                try {
                    var alarm = JSON.parse(e.data);
                    if (alarm && alarm.id) {
                        enqueueLiveAlarm(alarm);
                    }
                } catch (err) {
                    console.warn("Erro ao ler evento SSE", err);
                }
            });
            sse.onerror = function () {
                // Reconexao automatica
            };
        } catch (err) {
            console.warn("Falha ao abrir EventSource SSE", err);
        }
    }

        initSseStream();

        if (liveAlarmPinBtn) {
            liveAlarmPinBtn.addEventListener("click", function () {
                if (!liveAlarmCurrent) return;
                var cameraId = alarmCameraId(liveAlarmCurrent);
                if (!cameraId) return;
                ensureCameraVisibleOnStage(cameraId, { allowAssign: true, scroll: true });
            });
        }

        if (liveAlarmMinimizeBtn) {
            liveAlarmMinimizeBtn.addEventListener("click", function () {
                closeLiveAlarmModal({ markHandled: true });
                showNextLiveAlarm();
            });
        }

        if (clearLiveAlarmQueueBtn) {
            clearLiveAlarmQueueBtn.addEventListener("click", clearLiveAlarmQueue);
        }

        if (ptzPanelEl) {
            // --- Recolher / Expandir Painel ---
            var toggleCollapseEl = document.getElementById('monitorPtzToggleCollapse');
            if (toggleCollapseEl) {
                toggleCollapseEl.addEventListener('click', function () {
                    ptzPanelEl.classList.toggle('is-collapsed');
                });
            }

            // --- Alternância de Abas (Teclado vs Joystick) ---
            var modeTabs = ptzPanelEl.querySelectorAll('.vms-ptz-tab-btn');
            var padEl = document.getElementById('monitorPtzPad');
            var joystickContainerEl = document.getElementById('monitorPtzJoystickContainer');
            var joystick3dButton = document.getElementById('monitorPtz3dJoystick');
            if (joystick3dButton) joystick3dButton.style.display = 'none';

            Array.prototype.forEach.call(modeTabs, function (tab) {
                tab.addEventListener('click', function () {
                    Array.prototype.forEach.call(modeTabs, function (t) {
                        t.classList.remove('active');
                        t.setAttribute('aria-selected', 'false');
                    });
                    tab.classList.add('active');
                    tab.setAttribute('aria-selected', 'true');

                    var mode = tab.getAttribute('data-ptz-mode');
                    if (mode === 'pad') {
                        if (padEl) padEl.style.display = '';
                        if (joystickContainerEl) joystickContainerEl.style.display = 'none';
                        if (joystick3dButton) joystick3dButton.style.display = 'none';
                        setSelectedCameraPtz3dEnabled(false);
                    } else if (mode === 'joystick') {
                        if (padEl) padEl.style.display = 'none';
                        if (joystickContainerEl) joystickContainerEl.style.display = 'flex';
                        if (joystick3dButton) joystick3dButton.style.display = '';
                    }
                });
            });

            // --- Controle Joystick Virtual ---
            var joystickBase = document.getElementById('monitorPtzJoystickBase');
            var joystickHandle = document.getElementById('monitorPtzJoystickHandle');

            if (joystickBase && joystickHandle) {
                var joystickActive = false;
                var joystickTargetPan = 0;
                var joystickTargetTilt = 0;
                var joystickInFlight = false;
                var joystickLastSentPan = null;
                var joystickLastSentTilt = null;
                var maxRadius = 50; // px limit matching styling scale

                function sendJoystickTarget() {
                    var cameraId = selectedCameraId;
                    if (!cameraId || !canControlCamera || !selectedCameraSupportsPtz() || !joystickActive) {
                        return;
                    }

                    if (joystickInFlight) {
                        return;
                    }

                    var pan = joystickTargetPan;
                    var tilt = joystickTargetTilt;

                    // If joystick is back to center, don't send continuous move commands
                    if (Math.abs(pan) < 0.01 && Math.abs(tilt) < 0.01) {
                        return;
                    }

                    joystickInFlight = true;
                    joystickLastSentPan = pan;
                    joystickLastSentTilt = tilt;

                    queueSelectedCameraPtzCommand(function () {
                        return postSelectedCameraPtzCommand(
                            "/monitor/cameras/" + encodeURIComponent(cameraId) + "/ptz/move",
                            {
                                pan: pan,
                                tilt: tilt,
                                zoom: 0
                            }
                        );
                    }).then(function () {
                        // Assim que o SDK confirmar o comando, envie o último
                        // alvo do joystick. A espera fixa de 200 ms fazia o
                        // arrasto parecer travado e descartava vários eventos
                        // de pointermove em câmeras com resposta mais lenta.
                        joystickInFlight = false;
                        sendJoystickTarget();
                    }).catch(function (error) {
                        console.warn("Erro no comando PTZ do joystick", error);
                        joystickInFlight = false;
                        sendJoystickTarget();
                    });
                }

                joystickBase.addEventListener('pointerdown', function (event) {
                    var camera = currentSelectedCamera();
                    var ptzInfo = selectedCameraPtzInfo || (selectedCameraId ?ptzInspectionCache[selectedCameraId] || null : null);
                    var supportsPtz = !!(ptzInfo && (ptzInfo.ptz_capable !== undefined ?ptzInfo.ptz_capable : ptzInfo.capabilities && ptzInfo.capabilities.ptz));
                    var buttonDisabled = !camera || selectedCameraPtzLoading || !supportsPtz || !canControlCamera;
                    if (buttonDisabled) return;

                    event.preventDefault();
                    try {
                        joystickBase.setPointerCapture(event.pointerId);
                    } catch (error) {}

                    joystickActive = true;
                    updateJoystickPosition(event);
                });

                joystickBase.addEventListener('pointermove', function (event) {
                    if (!joystickActive) return;
                    event.preventDefault();
                    updateJoystickPosition(event);
                });

                // Depois do pointer capture alguns navegadores deixam de
                // entregar todos os eventos ao elemento quando o cursor sai
                // do círculo. Continue acompanhando no documento enquanto o
                // gesto estiver ativo.
                document.addEventListener('pointermove', function (event) {
                    if (!joystickActive) return;
                    event.preventDefault();
                    updateJoystickPosition(event);
                }, { passive: false });

                function handlePointerRelease(event) {
                    if (!joystickActive) return;
                    joystickActive = false;

                    // Reset handle to center
                    joystickHandle.style.transform = 'translate3d(0, 0, 0)';

                    var shaftEl = document.getElementById('monitorPtzJoystickShaft');
                    if (shaftEl) {
                        shaftEl.style.height = '0px';
                        shaftEl.style.transform = 'rotate(0rad)';
                    }

                    joystickTargetPan = 0;
                    joystickTargetTilt = 0;
                    joystickLastSentPan = null;
                    joystickLastSentTilt = null;

                    // Send stop command immediately to stop continuous movement
                    stopSelectedCameraPtzMove(true).catch(function (error) {
                        console.warn("Falha ao parar PTZ no joystick release", error);
                    });
                }

                ['pointerup', 'pointercancel'].forEach(function (eventName) {
                    joystickBase.addEventListener(eventName, handlePointerRelease);
                });
                document.addEventListener('pointerup', handlePointerRelease);
                document.addEventListener('pointercancel', handlePointerRelease);

                function updateJoystickPosition(event) {
                    var rect = joystickBase.getBoundingClientRect();
                    var centerX = rect.left + rect.width / 2;
                    var centerY = rect.top + rect.height / 2;

                    var dx = event.clientX - centerX;
                    var dy = event.clientY - centerY;

                    var distance = Math.sqrt(dx * dx + dy * dy);

                    if (distance > maxRadius) {
                        dx = (dx / distance) * maxRadius;
                        dy = (dy / distance) * maxRadius;
                        distance = maxRadius;
                    }

                    // Move handle using translate3d for high performance
                    joystickHandle.style.transform = 'translate3d(' + dx.toFixed(1) + 'px, ' + dy.toFixed(1) + 'px, 0)';

                    var shaftEl = document.getElementById('monitorPtzJoystickShaft');
                    if (shaftEl) {
                        var angle = Math.atan2(dy, dx) - Math.PI / 2;
                        shaftEl.style.height = distance.toFixed(1) + 'px';
                        shaftEl.style.transform = 'rotate(' + angle.toFixed(3) + 'rad)';
                    }

                    // Map to velocity values between -1 and 1
                    var speedMultiplier = ptzMoveSpeed(); // respect the speed range slider
                    joystickTargetPan = (dx / maxRadius) * speedMultiplier;
                    joystickTargetTilt = -(dy / maxRadius) * speedMultiplier; // CSS Y is downward, PTZ tilt is upward positive

                    sendJoystickTarget();
                }
            }

            // --- Controle do Teclado (D-pad) ---
            Array.prototype.forEach.call(ptzPanelEl.querySelectorAll('[data-ptz-action="move"]'), function (button) {
                var pan = Number(button.getAttribute("data-pan") || 0);
                var tilt = Number(button.getAttribute("data-tilt") || 0);
                var zoom = Number(button.getAttribute("data-zoom") || 0);
                var repeatInterval = null;

                button.addEventListener("pointerdown", function (event) {
                    if (button.disabled) return;
                    event.preventDefault();
                    try {
                        button.setPointerCapture(event.pointerId);
                    } catch (error) {}
                    button.classList.add("is-active");

                    if (repeatInterval) {
                        clearInterval(repeatInterval);
                        repeatInterval = null;
                    }

                    // Send first command immediately
                    dpadMoveInFlight = true;
                    startSelectedCameraPtzMove(pan, tilt, zoom).then(function (data) {
                        dpadMoveInFlight = false;
                        // So reenvia periodicamente quando o backend confirmou que
                        // caiu no pulso de fallback (ver "continuous" na resposta de
                        // /ptz/move). ONVIF e o start/stop real do SDK nativo (as 3
                        // marcas) bastam com um unico comando ate o Stop no solta-o-
                        // botao; reenviar so reinicia a rampa do motor e da a
                        // sensacao de movimento "travado" a cada pulso.
                        var usesPulse = !!(data && data.backend === "native_sdk" && data.continuous === false);
                        if (usesPulse && !repeatInterval && button.classList.contains("is-active")) {
                            repeatInterval = setInterval(function () {
                                if (dpadMoveInFlight) {
                                    return;
                                }
                                if (button.classList.contains("is-active") && selectedCameraId) {
                                    dpadMoveInFlight = true;
                                    startSelectedCameraPtzMove(pan, tilt, zoom, true).then(function () {
                                        dpadMoveInFlight = false;
                                    }).catch(function (error) {
                                        console.warn("Falha ao repetir PTZ", error);
                                        dpadMoveInFlight = false;
                                    });
                                } else {
                                    clearInterval(repeatInterval);
                                    repeatInterval = null;
                                }
                            }, 200);
                        }
                    }).catch(function (error) {
                        console.warn("Falha ao iniciar PTZ", error);
                        dpadMoveInFlight = false;
                    });
                });

                ["pointerup", "pointerleave", "pointercancel"].forEach(function (eventName) {
                    button.addEventListener(eventName, function () {
                        button.classList.remove("is-active");
                        if (repeatInterval) {
                            clearInterval(repeatInterval);
                            repeatInterval = null;
                        }
                        stopSelectedCameraPtzMove();
                    });
                });
            });

            var ptzStopButton = ptzPanelEl.querySelector('[data-ptz-action="stop"]');
            if (ptzStopButton) {
                ptzStopButton.addEventListener("click", function () {
                    stopSelectedCameraPtzMove(true).catch(function (error) {
                        console.warn("Falha ao parar PTZ", error);
                    });
                });
            }

            Array.prototype.forEach.call(ptzPanelEl.querySelectorAll('[data-ptz-action="3d"]'), function (button) {
                button.addEventListener("click", function () {
                    if (button.disabled) return;
                    setSelectedCameraPtz3dEnabled(!ptz3dEnabled);
                });
            });
        }

        if (ptzSpeedEl && ptzSpeedValueEl) {
            ptzSpeedEl.addEventListener("input", function () {
                ptzSpeedValueEl.textContent = ptzMoveSpeed().toFixed(1);
            });
        }

        if (ptzReinspectEl) {
            ptzReinspectEl.addEventListener("click", function () {
                if (!selectedCameraId || selectedCameraPtzLoading) return;
                delete ptzInspectionCache[selectedCameraId];
                selectedCameraPtzInfo = null;
                selectedCameraPtzPresets = [];
                selectedCameraPtzPresetsCameraId = "";
                inspectSelectedCameraPtz(selectedCameraId, true);
            });
        }

        if (ptzPresetSelectEl) {
            ptzPresetSelectEl.addEventListener("change", function () {
                if (ptzPresetGoEl) {
                    ptzPresetGoEl.disabled = !safeString(ptzPresetSelectEl.value);
                }
            });
        }

        if (ptzPresetGoEl) {
            ptzPresetGoEl.addEventListener("click", gotoSelectedCameraPtzPreset);
        }

        if (ptzPresetRefreshEl) {
            ptzPresetRefreshEl.addEventListener("click", function () {
                refreshSelectedCameraPtzPresets(selectedCameraId);
            });
        }

        window.addEventListener("blur", function () {
            stopSelectedCameraPtzMove(true).catch(function (error) {
                console.warn("Falha ao parar PTZ no blur", error);
            });
        });

        document.addEventListener("visibilitychange", function () {
            if (document.hidden) {
                stopSelectedCameraPtzMove(true).catch(function (error) {
                    console.warn("Falha ao parar PTZ ao ocultar a página", error);
                });
            }
        });

        if (spotlightCloseBtn && spotlightEl) {
            spotlightCloseBtn.addEventListener("click", function () {
                resetSpotlight();
            });
        }

        if (spotlightRemoveBtn) {
            spotlightRemoveBtn.addEventListener("click", function () {
                resetSpotlight();
            });
        }

        if (spotlightAckBtn) {
            spotlightAckBtn.addEventListener("click", function () {
                acknowledgeSpotlightAlarm();
            });
        }

        if (spotlightPinBtn) {
            spotlightPinBtn.addEventListener("click", function () {
                if (!spotlightAlarmState || !spotlightAlarmState.cameraId) return;
                ensureCameraVisibleOnStage(spotlightAlarmState.cameraId, { allowAssign: true, scroll: true });
            });
        }

        if (mapCanvasEl) {
            mapCanvasEl.addEventListener("click", function (event) {
                if (layoutLocked) return;
                var pin = event.target.closest(".sunorus-map-pin");
                if (!pin) return;
                var cameraId = pin.getAttribute("data-camera-id");
                if (!cameraId) return;
                ensureCameraVisibleOnStage(cameraId, { allowAssign: true, scroll: true });
            });
        }

        if (alarmSidebarEl) {
            alarmSidebarEl.addEventListener("click", function (event) {
                var evidenceButton = event.target.closest("[data-alarm-evidence]");
                if (evidenceButton) {
                    event.preventDefault();
                    openEvidenceModal(
                        evidenceButton.getAttribute("data-alarm-evidence"),
                        evidenceButton.getAttribute("data-evidence-url"),
                        evidenceButton.getAttribute("data-evidence-title"),
                        evidenceButton.getAttribute("data-evidence-meta"));
                    return;
                }

                var button = event.target.closest("[data-alarm-camera-id]");
                if (!button) return;
                event.preventDefault();
                var cameraId = button.getAttribute("data-alarm-camera-id");
                ensureCameraVisibleOnStage(cameraId, { allowAssign: true, scroll: true });
            });
        }

        if (libraryListEl) {
            libraryListEl.addEventListener("dragstart", function (event) {
                if (layoutLocked) {
                    event.preventDefault();
                    return;
                }

                var item = event.target.closest(".camera-library-item");
                if (!item) return;

                var cameraId = item.getAttribute("data-camera-id");
                if (!cameraId) return;

                event.dataTransfer.setData("application/x-camera-id", cameraId);
                event.dataTransfer.setData("application/x-source-slot", "");
                event.dataTransfer.effectAllowed = "copyMove";
                item.classList.add("dragging");
                if (wallEl) wallEl.classList.add("drag-active");
            });

            libraryListEl.addEventListener("dragend", function (event) {
                var item = event.target.closest(".camera-library-item");
                if (item) item.classList.remove("dragging");
                clearDragState();
            });

            libraryListEl.addEventListener("click", function (event) {
                if (layoutLocked) return;

                var item = event.target.closest(".camera-library-item");
                if (!item) return;

                var cameraId = item.getAttribute("data-camera-id");
                if (!cameraId) return;

                var emptySlot = assignments.findIndex(function (id) {
                    return !id;
                });

                var targetIndex = emptySlot >= 0 ?emptySlot : 0;
                replaceCameraInSlot(targetIndex, cameraId, null);
            });
        }

        if (wallEl) {
            wallEl.addEventListener("dragstart", function (event) {
                if (layoutLocked) {
                    event.preventDefault();
                    return;
                }

                var tile = event.target.closest(".vms-tile");
                if (!tile) return;

                var cameraId = tile.getAttribute("data-camera-id");
                var sourceSlot = tile.getAttribute("data-slot-index");

                if (!cameraId || sourceSlot === null) return;

                event.dataTransfer.setData("application/x-camera-id", cameraId);
                event.dataTransfer.setData("application/x-source-slot", sourceSlot);
                event.dataTransfer.effectAllowed = "move";
                tile.classList.add("dragging");
                wallEl.classList.add("drag-active");
            });

            wallEl.addEventListener("dragend", function (event) {
                var tile = event.target.closest(".vms-tile");
                if (tile) tile.classList.remove("dragging");
                clearDragState();
            });

            wallEl.addEventListener("dragover", function (event) {
                if (layoutLocked) return;

                var slot = dragSlotFromEvent(event);
                if (!slot) return;

                event.preventDefault();
                slot.classList.add("drop-target");
            });

            wallEl.addEventListener("dragleave", function (event) {
                var slot = dragSlotFromEvent(event);
                if (slot) {
                    slot.classList.remove("drop-target");
                }
            });

            wallEl.addEventListener("drop", function (event) {
                if (layoutLocked) {
                    event.preventDefault();
                    return;
                }

                var slot = dragSlotFromEvent(event);
                if (!slot) return;

                event.preventDefault();
                slot.classList.remove("drop-target");
                clearDragState();

                var slotIndex = Number(slot.dataset.slotIndex);
                if (Number.isNaN(slotIndex)) return;

                var cameraId = event.dataTransfer.getData("application/x-camera-id");
                if (!cameraId) return;

                var sourceSlotRaw = event.dataTransfer.getData("application/x-source-slot");
                var sourceSlot = sourceSlotRaw === "" ?null : Number(sourceSlotRaw);

                replaceCameraInSlot(slotIndex, cameraId, sourceSlot);
            });

            document.addEventListener("dragend", clearDragState);
            document.addEventListener("drop", clearDragState);
            window.addEventListener("blur", clearDragState);

            document.addEventListener("click", function (event) {
                if (!event.target.closest(".vms-mini-events-popover") && !event.target.closest(".vms-tile-bell-btn")) {
                    document.querySelectorAll(".vms-mini-events-popover").forEach(function(p) {
                        p.style.display = "none";
                    });
                }
            });

            wallEl.addEventListener("dblclick", function (event) {
                var slot = event.target.closest(".vms-slot");
                if (!slot) return;

                var slotIndex = Number(slot.dataset.slotIndex);
                if (Number.isNaN(slotIndex)) return;

                if (layoutLocked) return;

                assignments[slotIndex] = null;
                persistAssignments();
                renderWall();
            });

            wallEl.addEventListener("click", function (event) {
                var bellBtn = event.target.closest(".vms-tile-bell-btn");
                if (bellBtn) {
                    event.stopPropagation();
                    var cameraId = bellBtn.getAttribute("data-camera-id");
                    var tile = bellBtn.closest(".vms-tile");
                    var popover = tile ?tile.querySelector(".vms-mini-events-popover") : null;
                    if (popover) {
                        var isCurrentlyVisible = popover.style.display === "block";
                        document.querySelectorAll(".vms-mini-events-popover").forEach(function(p) {
                            p.style.display = "none";
                        });
                        if (!isCurrentlyVisible) {
                            popover.style.display = "block";
                            fetchMiniEvents(cameraId, popover.querySelector(".vms-mini-events-content"));
                        }
                    }
                    return;
                }

                var popoverCloseBtn = event.target.closest(".vms-mini-events-close");
                if (popoverCloseBtn) {
                    event.stopPropagation();
                    var popover = popoverCloseBtn.closest(".vms-mini-events-popover");
                    if (popover) {
                        popover.style.display = "none";
                    }
                    return;
                }

                var nativeLink = event.target.closest("a[href]");
                if (nativeLink) {
                    return;
                }

                var actionButton = event.target.closest("[data-action]");
                if (actionButton) {
                    var action = actionButton.getAttribute("data-action");

                    if (action === "menu-toggle") {
                        event.stopPropagation();
                        var menu = actionButton.closest(".vms-tile-menu");
                        var wasOpen = menu && menu.classList.contains("is-open");
                        closeTileMenus();
                        if (menu && !wasOpen) {
                            menu.classList.add("is-open");
                        }
                        return;
                    }

                    closeTileMenus();

                    if (action === "toggle-aspect") {
                        event.stopPropagation();
                        var tile = actionButton.closest(".vms-tile");
                        if (tile) {
                            var tileIndex = Number(tile.getAttribute("data-slot-index"));
                            if (!Number.isNaN(tileIndex)) {
                                var currentFit = localFitOverrides[tileIndex] || "";
                                if (currentFit === "cover") {
                                    localFitOverrides[tileIndex] = "contain";
                                } else if (currentFit === "contain") {
                                    delete localFitOverrides[tileIndex];
                                } else {
                                    var isGlobalCover = document.body.classList.contains("vms-fit-fill");
                                    localFitOverrides[tileIndex] = isGlobalCover ?"contain" : "cover";
                                }
                                var fitOverride = localFitOverrides[tileIndex] || "";
                                tile.setAttribute("data-fit-override", fitOverride);
                                tile.classList.toggle("vms-local-fit-cover", fitOverride === "cover");
                                tile.classList.toggle("vms-local-fit-contain", fitOverride === "contain");
                            }
                        }
                        return;
                    }

                    if (action === "maximize-slot") {
                        event.stopPropagation();
                        var tile = actionButton.closest(".vms-tile");
                        if (tile) {
                            handleVideoDoubleClick(tile);
                        }
                        return;
                    }

                    if (action === "remove-slot") {
                        if (layoutLocked) return;

                        var removeSlotIndex = Number(actionButton.getAttribute("data-slot-index"));
                        if (Number.isNaN(removeSlotIndex)) return;

                        assignments[removeSlotIndex] = null;
                        persistAssignments();
                        renderWall();
                        return;
                    }

                    if (action === "toggle-audio") {
                        event.stopPropagation();
                        toggleTileAudio(actionButton.getAttribute("data-camera-id"));
                        return;
                    }

                    var cameraId = actionButton.getAttribute("data-camera-id");
                    if (!cameraId) return;

                    if (action === "motion" || action === "stop") {
                        controlCamera(cameraId, action);
                    }

                    return;
                }

                if (event.target.closest(".vms-tile-menu")) {
                    return;
                }

                var tile = event.target.closest(".vms-tile");
                if (!tile) return;

                var selectedSlotIndex = tile.getAttribute("data-slot-index");
                if (selectedSlotIndex !== null && wallEl) {
                    wallEl.dataset.selectedSlotIndex = selectedSlotIndex;
                }

                setSelectedCamera(tile.getAttribute("data-camera-id"), { fetch: true });
            });

        }
    }

    async function pollMonitor(force) {
        if (disposed || document.hidden) return;
        if (monitorPollRunning && !force) return;
        monitorPollRunning = true;

        try {
            var separator = dataUrl.indexOf("?") >= 0 ?"&" : "?";
            var pollUrl = dataUrl + separator + "_ts=" + Date.now();

            var response = await fetch(pollUrl, {
                cache: "no-store",
                headers: { "Cache-Control": "no-cache" }
            });

            if (!response.ok) return;

            var data = await response.json();

            stats = data.stats || {};
            if (data.gateway_health !== undefined) {
                gatewayHealth = data.gateway_health;
            }
            visibleCameras = Array.isArray(data.cameras) ?data.cameras : [];
            mergeVisibleCamerasIntoLibrary();

            refreshCameraMap();
            ensureAssignmentsMatchAvailable();

            refreshAll();
        } catch (error) {
            console.warn("Falha no poll do monitor", error);
        } finally {
            monitorPollRunning = false;
        }
    }

    async function pollLibrary(force) {
        if (disposed || document.hidden) return;
        if (libraryPollRunning && !force) return;
        libraryPollRunning = true;

        try {
            var separator = libraryDataUrl.indexOf("?") >= 0 ?"&" : "?";
            var pollUrl = libraryDataUrl + separator + "_ts=" + Date.now();

            var response = await fetch(pollUrl, {
                cache: "no-store",
                headers: { "Cache-Control": "no-cache" }
            });
            if (!response.ok) return;

            var data = await response.json();
            if (data.gateway_health !== undefined) {
                gatewayHealth = data.gateway_health;
            }
            availableCameras = Array.isArray(data.available_cameras) ?data.available_cameras : [];
            mergeVisibleCamerasIntoLibrary();
            refreshCameraMap();
            ensureAssignmentsMatchAvailable();
            refreshAll();
        } catch (error) {
            console.warn("Falha no poll da biblioteca do monitor", error);
        } finally {
            libraryPollRunning = false;
        }
    }

    function configuredTrackSseCameraIds() {
        var configured = new Set();
        webTrackSseCameraIds.split(",").forEach(function (value) {
            value = String(value || "").trim();
            if (/^\d+$/.test(value)) configured.add(value);
        });
        return assignedCameraIdsForBoxes().filter(function (cameraId) {
            return configured.has(String(cameraId));
        });
    }

    function applyTrackPayload(data, cameraIds) {
        var cameras = data && data.cameras ?data.cameras : {};
        (cameraIds || []).forEach(function (cameraId) {
            scheduleCameraBoxUpdate(
                cameraId,
                cameras[String(cameraId)] || {
                    camera_id: Number(cameraId),
                    tracks: [],
                    stale: true
                }
            );
        });
    }

    function scheduleCameraBoxUpdate(cameraId, payload) {
        var key = String(cameraId);
        if (pendingTrackUpdates.has(key)) {
            trackTransportMetrics.visual_updates_coalesced_total += 1;
        }
        pendingTrackUpdates.set(key, payload);
        if (pendingTrackAnimationFrame != null) return;
        pendingTrackAnimationFrame = window.requestAnimationFrame(function () {
            pendingTrackAnimationFrame = null;
            var updates = Array.from(pendingTrackUpdates.entries());
            pendingTrackUpdates.clear();
            updates.forEach(function (entry) {
                updateCameraBoxes(entry[0], entry[1]);
            });
            expireStaleCameraBoxes();
        });
    }

    function closeTrackSse() {
        if (tracksSseSource) {
            tracksSseSource.close();
            tracksSseSource = null;
        }
        tracksSseConnected = false;
        tracksSseCameraSignature = "";
    }

    function ensureTrackSse() {
        if (
            disposed
            || document.hidden
            || webTrackTransportMode === "polling"
            || typeof EventSource === "undefined"
        ) {
            closeTrackSse();
            return;
        }
        var ids = configuredTrackSseCameraIds();
        var signature = ids.join(",");
        if (!signature) {
            closeTrackSse();
            return;
        }
        if (tracksSseSource && tracksSseCameraSignature === signature) return;

        closeTrackSse();
        var params = new URLSearchParams();
        params.set("camera_ids", signature);
        params.set("max_age_seconds", String(tracksMaxAgeSeconds));
        params.set("min_confidence", minBoxConfidence.toFixed(3));
        params.set("interval_ms", "25");
        var source = new EventSource(
            "/monitor/tracks/stream?" + params.toString()
        );
        tracksSseSource = source;
        tracksSseCameraSignature = signature;
        source.onopen = function () {
            tracksSseConnected = true;
        };
        source.addEventListener("tracks", function (event) {
            if (source !== tracksSseSource) return;
            try {
                var data = JSON.parse(event.data || "{}");
                trackTransportMetrics.sse_messages_total += 1;
                applyTrackPayload(data, ids);
            } catch (error) {
                console.warn("Payload SSE de boxes invalido", error);
            }
        });
        source.onerror = function () {
            if (source !== tracksSseSource) return;
            trackTransportMetrics.sse_errors_total += 1;
            tracksSseConnected = false;
            source.close();
            tracksSseSource = null;
            if (webTrackTransportMode === "sse_prefer") {
                trackTransportMetrics.polling_fallback_total += 1;
                pollTrackBoxes();
            }
            if (!tracksSseReconnectTimerId) {
                tracksSseReconnectTimerId = setTimeout(function () {
                    tracksSseReconnectTimerId = null;
                    ensureTrackSse();
                }, 1000);
            }
        };
    }

    async function pollTrackBoxes(explicitIds) {
        if (disposed || document.hidden) return;
        if (!boxesEnabled) return;
        if (tracksPollRunning) return;

        ensureTrackSse();
        var ids = Array.isArray(explicitIds)
            ?explicitIds.slice()
            :assignedCameraIdsForBoxes();
        if (!Array.isArray(explicitIds) && webTrackTransportMode !== "polling") {
            var sseIds = new Set(configuredTrackSseCameraIds());
            ids = ids.filter(function (cameraId) {
                if (!sseIds.has(String(cameraId))) return true;
                return (
                    webTrackTransportMode === "sse_prefer"
                    && !tracksSseConnected
                );
            });
        }
        if (!ids.length) return;

        tracksPollRunning = true;
        trackTransportMetrics.polling_requests_total += 1;
        try {
            var params = new URLSearchParams();
            params.set("camera_ids", ids.join(","));
            params.set("max_age_seconds", String(tracksMaxAgeSeconds));
            params.set("min_confidence", minBoxConfidence.toFixed(3));
            params.set("_ts", String(Date.now()));

            var response = await fetch(tracksDataUrl + "?" + params.toString(), {
                cache: "no-store",
                headers: { "Cache-Control": "no-cache" }
            });
            if (!response.ok) return;

            var data = await response.json();
            applyTrackPayload(data, ids);
        } catch (error) {
            console.warn("Falha no poll das boxes", error);
        } finally {
            expireStaleCameraBoxes();
            tracksPollRunning = false;
        }
    }

    window.sunorusTrackLatencyDiagnostics = function () {
        return {
            transport_mode: webTrackTransportMode,
            configured_sse_camera_ids: webTrackSseCameraIds,
            sse_connected: tracksSseConnected,
            sse_camera_signature: tracksSseCameraSignature,
            visual_track_fresh_ms: visualTrackFreshMs,
            visual_track_retention_ms: visualTrackRetentionMs,
            counters: Object.assign({}, trackTransportMetrics),
            latency: {
                backend_to_client_ms: summarizeClientLatency(
                    clientLatencySamples.backend_to_client_ms
                ),
                client_render_ms: summarizeClientLatency(
                    clientLatencySamples.client_render_ms
                )
            },
            cameras: configuredTrackSseCameraIds().map(function (cameraId) {
                var camera = cameraById.get(String(cameraId)) || {};
                return {
                    camera_id: Number(cameraId),
                    generation_id: camera.monitor_boxes_generation_id || null,
                    frame_id: camera.monitor_boxes_frame_id || null,
                    client_received_at_ns:
                        camera.monitor_boxes_client_received_at_ns || null,
                    client_rendered_at_ns:
                        camera.monitor_boxes_client_rendered_at_ns || null,
                    backend_to_client_ms:
                        camera.monitor_boxes_backend_to_client_ms ?? null,
                    client_render_ms:
                        camera.monitor_boxes_client_render_ms ?? null,
                    stale: !!camera.monitor_boxes_stale,
                    boxes: Array.isArray(camera.monitor_boxes)
                        ?camera.monitor_boxes.length
                        :0
                };
            })
        };
    };

    async function pollAlarmQueue() {
        if (disposed || document.hidden) return;
        if (alarmPollRunning) return;
        alarmPollRunning = true;

        try {
            setAlarmRefreshMeta("warn", "Fila: atualizando...");

            var separator = alarmDataUrl.indexOf("?") >= 0 ?"&" : "?";
            var pollUrl = alarmDataUrl + separator + "_ts=" + Date.now();

            var response = await fetch(pollUrl, {
                cache: "no-store",
                headers: { "Cache-Control": "no-cache" }
            });

            if (!response.ok) {
                var monitorSeparator = dataUrl.indexOf("?") >= 0 ?"&" : "?";
                var monitorPollUrl = dataUrl + monitorSeparator + "_ts=" + Date.now();

                response = await fetch(monitorPollUrl, {
                    cache: "no-store",
                    headers: { "Cache-Control": "no-cache" }
                });

                if (!response.ok) {
                    setAlarmRefreshMeta("error", "Fila: falha HTTP " + response.status + " em " + formatNowLabel());
                    return;
                }
            }

            var data = await response.json();
            var nextAlarms = Array.isArray(data.alarms) ?data.alarms : [];
            var visibleQueueAlarms = nextAlarms.filter(isAlarmVisibleInOperatorQueue);
            var queueSignature = buildQueueSignatureFromAlarms(visibleQueueAlarms);

            var previousQueueSignature = lastAlarmQueueSignature || localStorage.getItem(alarmQueueSignatureKey) || "";
            var queueChanged = queueSignature !== previousQueueSignature;

            alarms = nextAlarms;
            lastAlarmQueueSignature = queueSignature;

            try {
                localStorage.setItem(alarmQueueSignatureKey, queueSignature);
            } catch (error) {}

            renderAlarms();
            renderMapPins();
            updateAlarmSpotlight();
            syncLiveAlarmQueue(nextAlarms);
            if (data.popup_should_show && data.latest_popup_alarm) {
                openLiveAlarmModalNow(data.latest_popup_alarm);
            }
            setAlarmRefreshMeta("ok", "Fila: atualizada " + formatNowLabel() + " (" + visibleQueueAlarms.length + " alarmes)");

            if (queueChanged) {
                setTimeout(function () {
                    pollLibrary(true);
                    pollMonitor(true);
                }, 150);
            }

            var signature = data.latest_alarm_signature || "";
            var previousSignature = localStorage.getItem(alarmSignatureKey);

            if (signature) {
                if (!firstAlarmPoll && previousSignature !== signature) {
                    if (data.alarm_should_play) {
                        safePlayAlarmTone();
                    }

                    if (data.popup_should_show && data.latest_popup_alarm) {
                        safeShowAlarmPopup(data.latest_popup_alarm);
                    }
                }

                localStorage.setItem(alarmSignatureKey, signature);
            }

            firstAlarmPoll = false;
        } catch (error) {
            console.warn("Falha no poll da fila de alarmes", error);
            setAlarmRefreshMeta("error", "Fila: erro de atualização em " + formatNowLabel());
        } finally {
            alarmPollRunning = false;
        }
    }

    loadTileDetailsPreference();
    loadTileHeadersPreference();
    loadWebrtcPurePreference();
    loadOverlayPreference();
    loadBoxesPreference();
    loadBoxConfidencePreference();
    loadLayoutModePreference();
    var requiredLayoutGrid = requiredGridForLayout();
    if (requiredLayoutGrid && selectedGrid !== requiredLayoutGrid) {
        applyGridPreset(requiredLayoutGrid);
    }
    loadVideoFitPreference();
    loadDensityPreference();
    loadOperatorModePreference();
    loadSpotlightPreference();
    loadCentralPopupPreference();
    applyCentralPopupPreference();
    loadLayoutLockPreference();
    var centralPopupToggleBtn = document.getElementById("centralPopupToggleBtn");
    if (centralPopupToggleBtn) {
        centralPopupToggleBtn.addEventListener("click", function () {
            centralPopupEnabled = !centralPopupEnabled;
            persistCentralPopupPreference();
            applyCentralPopupPreference();
        });
    }
    loadSequencePreference();
    detectClientHevcSupport();
    detectVideoHelper();
    refreshCameraMap();
    loadAssignments();
    consumePendingSavedView();
    renderSavedViewsSelect();
    renderTemporalSequenceSelect();
    syncMosaicosFromServer();
    initTemporalSequencePlayback();
    renderGridIcons();
    refreshAll();
    bindInteractions();
    updateFullscreenButtonLabel();
    applyTileDetailsPreference();
    applyOverlayPreference();
    applyBoxesPreference();
    applyLayoutModePreference();
    applyVideoFitPreference();
    applyDensityPreference();
    applyOperatorModePreference();
    applySpotlightPreference();
    updateLayoutLockUi();
    updateSequenceUi();
    syncMuteButtonState();
    setLeftPanel("library");

    if (initialQueueSignature) {
        lastAlarmQueueSignature = buildQueueSignatureFromAlarms(initialAlarms.filter(isAlarmVisibleInOperatorQueue)) || initialQueueSignature;
        try {
            localStorage.setItem(alarmQueueSignatureKey, lastAlarmQueueSignature);
        } catch (error) {}
    } else {
        lastAlarmQueueSignature = buildQueueSignatureFromAlarms(initialAlarms.filter(isAlarmVisibleInOperatorQueue));
        try {
            localStorage.setItem(alarmQueueSignatureKey, lastAlarmQueueSignature);
        } catch (error) {}
    }

    if (initialSignature) {
        var previous = localStorage.getItem(alarmSignatureKey);

        if (initialAllowSound && previous && previous !== initialSignature) {
            safePlayAlarmTone();
        }

        localStorage.setItem(alarmSignatureKey, initialSignature);

        if (initialPopupShouldShow && initialPopupAlarm) {
            safeShowAlarmPopup(initialPopupAlarm);
        }
    }

    async function syncMosaicosFromServer() {
        try {
            var resViews = await fetch("/api/view-presets");
            if (resViews.ok) {
                var views = (await resViews.json()).map(normalizeServerView);
                localStorage.setItem(savedViewsKey, JSON.stringify(views));
                renderSavedViewsSelect();
            }
            
            var resSeqs = await fetch("/api/temporal-sequences");
            if (resSeqs.ok) {
                var seqs = await resSeqs.json();
                var parsedSeqs = seqs.map(function(s) {
                    return {
                        id: s.id,
                        name: s.name,
                        steps: s.steps.map(function(st) {
                            return { viewId: st.viewId, duration: st.duration };
                        }),
                        isShared: s.is_shared === true,
                        ownerUsername: s.owner_username || "",
                        canManage: s.can_manage === true
                    };
                });
                
                localStorage.setItem(temporalMosaicsKey, JSON.stringify(parsedSeqs));
                renderTemporalSequenceSelect();
            }
        } catch (error) {
            console.error("Erro ao sincronizar mosaicos do servidor:", error);
        }
    }

    pollLibrary(true);
    pollMonitor(true);
    pollAlarmQueue();
    pollTrackBoxes();
    startPollingTimers();
    if (sequenceEnabled) {
        applySequencePage();
        scheduleSequencePage();
    }
})();
