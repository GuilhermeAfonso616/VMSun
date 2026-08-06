package main

import (
	"container/heap"
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
)

func newGateway() *Gateway {
	sourcePolicy, err := gatewaySourcePolicy()
	if err != nil {
		panic(err)
	}
	latencyProfile, err := parseLatencyProfile(
		getenvDefault("GATEWAY_FFMPEG_LATENCY_PROFILE", string(profileCompatibility)),
	)
	if err != nil {
		panic(err)
	}
	maxConcurrentStarts := getenvInt("GATEWAY_MAX_CONCURRENT_STARTS", 2)
	if maxConcurrentStarts < 1 {
		maxConcurrentStarts = 1
	}
	maxConcurrentRecovers := getenvInt("GATEWAY_MAX_CONCURRENT_RECOVERS", 2)
	if maxConcurrentRecovers < 1 {
		maxConcurrentRecovers = 1
	}

	queueAgingSeconds := getenvFloat("GATEWAY_QUEUE_AGING_SECONDS", 10)
	if queueAgingSeconds <= 0 {
		queueAgingSeconds = 10
	}

	ctx, cancel := context.WithCancel(context.Background())
	gateway := &Gateway{
		cameras:                     make(map[int]*CameraRuntime),
		ffmpegPath:                  getenvDefault("GATEWAY_FFMPEG_PATH", "ffmpeg"),
		reconnectDelay:              getenvDurationSeconds("GATEWAY_RECONNECT_DELAY_SECONDS", 2*time.Second),
		reconnectBackoffMax:         getenvDurationSeconds("GATEWAY_RECONNECT_BACKOFF_MAX_SECONDS", 30*time.Second),
		backboneReconnectDelay:      getenvDurationSeconds("GATEWAY_BACKBONE_RECONNECT_DELAY_SECONDS", time.Second),
		backboneReconnectBackoffMax: getenvDurationSeconds("GATEWAY_BACKBONE_RECONNECT_BACKOFF_MAX_SECONDS", 5*time.Second),
		frameStaleAfter:             getenvDurationSeconds("GATEWAY_FRAME_STALE_AFTER_SECONDS", 10*time.Second),
		firstFrameTimeout:           getenvDurationSeconds("GATEWAY_FIRST_FRAME_TIMEOUT_SECONDS", 20*time.Second),
		snapshotWaitTimeout:         getenvDurationSeconds("GATEWAY_SNAPSHOT_WAIT_SECONDS", 5*time.Second),
		streamPollInterval:          getenvDurationMillis("GATEWAY_STREAM_POLL_MS", 100*time.Millisecond),
		outputFPS:                   getenvDefault("GATEWAY_OUTPUT_FPS", "2"),
		jpegQuality:                 getenvDefault("GATEWAY_JPEG_QUALITY", "12"),
		ffmpegHWAccelEnabled:        getenvBool("GATEWAY_FFMPEG_HWACCEL_ENABLED", false),
		ffmpegHWAccelFallback:       getenvBool("GATEWAY_FFMPEG_HWACCEL_FALLBACK_ENABLED", true),
		ffmpegHWAccel:               getenvDefault("GATEWAY_FFMPEG_HWACCEL", "cuda"),
		ffmpegHWOutputFormat:        getenvDefault("GATEWAY_FFMPEG_HWACCEL_OUTPUT_FORMAT", "cuda"),
		ffmpegVideoDecoder:          getenvDefault("GATEWAY_FFMPEG_VIDEO_DECODER", ""),
		ffmpegGPUScaleEnabled:       getenvBool("GATEWAY_FFMPEG_GPU_SCALE_ENABLED", true),
		ffmpegGPUScaleWidth:         getenvInt("GATEWAY_FFMPEG_GPU_SCALE_WIDTH", 960),
		ffmpegGPUScaleHeight:        getenvInt("GATEWAY_FFMPEG_GPU_SCALE_HEIGHT", 540),
		rtspTransport:               getenvDefault("GATEWAY_RTSP_TRANSPORT", "tcp"),
		lowLatency:                  getenvBool("GATEWAY_ENABLE_LOW_LATENCY", false),
		ffmpegLatencyProfile:        latencyProfile,
		ffmpegBalancedCameras:       parseCameraSelector(getenvDefault("GATEWAY_FFMPEG_BALANCED_CAMERA_IDS", "")),
		ffmpegLowLatencyCameras:     parseCameraSelector(getenvDefault("GATEWAY_FFMPEG_LOW_LATENCY_CAMERA_IDS", "")),
		ffmpegProfileFallback:       getenvBool("GATEWAY_FFMPEG_PROFILE_FALLBACK_ENABLED", true),
		ffmpegProfileStrict:         getenvBool("GATEWAY_FFMPEG_PROFILE_STRICT", false),
		maxAnalyticFrameAge:         getenvDurationMillis("GATEWAY_MAX_ANALYTIC_FRAME_AGE_MS", 0),
		maxAnalyticFrameAgeCameras:  parseCameraSelector(getenvDefault("GATEWAY_MAX_ANALYTIC_FRAME_AGE_CAMERA_IDS", "")),
		lagWatchdogEnabled:          getenvBool("GATEWAY_FRAME_LAG_WATCHDOG_ENABLED", false),
		lagWatchdogCameras:          parseCameraSelector(getenvDefault("GATEWAY_FRAME_LAG_WATCHDOG_CAMERA_IDS", "")),
		lagRestartThreshold:         getenvDurationMillis("GATEWAY_FRAME_LAG_RESTART_THRESHOLD_MS", 2*time.Second),
		lagRestartHold:              getenvDurationSeconds("GATEWAY_FRAME_LAG_RESTART_HOLD_SECONDS", 5*time.Second),
		lagRestartCooldown:          getenvDurationSeconds("GATEWAY_FRAME_LAG_RESTART_COOLDOWN_SECONDS", 30*time.Second),
		lagRestartMaxPerHour:        getenvInt("GATEWAY_FRAME_LAG_RESTART_MAX_PER_HOUR", 3),
		frameRingMax:                getenvInt("GATEWAY_FRAME_RING_MAX", 5),
		maxConcurrentStarts:         maxConcurrentStarts,
		maxConcurrentRecovers:       maxConcurrentRecovers,
		startStagger:                getenvDurationMillis("GATEWAY_START_STAGGER_MS", 1500*time.Millisecond),
		enableFallbackRTSP:          getenvBool("GATEWAY_ENABLE_FALLBACK_RTSP", false),
		defaultPriority:             getenvInt("GATEWAY_DEFAULT_CAMERA_PRIORITY", 5),
		maxRetryBeforeFB:            getenvInt("GATEWAY_MAX_RETRY_BEFORE_FALLBACK", 5),
		queueAgingSeconds:           queueAgingSeconds,
		flapWindow:                  getenvDurationSeconds("GATEWAY_FLAP_WINDOW_SECONDS", 120*time.Second),
		flapThreshold:               getenvInt("GATEWAY_FLAP_THRESHOLD", 4),
		circuitBreaker:              getenvDurationSeconds("GATEWAY_CIRCUIT_BREAKER_SECONDS", 60*time.Second),
		nodeMaxActiveCameras:        getenvInt("GATEWAY_NODE_MAX_ACTIVE_CAMERAS", 0),
		healthInterval:              getenvDurationSeconds("GATEWAY_HEALTH_CHECK_INTERVAL_SECONDS", 2*time.Second),
		instanceID:                  newGatewayInstanceID(),
		sourcePolicy:                sourcePolicy,
		allowedRTSPHosts:            gatewayAllowedRTSPHosts(),
		orchCtx:                     ctx,
		orchCancel:                  cancel,
		pendingJobs:                 make(map[int]*StartJob),
		activeStarts:                make(map[int]*StartJob),
		activeRecoveries:            make(map[int]*StartJob),
		frameTransport:              newFrameTransportManager(loadFrameTransportConfig()),
	}
	gateway.queueCond = sync.NewCond(&gateway.queueMu)
	heap.Init(&gateway.startQueue)
	go gateway.orchestratorLoop(ctx)
	go gateway.healthMonitorLoop(ctx)
	return gateway
}

