package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
	"time"
)

func (g *Gateway) healthz(w http.ResponseWriter, r *http.Request) {
	stats := g.gatewayStats()
	stats["ok"] = true
	stats["service"] = "camera-gateway"
	stats["gateway_instance_id"] = g.instanceID
	stats["timestamp"] = time.Now().UTC().Format(time.RFC3339Nano)
	writeJSON(w, http.StatusOK, stats)
}

func (g *Gateway) cameraList(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	g.mu.Lock()
	cameras := make([]*CameraRuntime, 0, len(g.cameras))
	for _, cam := range g.cameras {
		cameras = append(cameras, cam)
	}
	g.mu.Unlock()

	states := make([]CameraState, 0, len(cameras))
	for _, cam := range cameras {
		states = append(states, g.cameraSnapshot(cam))
	}
	sort.Slice(states, func(i, j int) bool {
		return states[i].CameraID < states[j].CameraID
	})

	summaries := make([]map[string]any, 0, len(states))
	for _, state := range states {
		summaries = append(summaries, map[string]any{
			"camera_id":                             state.CameraID,
			"state":                                 state.State,
			"source_registered":                     state.SourceURL != "",
			"priority":                              state.Priority,
			"last_frame_age_ms":                     state.LastFrameAgeMS,
			"last_error":                            state.LastError,
			"circuit_open":                          state.CircuitOpen,
			"circuit_state":                         state.CircuitState,
			"circuit_reason":                        state.CircuitReason,
			"circuit_opened_at":                     state.CircuitOpenedAt,
			"circuit_open_until":                    state.CircuitOpenUntil,
			"retry_after_ms":                        state.CircuitRetryAfter,
			"gateway_instance_id":                   state.GatewayInstanceID,
			"stream_generation_id":                  state.StreamGenerationID,
			"latency_profile":                       state.FFmpegLatencyProfile,
			"watchdog_state":                        state.WatchdogState,
			"gateway_frame_pts_drift_ms":            state.FramePTSDriftMS,
			"gateway_frame_source_estimated_age_ms": state.FrameSourceEstimatedAgeMS,
			"failure_epoch":                         state.FailureEpoch,
			"frame_transport_mode":                  state.FrameTransportMode,
			"shared_buffer_ready":                   state.SharedBufferReady,
			"shared_buffer_generation":              state.SharedBufferGeneration,
			"shared_buffer_last_frame_id":           state.SharedBufferLastFrameID,
			"frame_transport_http_fallback_total":   state.FrameTransportHTTPFallbackTotal,
			"frame_transport_http_requests_total":   state.FrameTransportHTTPRequestsTotal,
		})
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      true,
		"count":   len(summaries),
		"cameras": summaries,
	})
}

func (g *Gateway) cameraHealth(w http.ResponseWriter, r *http.Request) {
	id, ok := parseCameraID(r.URL.Path)
	if !ok {
		http.NotFound(w, r)
		return
	}

	cam := g.configureCameraFromRequest(r, id)
	writeJSON(w, http.StatusOK, g.cameraSnapshot(cam))
}

func (g *Gateway) cameraStatus(w http.ResponseWriter, r *http.Request) {
	g.cameraHealth(w, r)
}

func (g *Gateway) updateSource(w http.ResponseWriter, r *http.Request) {
	id, ok := parseCameraID(r.URL.Path)
	if !ok {
		http.NotFound(w, r)
		return
	}

	if r.Method == http.MethodDelete {
		cam := g.stopCamera(id, reasonManualStop)
		if strings.EqualFold(strings.TrimSpace(r.URL.Query().Get("remove_buffer")), "true") {
			g.frameTransport.remove(id)
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":      true,
			"stopped": true,
			"camera":  g.cameraSnapshot(cam),
		})
		return
	}

	if r.Method != http.MethodPost && r.Method != http.MethodPut {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var payload sourceRequest
	if err := json.NewDecoder(io.LimitReader(r.Body, 16*1024)).Decode(&payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"ok":    false,
			"error": "invalid_json",
		})
		return
	}

	if strings.TrimSpace(payload.SourceURL) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"ok":    false,
			"error": "missing_source_url",
		})
		return
	}
	if err := validateGatewaySource(g.sourcePolicy, g.allowedRTSPHosts, payload.SourceURL); err != nil {
		writeJSON(w, http.StatusForbidden, map[string]any{
			"ok":              false,
			"error":           "source_policy_violation",
			"message":         "RTSP source host is not allowed",
			"expected_policy": g.sourcePolicy,
		})
		return
	}

	priority := payload.Priority
	if priority <= 0 {
		priority = g.defaultPriority
	}
	reason := strings.TrimSpace(payload.Reason)
	if reason == "" {
		reason = reasonInitialStart
	}
	cam, restarted := g.configureCameraWithJob(id, payload.SourceURL, priority, reason)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":        true,
		"restarted": restarted,
		"queued":    restarted,
		"camera":    g.cameraSnapshot(cam),
	})
}

func (g *Gateway) snapshot(w http.ResponseWriter, r *http.Request) {
	id, ok := parseCameraID(r.URL.Path)
	if !ok {
		http.NotFound(w, r)
		return
	}

	cam := g.configureCameraFromRequest(r, id)
	deadline := time.Now().Add(g.snapshotWaitTimeout)

	for {
		frame, _, state := g.latestFrame(cam)
		if len(frame) > 0 {
			w.Header().Set("Content-Type", "image/jpeg")
			w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write(frame)
			return
		}

		if state.SourceURL == "" {
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{
				"ok":        false,
				"error":     "camera_source_not_registered",
				"camera_id": id,
			})
			return
		}

		if time.Now().After(deadline) {
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{
				"ok":     false,
				"error":  "snapshot_not_ready",
				"camera": state,
			})
			return
		}

		select {
		case <-r.Context().Done():
			return
		case <-time.After(g.streamPollInterval):
		}
	}
}

