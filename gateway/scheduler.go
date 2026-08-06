package main

import (
	"container/heap"
	"context"
	"fmt"
	"strings"
	"time"
)

func isRecoveryJob(job *StartJob) bool {
	return job != nil && (job.Reason == reasonReconnect || job.Reason == reasonFallback)
}

func (g *Gateway) slotAvailableLocked(job *StartJob) bool {
	if isRecoveryJob(job) {
		return g.activeRecoverCount < g.maxConcurrentRecovers
	}
	return g.activeStartCount < g.maxConcurrentStarts
}

func (g *Gateway) effectivePriorityLocked(cam *CameraRuntime, job *StartJob, now time.Time) int {
	effective := job.Priority
	if g.queueAgingSeconds > 0 && !job.EnqueuedAt.IsZero() {
		effective += int(now.Sub(job.EnqueuedAt).Seconds() / g.queueAgingSeconds)
	}
	if isRecoveryJob(job) {
		switch {
		case cam.state.HealthScore < 20:
			effective -= 2
		case cam.state.HealthScore < 40:
			effective--
		}
	}
	if effective < 1 {
		return 1
	}
	return effective
}

func (g *Gateway) effectivePriority(job *StartJob, now time.Time) int {
	cam := g.ensureCamera(job.CameraID)
	cam.mu.RLock()
	defer cam.mu.RUnlock()
	return g.effectivePriorityLocked(cam, job, now)
}

func (g *Gateway) healthScore(state CameraState) int {
	score := 100
	switch state.State {
	case stateRunning:
		score = 100
	case stateSlow:
		score = 75
	case stateQueued, stateStarting, stateWarmingUp:
		score = 55
	case stateDegraded, stateReconnecting:
		score = 35
	case stateFallback:
		score = 45
	case stateOffline:
		score = 5
	case stateStopped:
		score = 0
	case stateUnstable:
		score = 15
	default:
		score = 20
	}
	score -= state.ConsecutiveFails * 8
	if state.LastFrameAgeMS > g.frameStaleAfter.Milliseconds() {
		score -= 30
	}
	if score < 0 {
		return 0
	}
	if score > 100 {
		return 100
	}
	return score
}

func (g *Gateway) snapshotFrames(cam *CameraRuntime) []FrameRecord {
	cam.mu.RLock()
	defer cam.mu.RUnlock()

	if len(cam.frameRing) == 0 {
		return nil
	}

	frames := make([]FrameRecord, len(cam.frameRing))
	for i := range cam.frameRing {
		frames[i] = FrameRecord{
			Seq:        cam.frameRing[i].Seq,
			CapturedAt: cam.frameRing[i].CapturedAt,
			JPEG:       cloneBytes(cam.frameRing[i].JPEG),
		}
	}

	return frames
}

func (g *Gateway) recordReconnect(cam *CameraRuntime, failureCountDelta int, state string) {
	now := time.Now().UTC()

	cam.mu.Lock()
	cam.state.State = state
	cam.state.LastReconnectAt = utcPtr(now)
	cam.state.FailureCount += failureCountDelta
	cam.state.ReconnectCount++
	cam.state.ConsecutiveFails += failureCountDelta
	cam.state.LastStateChangeAt = utcPtr(now)
	cam.state.HealthScore = g.healthScore(cam.state)
	cam.mu.Unlock()
}

func (g *Gateway) pruneFlapsLocked(cam *CameraRuntime, now time.Time) {
	if g.flapWindow <= 0 || len(cam.flapEvents) == 0 {
		return
	}
	cutoff := now.Add(-g.flapWindow)
	keepFrom := 0
	for keepFrom < len(cam.flapEvents) && cam.flapEvents[keepFrom].Before(cutoff) {
		keepFrom++
	}
	if keepFrom > 0 {
		cam.flapEvents = append([]time.Time(nil), cam.flapEvents[keepFrom:]...)
	}
}

func (g *Gateway) recordFlapLocked(cam *CameraRuntime, now time.Time) bool {
	g.pruneFlapsLocked(cam, now)
	cam.flapEvents = append(cam.flapEvents, now)
	cam.state.FlapCount = len(cam.flapEvents)
	if g.flapThreshold > 0 && len(cam.flapEvents) >= g.flapThreshold {
		if !cam.circuitOpenUntil.After(now) {
			cam.failureEpoch++
			cam.circuitOpenedAt = now
		}
		cam.circuitReason = fmt.Sprintf("%d_reconnects_in_%s", len(cam.flapEvents), g.flapWindow)
		cam.circuitHalfOpen = false
		cam.circuitOpenUntil = now.Add(g.circuitBreaker)
		cam.state.State = stateUnstable
		cam.state.CircuitOpen = true
		cam.state.CircuitState = "open"
		cam.state.CircuitReason = cam.circuitReason
		cam.state.CircuitOpenedAt = utcPtr(cam.circuitOpenedAt)
		cam.state.CircuitOpenUntil = utcPtr(cam.circuitOpenUntil)
		cam.state.CircuitRetryAfter = g.circuitBreaker.Milliseconds()
		cam.state.FailureEpoch = cam.failureEpoch
		cam.state.GatewayInstanceID = g.instanceID
		cam.state.LastError = fmt.Sprintf("circuit open after %d reconnects in %s", len(cam.flapEvents), g.flapWindow)
		cam.state.LastStateChangeAt = utcPtr(now)
		cam.state.HealthScore = g.healthScore(cam.state)
		return true
	}
	return false
}