func (g *Gateway) ensureCamera(id int) *CameraRuntime {
	g.mu.Lock()
	defer g.mu.Unlock()

	if cam, ok := g.cameras[id]; ok {
		return cam
	}

	cam := &CameraRuntime{
		state: CameraState{
			CameraID:          id,
			State:             stateIdle,
			Priority:          g.defaultPriority,
			HealthScore:       0,
			LastStateChangeAt: utcPtr(time.Now()),
		},
		frameRingMax: g.frameRingMax,
		watchdog: newLagWatchdog(
			g.lagWatchdogEnabled && g.lagWatchdogCameras.selected(id),
			g.lagRestartThreshold,
			g.lagRestartHold,
			g.lagRestartCooldown,
			g.lagRestartMaxPerHour,
		),
	}
	g.cameras[id] = cam
	return cam
}

func (g *Gateway) configureCamera(id int, sourceURL string) (*CameraRuntime, bool) {
	return g.configureCameraWithJob(id, sourceURL, g.defaultPriority, reasonInitialStart)
}

func (g *Gateway) configureCameraWithJob(id int, sourceURL string, priority int, reason string) (*CameraRuntime, bool) {
	cam := g.ensureCamera(id)
	sourceURL = strings.TrimSpace(sourceURL)
	if sourceURL == "" {
		return cam, false
	}

	if priority <= 0 {
		priority = g.defaultPriority
	}
	if strings.TrimSpace(reason) == "" {
		reason = reasonInitialStart
	}

	cam.mu.Lock()
	oldSource := cam.state.SourceURL
	if oldSource == sourceURL && (cam.running ||
		cam.circuitOpenUntil.After(time.Now().UTC()) ||
		cam.circuitHalfOpen ||
		cam.state.State == stateQueued ||
		cam.state.State == stateStarting ||
		cam.state.State == stateWarmingUp ||
		cam.state.State == stateReconnecting ||
		cam.state.State == stateUnstable) {
		cam.mu.Unlock()
		return cam, false
	}

	if cam.cancel != nil {
		cam.cancel()
	}

	cam.generation++
	cam.sourceVersion++
	cam.lastStarted = time.Now().UTC()
	cam.cancel = nil
	cam.running = false
	cam.state.SourceURL = sourceURL
	cam.state.State = stateQueued
	cam.state.Priority = priority
	cam.state.QueuedAt = utcPtr(time.Now())
	cam.state.StartedAt = nil
	cam.state.LastFrameAt = nil
	cam.state.LastReconnectAt = nil
	cam.state.LastFrameAgeMS = 0
	cam.state.FirstFrameMS = 0
	cam.state.QueueWaitMS = 0
	cam.state.HealthScore = 10
	cam.state.LastError = ""
	cam.state.LastStateChangeAt = utcPtr(time.Now())
	if oldSource != sourceURL {
		closeCircuitLocked(cam)
	}
	cam.latestJPEG = nil
	cam.latestSeq = 0
	cam.frameRing = nil
	cam.frameRingMax = g.frameRingMax
	cam.lastFrameRecorded = time.Time{}
	cam.state.FFmpegReady = false
	cam.state.FFmpegPID = 0
	cam.state.FramePTSSeconds = nil
	cam.state.FramePTSDriftMS = nil
	cam.state.FrameSourceEstimatedAgeMS = nil
	generation := cam.generation
	sourceVersion := cam.sourceVersion
	cam.mu.Unlock()
	cam.ptsEstimator.Reset()

	if g.frameTransport.selected(id) {
		if err := g.frameTransport.reset(id); err != nil {
			transportLog.Error("buffer_create_failed", "cam", id, "mode", g.frameTransport.config.Mode, "error", err)
			if g.frameTransport.config.Mode == "shared_memory_strict" {
				cam.mu.Lock()
				cam.state.State = stateDegraded
				cam.state.LastError = "shared_memory_unavailable"
				cam.mu.Unlock()
			}
		}
	}

	g.enqueueStart(StartJob{
		CameraID:      id,
		SourceURL:     sourceURL,
		Priority:      priority,
		Attempt:       0,
		Reason:        reason,
		EnqueuedAt:    time.Now().UTC(),
		Generation:    generation,
		SourceVersion: sourceVersion,
	})
	return cam, true
}