func (g *Gateway) latestFrameJPEG(w http.ResponseWriter, r *http.Request) {
	id, ok := parseCameraID(r.URL.Path)
	if !ok {
		http.NotFound(w, r)
		return
	}

	cam := g.configureCameraFromRequest(r, id)
	frame, _, state := g.latestFrame(cam)

	if len(frame) > 0 {
		w.Header().Set("Content-Type", "image/jpeg")
		w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(frame)
		return
	}

	if state.SourceURL == "" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"ok":        false,
			"error":     "camera_source_not_registered",
			"camera_id": id,
		})
		return
	}

	writeJSON(w, http.StatusServiceUnavailable, map[string]any{
		"ok":     false,
		"error":  "frame_not_ready",
		"camera": state,
	})
}

func (g *Gateway) framesSince(w http.ResponseWriter, r *http.Request) {
	id, ok := parseCameraID(r.URL.Path)
	if !ok {
		http.NotFound(w, r)
		return
	}
	cam := g.configureCameraFromRequest(r, id)
	cam.mu.Lock()
	cam.frameHTTPRequests++
	cam.mu.Unlock()
	if g.frameTransport.selected(id) && g.frameTransport.config.Mode == "shared_memory_strict" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"ok":        false,
			"error":     "shared_memory_transport_required",
			"camera_id": id,
		})
		return
	}

	afterSeq := parseUint64Query(r.URL.Query().Get("after_seq"), 0)
	limit := parseIntQuery(r.URL.Query().Get("limit"), 5)
	if limit <= 0 {
		limit = 5
	}
	if limit > 30 {
		limit = 30
	}

	state := g.cameraSnapshot(cam)
	ring := g.snapshotFrames(cam)

	var oldestSeq uint64
	var latestSeq uint64
	if len(ring) > 0 {
		oldestSeq = ring[0].Seq
		latestSeq = ring[len(ring)-1].Seq
	}

	dropped := false
	if oldestSeq > 0 && afterSeq < oldestSeq-1 {
		dropped = true
	}

	frames := make([]map[string]any, 0, limit)
	for _, rec := range ring {
		if rec.Seq <= afterSeq {
			continue
		}

		frames = append(frames, map[string]any{
			"seq":          rec.Seq,
			"captured_at":  rec.CapturedAt.UTC().Format(time.RFC3339Nano),
			"content_type": "image/jpeg",
			"jpeg_base64":  base64.StdEncoding.EncodeToString(rec.JPEG),
		})

		if len(frames) >= limit {
			break
		}
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"camera_id":            id,
		"state":                state.State,
		"oldest_seq":           oldestSeq,
		"latest_seq":           latestSeq,
		"dropped":              dropped,
		"frames":               frames,
		"circuit_open":         state.CircuitOpen,
		"circuit_state":        state.CircuitState,
		"circuit_reason":       state.CircuitReason,
		"circuit_opened_at":    state.CircuitOpenedAt,
		"circuit_open_until":   state.CircuitOpenUntil,
		"retry_after_ms":       state.CircuitRetryAfter,
		"gateway_instance_id":  state.GatewayInstanceID,
		"stream_generation_id": state.StreamGenerationID,
		"failure_epoch":        state.FailureEpoch,
	})
}

func (g *Gateway) live(w http.ResponseWriter, r *http.Request) {
	id, ok := parseCameraID(r.URL.Path)
	if !ok {
		http.NotFound(w, r)
		return
	}

	cam := g.configureCameraFromRequest(r, id)

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unavailable", http.StatusInternalServerError)
		return
	}

	if state := g.cameraSnapshot(cam); state.SourceURL == "" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"ok":        false,
			"error":     "camera_source_not_registered",
			"camera_id": id,
		})
		return
	}

	w.Header().Set("Content-Type", "multipart/x-mixed-replace; boundary=frame")
	w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
	w.Header().Set("Pragma", "no-cache")
	w.WriteHeader(http.StatusOK)

	var lastSeq uint64

	for {
		select {
		case <-r.Context().Done():
			return

		case <-time.After(g.streamPollInterval):
			frame, seq, _ := g.latestFrame(cam)
			if len(frame) == 0 || seq == lastSeq {
				continue
			}

			lastSeq = seq

			_, _ = fmt.Fprintf(w, "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n", len(frame))
			_, _ = w.Write(frame)
			_, _ = w.Write([]byte("\r\n"))
			flusher.Flush()
		}
	}
}

func (g *Gateway) registerRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/health", g.healthz)
	mux.HandleFunc("/healthz", g.healthz)
	mux.HandleFunc("/cameras", g.cameraList)

	mux.HandleFunc("/cameras/", func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/source"):
			g.updateSource(w, r)

		case strings.HasSuffix(r.URL.Path, "/frames"):
			g.framesSince(w, r)

		case strings.HasSuffix(r.URL.Path, "/frame/latest.jpg"):
			g.latestFrameJPEG(w, r)

		case strings.HasSuffix(r.URL.Path, "/health"):
			g.cameraHealth(w, r)

		case strings.HasSuffix(r.URL.Path, "/status"):
			g.cameraStatus(w, r)

		case strings.HasSuffix(r.URL.Path, "/snapshot") || strings.HasSuffix(r.URL.Path, "/snapshot.jpg"):
			g.snapshot(w, r)

		case strings.HasSuffix(r.URL.Path, "/stream/live"):
			g.live(w, r)

		default:
			http.NotFound(w, r)
		}
	})
}
