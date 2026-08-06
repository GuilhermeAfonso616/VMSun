package main

func (g *Gateway) gatewayStats() map[string]any {
	g.queueMu.Lock()
	queueDepth := len(g.pendingJobs)
	activeStarts := g.activeStartCount
	activeRecovers := g.activeRecoverCount
	g.queueMu.Unlock()

	g.mu.Lock()
	cameras := make([]*CameraRuntime, 0, len(g.cameras))
	for _, cam := range g.cameras {
		cameras = append(cameras, cam)
	}
	g.mu.Unlock()

	byState := make(map[string]int)
	running := 0
	reconnecting := 0
	queued := 0
	starting := 0
	activeCameras := 0
	totalReconnects := 0
	totalFailures := 0
	sharedBuffersReady := 0
	var sharedFramesWritten uint64
	var sharedFramesOverwritten uint64
	var sharedWriteErrors uint64

	for _, cam := range cameras {
		state := g.cameraSnapshot(cam)
		byState[state.State]++
		switch state.State {
		case stateRunning:
			running++
			activeCameras++
		case stateQueued:
			queued++
		case stateStarting, stateWarmingUp:
			starting++
			activeCameras++
		case stateReconnecting:
			reconnecting++
		}
		totalReconnects += state.ReconnectCount
		totalFailures += state.FailureCount
		if state.SharedBufferReady {
			sharedBuffersReady++
		}
		sharedFramesWritten += state.SharedBufferFramesWritten
		sharedFramesOverwritten += state.SharedBufferFramesOverwritten
		sharedWriteErrors += state.SharedBufferWriteErrors
	}

	g.metricsMu.Lock()
	totalStarts := g.totalStarts
	totalStartFailures := g.totalStartFailures
	totalFirstFrames := g.totalFirstFrames
	totalFirstFrameMS := g.totalFirstFrameMS
	g.metricsMu.Unlock()

	avgFirstFrameMS := int64(0)
	if totalFirstFrames > 0 {
		avgFirstFrameMS = totalFirstFrameMS / totalFirstFrames
	}

	capacityUsedPercent := 0
	if g.nodeMaxActiveCameras > 0 {
		capacityUsedPercent = int(float64(activeCameras) / float64(g.nodeMaxActiveCameras) * 100)
	}

	return map[string]any{
		"total_cameras":                          len(cameras),
		"running":                                running,
		"queued":                                 queued,
		"starting":                               starting,
		"reconnecting":                           reconnecting,
		"states":                                 byState,
		"queue_depth":                            queueDepth,
		"active_starts":                          activeStarts,
		"active_recovers":                        activeRecovers,
		"max_concurrent_starts":                  g.maxConcurrentStarts,
		"max_concurrent_recovers":                g.maxConcurrentRecovers,
		"start_stagger_ms":                       g.startStagger.Milliseconds(),
		"avg_first_frame_ms":                     avgFirstFrameMS,
		"total_starts":                           totalStarts,
		"total_start_failures":                   totalStartFailures,
		"total_reconnects":                       totalReconnects,
		"total_failures":                         totalFailures,
		"node_max_active_cameras":                g.nodeMaxActiveCameras,
		"node_active_cameras":                    activeCameras,
		"node_capacity_used_percent":             capacityUsedPercent,
		"frame_transport_mode":                   g.frameTransport.config.Mode,
		"shared_buffers_ready":                   sharedBuffersReady,
		"shared_buffer_frames_written_total":     sharedFramesWritten,
		"shared_buffer_frames_overwritten_total": sharedFramesOverwritten,
		"shared_buffer_write_errors_total":       sharedWriteErrors,
	}
}