func (g *Gateway) stopCamera(id int, reason string) *CameraRuntime {
	cam := g.ensureCamera(id)
	reason = strings.TrimSpace(reason)
	if reason == "" {
		reason = reasonManualStop
	}

	cam.mu.Lock()
	if cam.cancel != nil {
		cam.cancel()
	}
	cam.generation++
	cam.sourceVersion++
	cam.cancel = nil
	cam.running = false
	cam.state.SourceURL = ""
	cam.state.State = stateStopped
	cam.state.QueuedAt = nil
	cam.state.StartedAt = nil
	cam.state.LastFrameAt = nil
	cam.state.LastReconnectAt = nil
	cam.state.LastFrameAgeMS = 0
	cam.state.FirstFrameMS = 0
	cam.state.QueueWaitMS = 0
	cam.state.HealthScore = 0
	cam.state.LastError = reason
	cam.state.LastStateChangeAt = utcPtr(time.Now())
	closeCircuitLocked(cam)
	cam.latestJPEG = nil
	cam.latestSeq = 0
	cam.frameRing = nil
	cam.lastFrameRecorded = time.Time{}
	cam.state.FFmpegReady = false
	cam.state.FFmpegPID = 0
	cam.mu.Unlock()
	cam.ptsEstimator.Reset()
	g.frameTransport.stop(id)

	return cam
}