func (g *Gateway) recordSourceFlapLocked(cam *CameraRuntime, sourceURL string, now time.Time) bool {
	if g.isManagedBackboneSource(sourceURL) {
		return false
	}
	return g.recordFlapLocked(cam, now)
}

func (g *Gateway) enqueueStart(job StartJob) {
	now := time.Now().UTC()
	if job.EnqueuedAt.IsZero() {
		job.EnqueuedAt = now
	}
	if job.NotBefore.IsZero() {
		job.NotBefore = now
	}
	if job.Priority <= 0 {
		job.Priority = g.defaultPriority
	}
	if strings.TrimSpace(job.Reason) == "" {
		job.Reason = reasonInitialStart
	}

	cam := g.ensureCamera(job.CameraID)
	cam.mu.Lock()
	if job.Generation == 0 {
		cam.generation++
		job.Generation = cam.generation
	}
	if job.SourceVersion == 0 {
		if cam.sourceVersion == 0 {
			cam.sourceVersion = 1
		}
		job.SourceVersion = cam.sourceVersion
	}
	if cam.circuitOpenUntil.After(now) && job.NotBefore.Before(cam.circuitOpenUntil) {
		job.NotBefore = cam.circuitOpenUntil
	}
	cam.state.State = stateQueued
	cam.state.Priority = job.Priority
	cam.state.EffectivePriority = g.effectivePriorityLocked(cam, &job, now)
	cam.state.QueuedAt = utcPtr(job.EnqueuedAt)
	cam.state.SourceURL = job.SourceURL
	cam.state.LastStateChangeAt = utcPtr(now)
	cam.state.LastError = ""
	if cam.circuitOpenUntil.After(now) {
		cam.state.CircuitOpen = true
		cam.state.CircuitState = "open"
		cam.state.CircuitReason = cam.circuitReason
		cam.state.CircuitOpenedAt = utcPtr(cam.circuitOpenedAt)
		cam.state.CircuitOpenUntil = utcPtr(cam.circuitOpenUntil)
		cam.state.CircuitRetryAfter = max(int64(0), cam.circuitOpenUntil.Sub(now).Milliseconds())
	}
	cam.mu.Unlock()

	g.queueMu.Lock()
	g.jobSequence++
	job.sequence = g.jobSequence
	jobCopy := job
	if existing := g.pendingJobs[job.CameraID]; existing != nil {
		orchLog.Info("replacing_queued",
			"cam", job.CameraID,
			"old_reason", existing.Reason,
			"new_reason", job.Reason,
			"priority", job.Priority,
		)
	}
	g.pendingJobs[job.CameraID] = &jobCopy
	heap.Push(&g.startQueue, &jobCopy)
	g.queueCond.Broadcast()
	g.queueMu.Unlock()

	orchLog.Info("queued", "cam", job.CameraID, "priority", job.Priority, "reason", job.Reason, "attempt", job.Attempt)
}

func (g *Gateway) nextReadyJob(now time.Time) *StartJob {
	var postponed []*StartJob
	var ready []*StartJob
	var selected *StartJob
	for g.startQueue.Len() > 0 {
		job := heap.Pop(&g.startQueue).(*StartJob)
		if g.pendingJobs[job.CameraID] != job {
			continue
		}
		if job.NotBefore.After(now) {
			postponed = append(postponed, job)
			continue
		}
		if !g.slotAvailableLocked(job) {
			postponed = append(postponed, job)
			continue
		}
		ready = append(ready, job)
	}

	bestIndex := -1
	bestPriority := -1
	for i, job := range ready {
		priority := g.effectivePriority(job, now)
		if priority > bestPriority || (priority == bestPriority && bestIndex >= 0 && job.sequence < ready[bestIndex].sequence) {
			bestPriority = priority
			bestIndex = i
		}
	}
	if bestIndex >= 0 {
		selected = ready[bestIndex]
		ready = append(ready[:bestIndex], ready[bestIndex+1:]...)
	}
	for _, job := range ready {
		heap.Push(&g.startQueue, job)
	}
	for _, job := range postponed {
		heap.Push(&g.startQueue, job)
	}
	return selected
}

func (g *Gateway) hasDispatchCapacityLocked() bool {
	return g.activeStartCount < g.maxConcurrentStarts || g.activeRecoverCount < g.maxConcurrentRecovers
}

func (g *Gateway) orchestratorLoop(ctx context.Context) {
	orchLog.Info("orchestrator_started",
		"max_concurrent_starts", g.maxConcurrentStarts,
		"max_concurrent_recovers", g.maxConcurrentRecovers,
		"stagger", g.startStagger,
	)
	for {
		g.queueMu.Lock()
		for {
			if ctx.Err() != nil {
				g.queueMu.Unlock()
				return
			}
			if g.hasDispatchCapacityLocked() && g.startQueue.Len() > 0 {
				break
			}
			g.queueCond.Wait()
		}

		job := g.nextReadyJob(time.Now().UTC())
		if job == nil {
			g.queueMu.Unlock()
			select {
			case <-ctx.Done():
				return
			case <-time.After(200 * time.Millisecond):
			}
			continue
		}
		delete(g.pendingJobs, job.CameraID)
		if isRecoveryJob(job) {
			g.activeRecoveries[job.CameraID] = job
			g.activeRecoverCount++
		} else {
			g.activeStarts[job.CameraID] = job
			g.activeStartCount++
		}
		g.queueMu.Unlock()

		g.startCaptureJob(ctx, job)

		if g.startStagger > 0 {
			select {
			case <-ctx.Done():
				return
			case <-time.After(g.startStagger):
			}
		}
	}
}
