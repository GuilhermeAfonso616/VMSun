package main

import (
	"fmt"
	"strconv"
	"strings"
	"sync"
	"time"
)

type ffmpegLatencyProfile string

const (
	profileCompatibility ffmpegLatencyProfile = "compatibility"
	profileBalanced      ffmpegLatencyProfile = "balanced"
	profileLowLatency    ffmpegLatencyProfile = "low_latency"
)

func parseLatencyProfile(value string) (ffmpegLatencyProfile, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "":
		return profileCompatibility, nil
	case string(profileBalanced):
		return profileBalanced, nil
	case string(profileCompatibility):
		return profileCompatibility, nil
	case string(profileLowLatency), "low-latency":
		return profileLowLatency, nil
	default:
		return "", fmt.Errorf("invalid FFmpeg latency profile %q", value)
	}
}

type cameraSelector struct {
	all bool
	ids map[int]struct{}
}

func parseCameraSelector(value string) cameraSelector {
	selector := cameraSelector{ids: make(map[int]struct{})}
	for _, item := range strings.Split(value, ",") {
		item = strings.TrimSpace(item)
		if item == "*" {
			selector.all = true
			continue
		}
		cameraID, err := strconv.Atoi(item)
		if err == nil && cameraID > 0 {
			selector.ids[cameraID] = struct{}{}
		}
	}
	return selector
}

func (s cameraSelector) selected(cameraID int) bool {
	if s.all {
		return true
	}
	_, ok := s.ids[cameraID]
	return ok
}

func profileFallbackChain(
	selected ffmpegLatencyProfile,
	fallbackEnabled bool,
	strict bool,
) []ffmpegLatencyProfile {
	if strict || !fallbackEnabled {
		return []ffmpegLatencyProfile{selected}
	}
	switch selected {
	case profileLowLatency:
		return []ffmpegLatencyProfile{
			profileLowLatency,
			profileBalanced,
			profileCompatibility,
		}
	case profileBalanced:
		return []ffmpegLatencyProfile{
			profileBalanced,
			profileCompatibility,
		}
	default:
		return []ffmpegLatencyProfile{profileCompatibility}
	}
}

func ffmpegInputArgs(profile ffmpegLatencyProfile) []string {
	switch profile {
	case profileLowLatency:
		return []string{
			"-fflags", "+nobuffer+discardcorrupt",
			"-flags", "low_delay",
			"-max_delay", "0",
			"-analyzeduration", "500000",
			"-probesize", "500000",
		}
	case profileBalanced:
		return []string{
			"-fflags", "+discardcorrupt",
			"-analyzeduration", "1000000",
			"-probesize", "1000000",
		}
	default:
		return nil
	}
}

type latestFrameMailbox struct {
	mu           sync.Mutex
	frames       chan gatewayFrame
	closed       bool
	replacements uint64
}

type gatewayFrame struct {
	JPEG        []byte
	ReceivedAt  time.Time
	PipeRead    time.Duration
	JPEGParse   time.Duration
	ChannelWait time.Duration
}

func newLatestFrameMailbox() *latestFrameMailbox {
	return &latestFrameMailbox{frames: make(chan gatewayFrame, 1)}
}

func (m *latestFrameMailbox) Put(frame gatewayFrame) (replaced bool, ok bool) {
	if len(frame.JPEG) == 0 {
		return false, false
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return false, false
	}
	frame.JPEG = cloneBytes(frame.JPEG)
	select {
	case m.frames <- frame:
		return false, true
	default:
	}
	select {
	case <-m.frames:
		replaced = true
		m.replacements++
	default:
	}
	select {
	case m.frames <- frame:
		return replaced, true
	default:
		return replaced, false
	}
}

func (m *latestFrameMailbox) Frames() <-chan gatewayFrame {
	return m.frames
}

func (m *latestFrameMailbox) Len() int {
	return len(m.frames)
}

func (m *latestFrameMailbox) Replacements() uint64 {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.replacements
}

func (m *latestFrameMailbox) Close() {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return
	}
	m.closed = true
	close(m.frames)
}

type ptsDriftEstimator struct {
	mu              sync.Mutex
	initialized     bool
	basePTS         time.Duration
	baseWall        time.Time
	lastPTS         time.Duration
	lastWall        time.Time
	drift           time.Duration
	discontinuities uint64
}