func (g *Gateway) configureCameraFromRequest(r *http.Request, id int) *CameraRuntime {
	if sourceURL := strings.TrimSpace(r.URL.Query().Get("source_url")); sourceURL != "" {
		priority := parseIntQuery(r.URL.Query().Get("priority"), g.defaultPriority)
		reason := strings.TrimSpace(r.URL.Query().Get("reason"))
		if reason == "" {
			reason = reasonInitialStart
		}
		cam, _ := g.configureCameraWithJob(id, sourceURL, priority, reason)
		return cam
	}

	return g.ensureCamera(id)
}

func (g *Gateway) cameraSnapshot(cam *CameraRuntime) CameraState {
	cam.mu.RLock()
	defer cam.mu.RUnlock()

	now := time.Now().UTC()
	state := cam.state
	state.SourceURL = maskSourceURL(state.SourceURL)
	if state.LastFrameAt != nil {
		state.LastFrameAgeMS = time.Since(*state.LastFrameAt).Milliseconds()
	}
	if state.State == stateRunning && state.LastFrameAt != nil && time.Since(*state.LastFrameAt) > g.frameStaleAfter {
		state.State = stateDegraded
	}
	state.HealthScore = g.healthScore(state)
	state.FlapCount = len(cam.flapEvents)
	state.GatewayInstanceID = g.instanceID
	state.StreamGenerationID = g.streamGenerationID(state.CameraID, cam.generation)
	state.FFmpegConfiguredProfile = string(g.profileForCamera(state.CameraID))
	if state.FFmpegLatencyProfile == "" {
		state.FFmpegLatencyProfile = state.FFmpegConfiguredProfile
	}
	state.WatchdogState = cam.watchdog.State()
	if state.LastFrameAt != nil {
		state.FrameLocalAgeMS = float64(time.Since(*state.LastFrameAt)) / float64(time.Millisecond)
	}
	state.FailureEpoch = cam.failureEpoch
	state.CircuitReason = cam.circuitReason
	if !cam.circuitOpenedAt.IsZero() {
		state.CircuitOpenedAt = utcPtr(cam.circuitOpenedAt)
	} else {
		state.CircuitOpenedAt = nil
	}
	if cam.circuitOpenUntil.After(now) {
		state.CircuitOpen = true
		state.CircuitState = "open"
		state.CircuitOpenUntil = utcPtr(cam.circuitOpenUntil)
		state.CircuitRetryAfter = max(int64(0), cam.circuitOpenUntil.Sub(now).Milliseconds())
	} else if cam.circuitHalfOpen || !cam.circuitOpenedAt.IsZero() {
		state.CircuitOpen = false
		state.CircuitState = "half_open"
		state.CircuitOpenUntil = nil
		state.CircuitRetryAfter = 0
	} else {
		state.CircuitOpen = false
		state.CircuitState = "closed"
		state.CircuitOpenUntil = nil
		state.CircuitRetryAfter = 0
	}
	if state.State == stateQueued && state.QueuedAt != nil {
		state.EffectivePriority = g.effectivePriorityLocked(cam, &StartJob{
			CameraID:   state.CameraID,
			SourceURL:  state.SourceURL,
			Priority:   state.Priority,
			Reason:     reasonInitialStart,
			EnqueuedAt: *state.QueuedAt,
		}, time.Now().UTC())
	}
	transport := g.frameTransport.metrics(state.CameraID)
	state.FrameTransportMode = transport.Mode
	state.SharedBufferReady = transport.Ready
	state.SharedBufferGeneration = transport.Generation
	state.SharedBufferCapacityBytes = transport.CapacityBytes
	state.SharedBufferSlots = transport.Slots
	state.SharedBufferFramesWritten = transport.FramesWritten
	state.SharedBufferFramesOverwritten = transport.FramesOverwritten
	state.SharedBufferWriteErrors = transport.WriteErrors
	state.SharedBufferPayloadTooLarge = transport.PayloadTooLarge
	state.SharedBufferLastFrameID = transport.LastFrameID
	state.SharedBufferLastWriteAgeMS = transport.LastWriteAgeMS
	state.FrameTransportHTTPFallbackTotal = transport.HTTPFallbacks
	state.FrameTransportHTTPRequestsTotal = cam.frameHTTPRequests
	state.FrameTransportError = transport.LastError

	return state
}

func (g *Gateway) profileForCamera(cameraID int) ffmpegLatencyProfile {
	if g.lowLatency || g.ffmpegLowLatencyCameras.selected(cameraID) {
		return profileLowLatency
	}
	if g.ffmpegBalancedCameras.selected(cameraID) {
		return profileBalanced
	}
	return g.ffmpegLatencyProfile
}

func (g *Gateway) streamGenerationID(cameraID int, generation uint64) string {
	return fmt.Sprintf("%s-cam%d-gen%d", g.instanceID, cameraID, generation)
}

func closeCircuitLocked(cam *CameraRuntime) {
	cam.flapEvents = nil
	cam.circuitOpenedAt = time.Time{}
	cam.circuitOpenUntil = time.Time{}
	cam.circuitReason = ""
	cam.circuitHalfOpen = false
	cam.state.CircuitOpen = false
	cam.state.CircuitState = "closed"
	cam.state.CircuitReason = ""
	cam.state.CircuitOpenedAt = nil
	cam.state.CircuitOpenUntil = nil
	cam.state.CircuitRetryAfter = 0
	cam.state.FlapCount = 0
}

func (g *Gateway) latestFrame(cam *CameraRuntime) ([]byte, uint64, CameraState) {
	cam.mu.RLock()
	defer cam.mu.RUnlock()

	return cloneBytes(cam.latestJPEG), cam.latestSeq, cam.state
}

func (g *Gateway) setState(cam *CameraRuntime, state string) {
	cam.mu.Lock()
	cam.state.State = state
	cam.state.LastStateChangeAt = utcPtr(time.Now())
	cam.mu.Unlock()
}

func (g *Gateway) recordFrame(cam *CameraRuntime, jpegFrame []byte) {
	g.recordFrameObserved(cam, jpegFrame, frameObservation{
		receivedAt: time.Now().UTC(),
	})
}