type ptsObservation struct {
	Available       bool
	PTS             time.Duration
	Drift           time.Duration
	Discontinuity   bool
	Discontinuities uint64
}

type frameObservation struct {
	receivedAt  time.Time
	pts         ptsObservation
	pipeRead    time.Duration
	jpegParse   time.Duration
	channelWait time.Duration
}

func (e *ptsDriftEstimator) Observe(pts time.Duration, wall time.Time) ptsObservation {
	e.mu.Lock()
	defer e.mu.Unlock()
	if pts < 0 || wall.IsZero() {
		return ptsObservation{}
	}
	if !e.initialized {
		e.initialized = true
		e.basePTS = pts
		e.baseWall = wall
		e.lastPTS = pts
		e.lastWall = wall
		e.drift = 0
		return ptsObservation{Available: true, PTS: pts}
	}

	discontinuity := pts+250*time.Millisecond < e.lastPTS ||
		pts-e.lastPTS > 30*time.Second
	if discontinuity {
		e.discontinuities++
		e.basePTS = pts
		e.baseWall = wall
		e.drift = 0
	} else {
		e.drift = wall.Sub(e.baseWall) - (pts - e.basePTS)
	}
	e.lastPTS = pts
	e.lastWall = wall
	return ptsObservation{
		Available:       true,
		PTS:             pts,
		Drift:           e.drift,
		Discontinuity:   discontinuity,
		Discontinuities: e.discontinuities,
	}
}

func (e *ptsDriftEstimator) Snapshot() ptsObservation {
	e.mu.Lock()
	defer e.mu.Unlock()
	if !e.initialized {
		return ptsObservation{}
	}
	return ptsObservation{
		Available:       true,
		PTS:             e.lastPTS,
		Drift:           e.drift,
		Discontinuities: e.discontinuities,
	}
}

func (e *ptsDriftEstimator) Reset() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.initialized = false
	e.basePTS = 0
	e.baseWall = time.Time{}
	e.lastPTS = 0
	e.lastWall = time.Time{}
	e.drift = 0
	e.discontinuities = 0
}

type lagWatchdog struct {
	enabled       bool
	threshold     time.Duration
	hold          time.Duration
	cooldown      time.Duration
	maxPerHour    int
	laggingSince  time.Time
	lastRestart   time.Time
	restartWindow []time.Time
	state         string
}

func newLagWatchdog(
	enabled bool,
	threshold time.Duration,
	hold time.Duration,
	cooldown time.Duration,
	maxPerHour int,
) *lagWatchdog {
	return &lagWatchdog{
		enabled:    enabled,
		threshold:  threshold,
		hold:       hold,
		cooldown:   cooldown,
		maxPerHour: maxPerHour,
		state:      "healthy",
	}
}

func (w *lagWatchdog) Observe(now time.Time, lag time.Duration, available bool) bool {
	if !w.enabled || !available {
		w.state = "healthy"
		w.laggingSince = time.Time{}
		return false
	}
	if lag <= w.threshold {
		w.state = "healthy"
		w.laggingSince = time.Time{}
		return false
	}
	if w.laggingSince.IsZero() {
		w.laggingSince = now
		w.state = "lagging"
		return false
	}
	w.state = "lagging"
	if now.Sub(w.laggingSince) < w.hold {
		return false
	}
	if !w.lastRestart.IsZero() && now.Sub(w.lastRestart) < w.cooldown {
		return false
	}
	cutoff := now.Add(-time.Hour)
	kept := w.restartWindow[:0]
	for _, item := range w.restartWindow {
		if item.After(cutoff) {
			kept = append(kept, item)
		}
	}
	w.restartWindow = kept
	if w.maxPerHour > 0 && len(w.restartWindow) >= w.maxPerHour {
		w.state = "degraded"
		return false
	}
	w.lastRestart = now
	w.restartWindow = append(w.restartWindow, now)
	w.laggingSince = time.Time{}
	w.state = "recovering"
	return true
}

func (w *lagWatchdog) State() string {
	if w == nil {
		return "disabled"
	}
	if !w.enabled {
		return "disabled"
	}
	return w.state
}