func (g *Gateway) recordFrameObserved(
	cam *CameraRuntime,
	jpegFrame []byte,
	observation frameObservation,
) bool {
	now := observation.receivedAt.UTC()
	if now.IsZero() {
		now = time.Now().UTC()
	}
	publishStarted := time.Now()
	latestCopy := cloneBytes(jpegFrame)

	cam.mu.Lock()
	if observation.pts.Available {
		ptsSeconds := observation.pts.PTS.Seconds()
		driftMS := float64(observation.pts.Drift) / float64(time.Millisecond)
		sourceAgeMS := max(0.0, driftMS)
		cam.state.FramePTSSeconds = &ptsSeconds
		cam.state.FramePTSDriftMS = &driftMS
		cam.state.FrameSourceEstimatedAgeMS = &sourceAgeMS
		cam.state.PTSDiscontinuitiesTotal = observation.pts.Discontinuities
		if g.maxAnalyticFrameAge > 0 &&
			g.maxAnalyticFrameAgeCameras.selected(cam.state.CameraID) &&
			time.Duration(sourceAgeMS*float64(time.Millisecond)) > g.maxAnalyticFrameAge {
			cam.state.GatewayFramesStaleTotal++
			cam.state.GatewayFramesDroppedTotal++
			cam.state.DroppedFrames++
			cam.mu.Unlock()
			return false
		}
	} else {
		cam.state.FramePTSSeconds = nil
		cam.state.FramePTSDriftMS = nil
		cam.state.FrameSourceEstimatedAgeMS = nil
	}
	if !cam.lastFrameRecorded.IsZero() {
		cam.state.FrameIntervalMS = float64(now.Sub(cam.lastFrameRecorded)) / float64(time.Millisecond)
	}
	cam.lastFrameRecorded = now
	if observation.pipeRead > 0 {
		cam.state.PipeReadMS = float64(observation.pipeRead) / float64(time.Millisecond)
	}
	if observation.jpegParse > 0 {
		cam.state.JPEGParseMS = float64(observation.jpegParse) / float64(time.Millisecond)
	}
	if observation.channelWait > 0 {
		cam.state.ChannelWaitMS = float64(observation.channelWait) / float64(time.Millisecond)
	}
	cam.latestJPEG = latestCopy
	cam.latestSeq++
	cam.state.GatewayFramesPublishedTotal++
	cam.state.State = stateRunning
	cam.state.LastFrameAt = utcPtr(now)
	cam.state.LastFrameAgeMS = 0
	cam.state.ConsecutiveFails = 0
	cam.state.LastError = ""
	cam.state.HealthScore = 100
	if cam.circuitHalfOpen || (!cam.circuitOpenedAt.IsZero() && !cam.circuitOpenUntil.After(now)) {
		closeCircuitLocked(cam)
	}
	g.pruneFlapsLocked(cam, now)
	cam.state.FlapCount = len(cam.flapEvents)
	cam.state.LastStateChangeAt = utcPtr(now)
	if cam.frameMetricsAt.IsZero() {
		cam.frameMetricsAt = now
	}
	if elapsed := now.Sub(cam.frameMetricsAt).Seconds(); elapsed > 0 {
		cam.state.GatewayPublishFPS = float64(
			cam.state.GatewayFramesPublishedTotal,
		) / elapsed
	}

	frameRingMax := cam.frameRingMax
	if frameRingMax <= 0 {
		frameRingMax = g.frameRingMax
	}

	record := FrameRecord{
		Seq:        cam.latestSeq,
		CapturedAt: now,
		JPEG:       cloneBytes(jpegFrame),
	}
	cam.frameRing = append(cam.frameRing, record)
	if frameRingMax > 0 && len(cam.frameRing) > frameRingMax {
		overflow := len(cam.frameRing) - frameRingMax
		cam.state.DroppedFrames += int64(overflow)
		cam.frameRing = append([]FrameRecord(nil), cam.frameRing[overflow:]...)
	}
	frameID := cam.latestSeq
	cameraID := cam.state.CameraID
	cam.mu.Unlock()

	_ = g.frameTransport.write(cameraID, frameID, now, jpegFrame)
	cam.mu.Lock()
	cam.state.JPEGPublishMS = float64(time.Since(publishStarted)) / float64(time.Millisecond)
	cam.mu.Unlock()
	return true
}
