package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/url"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

var errFrameLagWatchdog = errors.New("FFmpeg frame lag watchdog restart")

func (g *Gateway) startCaptureJob(parent context.Context, job *StartJob) {
	cam := g.ensureCamera(job.CameraID)
	cam.mu.Lock()
	if cam.generation != job.Generation || cam.sourceVersion != job.SourceVersion || cam.state.SourceURL != job.SourceURL {
		cam.mu.Unlock()
		g.releaseStartJob(job)
		return
	}
	if cam.running && cam.state.State == stateRunning && cam.state.LastFrameAt != nil && time.Since(*cam.state.LastFrameAt) <= g.frameStaleAfter && job.Reason != reasonManualRestart {
		cam.mu.Unlock()
		orchLog.Info("skip_redundant", "cam", job.CameraID, "reason", job.Reason)
		g.releaseStartJob(job)
		return
	}
	if cam.cancel != nil {
		cam.cancel()
	}
	now := time.Now().UTC()
	if isRecoveryJob(job) && !cam.circuitOpenedAt.IsZero() && !cam.circuitOpenUntil.After(now) {
		cam.circuitHalfOpen = true
		cam.state.CircuitOpen = false
		cam.state.CircuitState = "half_open"
		cam.state.CircuitReason = cam.circuitReason
		cam.state.CircuitOpenedAt = utcPtr(cam.circuitOpenedAt)
		cam.state.CircuitOpenUntil = nil
		cam.state.CircuitRetryAfter = 0
	}
	ctx, cancel := context.WithCancel(parent)
	cam.cancel = cancel
	cam.running = true
	cam.lastStarted = now
	cam.state.State = stateStarting
	cam.state.StartedAt = utcPtr(cam.lastStarted)
	cam.state.QueueWaitMS = durationMs(job.EnqueuedAt, cam.lastStarted)
	cam.state.EffectivePriority = g.effectivePriorityLocked(cam, job, cam.lastStarted)
	cam.state.LastStateChangeAt = utcPtr(cam.lastStarted)
	cam.state.LastError = ""
	cam.mu.Unlock()

	g.metricsMu.Lock()
	g.totalStarts++
	g.metricsMu.Unlock()

	orchLog.Info("starting",
		"cam", job.CameraID,
		"priority", job.Priority,
		"effective_priority", g.effectivePriority(job, cam.lastStarted),
		"attempt", job.Attempt,
		"reason", job.Reason,
		"queue_wait_ms", durationMs(job.EnqueuedAt, cam.lastStarted),
	)
	go g.captureLoop(ctx, cam, job)
}

func (g *Gateway) releaseStartJob(job *StartJob) {
	g.queueMu.Lock()
	if g.activeStarts[job.CameraID] == job {
		delete(g.activeStarts, job.CameraID)
		if g.activeStartCount > 0 {
			g.activeStartCount--
		}
	}
	if g.activeRecoveries[job.CameraID] == job {
		delete(g.activeRecoveries, job.CameraID)
		if g.activeRecoverCount > 0 {
			g.activeRecoverCount--
		}
	}
	g.queueCond.Broadcast()
	g.queueMu.Unlock()
}

func retryDelayWithProfile(attempt int, baseDelay time.Duration, maxDelay time.Duration) time.Duration {
	if attempt < 0 {
		attempt = 0
	}
	delay := baseDelay
	for i := 0; i < attempt; i++ {
		delay *= 2
		if delay >= maxDelay {
			delay = maxDelay
			break
		}
	}
	if delay <= 0 {
		delay = time.Second
	}
	jitterMax := delay / 5
	if jitterMax > 0 {
		delay += time.Duration(rand.Int63n(int64(jitterMax)))
	}
	if delay > maxDelay {
		delay = maxDelay
	}
	return delay
}

func (g *Gateway) isManagedBackboneSource(sourceURL string) bool {
	parsed, err := url.Parse(strings.TrimSpace(sourceURL))
	if err != nil || parsed.Hostname() == "" {
		return false
	}
	_, allowed := g.allowedRTSPHosts[strings.ToLower(parsed.Hostname())]
	return allowed
}

func (g *Gateway) retryProfile(sourceURL string) (time.Duration, time.Duration) {
	if g.isManagedBackboneSource(sourceURL) {
		return g.backboneReconnectDelay, g.backboneReconnectBackoffMax
	}
	return g.reconnectDelay, g.reconnectBackoffMax
}

func (g *Gateway) retryDelayForSource(attempt int, sourceURL string) time.Duration {
	baseDelay, maxDelay := g.retryProfile(sourceURL)
	return retryDelayWithProfile(attempt, baseDelay, maxDelay)
}

func (g *Gateway) retryDelay(attempt int) time.Duration {
	return retryDelayWithProfile(attempt, g.reconnectDelay, g.reconnectBackoffMax)
}

func (g *Gateway) resolveFallbackSource(job *StartJob) (string, bool) {
	if !g.enableFallbackRTSP {
		return job.SourceURL, false
	}
	return job.SourceURL, false
}

func (g *Gateway) scheduleRetry(cam *CameraRuntime, job *StartJob, reason string, err error) {
	nextAttempt := job.Attempt + 1
	retryReason := reasonReconnect
	sourceURL := job.SourceURL
	if nextAttempt >= g.maxRetryBeforeFB {
		if fallbackURL, ok := g.resolveFallbackSource(job); ok {
			sourceURL = fallbackURL
			retryReason = reasonFallback
			g.setState(cam, stateFallback)
			fallbackLog.Warn("selected", "cam", job.CameraID, "attempt", nextAttempt)
		} else {
			fallbackLog.Warn("unavailable", "cam", job.CameraID, "attempt", nextAttempt)
		}
	}

	baseDelay, maxDelay := g.retryProfile(sourceURL)
	delay := g.retryDelayForSource(nextAttempt, sourceURL)
	now := time.Now().UTC()
	cam.mu.Lock()
	circuitOpened := g.recordSourceFlapLocked(cam, sourceURL, now)
	if cam.state.HealthScore < 20 {
		delay *= 2
	} else if cam.state.HealthScore < 40 {
		delay += baseDelay
	}
	if delay > maxDelay {
		delay = maxDelay
	}
	if circuitOpened && cam.circuitOpenUntil.After(now.Add(delay)) {
		delay = cam.circuitOpenUntil.Sub(now)
	}
	if !circuitOpened {
		cam.state.State = stateReconnecting
	}
	cam.state.LastReconnectAt = utcPtr(now)
	cam.state.LastError = fmt.Sprint(err)
	cam.state.LastStateChangeAt = utcPtr(now)
	cam.state.HealthScore = g.healthScore(cam.state)
	sourceVersion := cam.sourceVersion
	cam.mu.Unlock()

	g.metricsMu.Lock()
	g.totalStartFailures++
	g.metricsMu.Unlock()

	if circuitOpened {
		recoveryLog.Warn("circuit_open", "cam", job.CameraID, "until", now.Add(delay).Format(time.RFC3339Nano))
	}
	recoveryLog.Warn("queued",
		"cam", job.CameraID,
		"reason", reason,
		"attempt", nextAttempt,
		"delay_ms", delay.Milliseconds(),
		"error", err,
	)
	g.enqueueStart(StartJob{
		CameraID:      job.CameraID,
		SourceURL:     sourceURL,
		Priority:      job.Priority,
		Attempt:       nextAttempt,
		Reason:        retryReason,
		EnqueuedAt:    now,
		NotBefore:     now.Add(delay),
		Generation:    job.Generation,
		SourceVersion: sourceVersion,
	})
}

func (g *Gateway) captureLoop(ctx context.Context, cam *CameraRuntime, job *StartJob) {
	id := job.CameraID
	sourceURL := job.SourceURL
	captureLog.Info("loop_start", "cam", id, "reason", job.Reason, "attempt", job.Attempt)

	defer func() {
		cam.mu.Lock()
		if cam.cancel != nil && ctx.Err() != nil {
			cam.running = false
			if cam.state.State != stateOffline && cam.state.State != stateReconnecting && cam.state.State != stateQueued && cam.state.State != stateUnstable {
				cam.state.State = stateStopped
			}
		}
		cam.mu.Unlock()

		captureLog.Info("loop_stop", "cam", id)
	}()

	select {
	case <-ctx.Done():
		g.releaseStartJob(job)
		return
	default:
	}

	g.setState(cam, stateWarmingUp)

	selectedProfile := g.profileForCamera(id)
	profiles := profileFallbackChain(
		selectedProfile,
		g.ffmpegProfileFallback,
		g.ffmpegProfileStrict,
	)
	var err error
	for index, profile := range profiles {
		cam.mu.Lock()
		cam.state.FFmpegConfiguredProfile = string(selectedProfile)
		cam.state.FFmpegLatencyProfile = string(profile)
		cam.mu.Unlock()

		err = g.runFFmpegOnce(
			ctx,
			cam,
			job,
			sourceURL,
			g.ffmpegHWAccelEnabled,
			profile,
		)
		if err != nil &&
			g.ffmpegHWAccelEnabled &&
			g.ffmpegHWAccelFallback &&
			!errors.Is(err, errFrameLagWatchdog) &&
			!errors.Is(err, context.Canceled) &&
			ctx.Err() == nil {
			captureLog.Warn(
				"hwaccel_failed_fallback_cpu",
				"cam", id,
				"profile", profile,
				"error", err,
			)
			err = g.runFFmpegOnce(ctx, cam, job, sourceURL, false, profile)
		}
		if errors.Is(err, errFrameLagWatchdog) ||
			errors.Is(err, context.Canceled) ||
			ctx.Err() != nil ||
			err == nil {
			break
		}
		if index+1 < len(profiles) {
			nextProfile := profiles[index+1]
			cam.mu.Lock()
			cam.state.FFmpegProfileFallbackTotal++
			cam.state.LastRestartReason = "profile_fallback"
			cam.mu.Unlock()
			captureLog.Warn(
				"profile_fallback",
				"cam", id,
				"from", profile,
				"to", nextProfile,
				"error", err,
			)
		}
	}
	if errors.Is(err, context.Canceled) || ctx.Err() != nil {
		g.releaseStartJob(job)
		return
	}
	if errors.Is(err, errFrameLagWatchdog) {
		cam.mu.Lock()
		cam.generation++
		job.Generation = cam.generation
		cam.latestJPEG = nil
		cam.latestSeq = 0
		cam.frameRing = nil
		cam.state.LastRestartReason = "frame_lag_watchdog"
		cam.mu.Unlock()
	}

	g.recordReconnect(cam, 1, stateReconnecting)
	if err != nil {
		captureLog.Error("failed", "cam", id, "error", err)
	}
	g.releaseStartJob(job)
	g.scheduleRetry(cam, job, reasonReconnect, err)
}

func (g *Gateway) healthMonitorLoop(ctx context.Context) {
	ticker := time.NewTicker(g.healthInterval)
	defer ticker.Stop()
	healthLog.Info("monitor_started", "interval", g.healthInterval, "stale_after", g.frameStaleAfter)

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}

		now := time.Now().UTC()
		g.mu.Lock()
		cameras := make([]*CameraRuntime, 0, len(g.cameras))
		for _, cam := range g.cameras {
			cameras = append(cameras, cam)
		}
		g.mu.Unlock()

		for _, cam := range cameras {
			var cancel context.CancelFunc
			var enqueue *StartJob
			var cameraID int
			var lastFrameAge time.Duration

			cam.mu.Lock()
			cameraID = cam.state.CameraID
			if cam.state.LastFrameAt != nil {
				lastFrameAge = now.Sub(cam.state.LastFrameAt.UTC())
				cam.state.LastFrameAgeMS = lastFrameAge.Milliseconds()
			}
			cam.state.HealthScore = g.healthScore(cam.state)

			if cam.running && cam.state.State == stateRunning && cam.state.LastFrameAt != nil && lastFrameAge > g.frameStaleAfter {
				cam.state.State = stateDegraded
				cam.state.ConsecutiveFails++
				cam.state.LastError = fmt.Sprintf("stale frame age=%s", lastFrameAge)
				cam.state.LastStateChangeAt = utcPtr(now)
				cam.state.HealthScore = g.healthScore(cam.state)
				circuitOpened := g.recordSourceFlapLocked(cam, cam.state.SourceURL, now)
				delay := g.retryDelayForSource(cam.state.ConsecutiveFails, cam.state.SourceURL)
				if circuitOpened && cam.circuitOpenUntil.After(now.Add(delay)) {
					delay = cam.circuitOpenUntil.Sub(now)
				}
				cancel = cam.cancel
				enqueue = &StartJob{
					CameraID:      cam.state.CameraID,
					SourceURL:     cam.state.SourceURL,
					Priority:      cam.state.Priority,
					Attempt:       cam.state.ConsecutiveFails,
					Reason:        reasonReconnect,
					EnqueuedAt:    now,
					NotBefore:     now.Add(delay),
					Generation:    cam.generation,
					SourceVersion: cam.sourceVersion,
				}
				if !circuitOpened {
					cam.state.State = stateReconnecting
				}
				cam.state.LastReconnectAt = utcPtr(now)
				cam.state.ReconnectCount++
			}
			cam.mu.Unlock()

			if enqueue != nil {
				healthLog.Warn("degraded", "cam", cameraID, "last_frame_age_ms", lastFrameAge.Milliseconds())
				if cancel != nil {
					cancel()
				}
				g.enqueueStart(*enqueue)
			}
		}
	}
}

func (g *Gateway) ffmpegArgs(
	sourceURL string,
	hwAccel bool,
	profile ffmpegLatencyProfile,
) []string {
	args := []string{
		"-hide_banner",
		"-loglevel", "warning",
		"-nostats",
		"-stats_period", "0.25",
		"-progress", "pipe:2",
	}

	if hwAccel {
		if value := strings.TrimSpace(g.ffmpegHWAccel); value != "" {
			args = append(args, "-hwaccel", value)
		}
		if value := strings.TrimSpace(g.ffmpegHWOutputFormat); value != "" {
			args = append(args, "-hwaccel_output_format", value)
		}
		if value := strings.TrimSpace(g.ffmpegVideoDecoder); value != "" {
			args = append(args, "-c:v", value)
		}
	}

	args = append(args, "-rtsp_transport", g.rtspTransport)
	args = append(args, ffmpegInputArgs(profile)...)
	args = append(args,
		"-i", sourceURL,
		"-map", "0:v:0",
		"-an",
		"-vf", g.ffmpegVideoFilter(hwAccel),
		"-q:v", g.jpegQuality,
		"-f", "image2pipe",
		"-vcodec", "mjpeg",
		"pipe:1",
	)
	return args
}

func sanitizedFFmpegCommand(path string, args []string) string {
	safe := append([]string(nil), args...)
	for index := 0; index < len(safe)-1; index++ {
		if safe[index] == "-i" {
			safe[index+1] = maskSourceURL(safe[index+1])
		}
	}
	return strings.TrimSpace(path + " " + strings.Join(safe, " "))
}

func (g *Gateway) ffmpegVideoFilter(hwAccel bool) string {
	scaleFilter := ""
	if g.ffmpegGPUScaleWidth > 0 && g.ffmpegGPUScaleHeight > 0 {
		scaleFilter = fmt.Sprintf("scale=%d:%d", g.ffmpegGPUScaleWidth, g.ffmpegGPUScaleHeight)
	}

	if !hwAccel {
		if scaleFilter != "" {
			return "fps=" + g.outputFPS + "," + scaleFilter
		}
		return "fps=" + g.outputFPS
	}

	filters := make([]string, 0, 4)
	framesInGPU := strings.TrimSpace(g.ffmpegHWOutputFormat) != ""
	if framesInGPU && g.ffmpegGPUScaleEnabled && g.ffmpegGPUScaleWidth > 0 && g.ffmpegGPUScaleHeight > 0 {
		filters = append(filters, fmt.Sprintf("scale_cuda=%d:%d", g.ffmpegGPUScaleWidth, g.ffmpegGPUScaleHeight))
	}
	if framesInGPU {
		filters = append(filters, "hwdownload", "format=nv12")
	}
	filters = append(filters, "fps="+g.outputFPS)
	if (!framesInGPU || !g.ffmpegGPUScaleEnabled) && scaleFilter != "" {
		filters = append(filters, scaleFilter)
	}
	return strings.Join(filters, ",")
}

func (g *Gateway) runFFmpegOnce(
	ctx context.Context,
	cam *CameraRuntime,
	job *StartJob,
	sourceURL string,
	hwAccel bool,
	profile ffmpegLatencyProfile,
) error {
	id := job.CameraID
	handshakeReleased := false
	startedAt := time.Now().UTC()

	args := g.ffmpegArgs(sourceURL, hwAccel, profile)
	cmd := exec.CommandContext(ctx, g.ffmpegPath, args...)
	configureCommandProcess(cmd)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}

	stderr, err := cmd.StderrPipe()
	if err != nil {
		return err
	}

	if err := cmd.Start(); err != nil {
		g.recordReconnect(cam, 1, stateOffline)
		return err
	}
	mode := "cpu"
	if hwAccel {
		mode = "hwaccel"
	}
	cam.mu.Lock()
	cam.state.FFmpegPID = cmd.Process.Pid
	if cam.state.FFmpegGeneration > 0 {
		cam.state.FFmpegProcessRestartsTotal++
	}
	cam.state.FFmpegGeneration++
	cam.state.FFmpegReady = false
	cam.state.FFmpegLatencyProfile = string(profile)
	cam.state.FFmpegStartSeconds = 0
	cam.mu.Unlock()
	defer func() {
		cam.mu.Lock()
		if cam.state.FFmpegPID == cmd.Process.Pid {
			cam.state.FFmpegPID = 0
			cam.state.FFmpegReady = false
		}
		cam.mu.Unlock()
	}()
	captureLog.Info(
		"ffmpeg_started",
		"cam", id,
		"pid", cmd.Process.Pid,
		"mode", mode,
		"profile", profile,
		"command", sanitizedFFmpegCommand(g.ffmpegPath, args),
	)

	progressUpdates := make(chan ptsObservation, 4)
	stderrDone := make(chan []byte, 1)
	go func() {
		data := readFFmpegProgress(stderr, func(pts time.Duration) {
			observation := cam.ptsEstimator.Observe(pts, time.Now())
			select {
			case progressUpdates <- observation:
			default:
			}
		})
		stderrDone <- data
	}()

	mailbox := newLatestFrameMailbox()
	readDone := make(chan error, 1)
	go func() {
		readDone <- readMJPEGFrames(stdout, mailbox, func(
			replaced bool,
			backlog int,
			frame gatewayFrame,
		) {
			now := time.Now()
			cam.mu.Lock()
			if cam.frameMetricsAt.IsZero() {
				cam.frameMetricsAt = now
			}
			cam.state.GatewayFramesReceivedTotal++
			cam.state.GatewayFramesDecodedTotal++
			if replaced {
				cam.state.GatewayFramesDroppedTotal++
				cam.state.GatewayFramesReplacedTotal++
				cam.state.LatestMailboxReplacements++
				cam.state.DroppedFrames++
			}
			cam.state.GatewayPipeBacklogEstimate = backlog
			cam.state.PipeReadMS = float64(frame.PipeRead) / float64(time.Millisecond)
			cam.state.JPEGParseMS = float64(frame.JPEGParse) / float64(time.Millisecond)
			cam.state.ChannelWaitMS = float64(frame.ChannelWait) / float64(time.Millisecond)
			elapsed := now.Sub(cam.frameMetricsAt).Seconds()
			if elapsed > 0 {
				cam.state.GatewayDecodeFPS = float64(
					cam.state.GatewayFramesReceivedTotal,
				) / elapsed
			}
			cam.mu.Unlock()
		})
		mailbox.Close()
	}()

	firstFrameTimer := time.NewTimer(g.firstFrameTimeout)
	defer firstFrameTimer.Stop()

	gotFrame := false

	for {
		select {
		case <-ctx.Done():
			terminateCommand(cmd)
			_ = cmd.Wait()
			return context.Canceled

		case frame, ok := <-mailbox.Frames():
			if !ok {
				waitErr := cmd.Wait()

				select {
				case errBytes := <-stderrDone:
					if len(errBytes) > 0 {
						return fmt.Errorf("ffmpeg ended: %v: %s", waitErr, strings.TrimSpace(string(errBytes)))
					}
				default:
				}

				return waitErr
			}

			published := g.recordFrameObserved(
				cam,
				frame.JPEG,
				frameObservation{
					receivedAt:  frame.ReceivedAt,
					pts:         cam.ptsEstimator.Snapshot(),
					pipeRead:    frame.PipeRead,
					jpegParse:   frame.JPEGParse,
					channelWait: frame.ChannelWait,
				},
			)
			if !published {
				continue
			}
			gotFrame = true
			if !handshakeReleased {
				handshakeReleased = true
				firstFrameMs := time.Since(startedAt).Milliseconds()
				cam.mu.Lock()
				cam.state.FirstFrameMS = firstFrameMs
				cam.state.FFmpegTimeToFirstFrameMS = firstFrameMs
				cam.state.FFmpegStartSeconds = time.Since(startedAt).Seconds()
				cam.state.FFmpegReady = true
				cam.state.State = stateRunning
				cam.state.ConsecutiveFails = 0
				cam.state.LastError = ""
				cam.state.HealthScore = 100
				cam.mu.Unlock()
				g.metricsMu.Lock()
				g.totalFirstFrames++
				g.totalFirstFrameMS += firstFrameMs
				g.metricsMu.Unlock()
				orchLog.Info("first_frame", "cam", id, "ms", firstFrameMs)
				g.releaseStartJob(job)
			}

		case observation := <-progressUpdates:
			if !observation.Available {
				continue
			}
			driftMS := float64(observation.Drift) / float64(time.Millisecond)
			sourceAgeMS := max(0.0, driftMS)
			ptsSeconds := observation.PTS.Seconds()
			now := time.Now()
			cam.mu.Lock()
			cam.state.FramePTSSeconds = &ptsSeconds
			cam.state.FramePTSDriftMS = &driftMS
			cam.state.FrameSourceEstimatedAgeMS = &sourceAgeMS
			cam.state.PTSDiscontinuitiesTotal = observation.Discontinuities
			if observation.Discontinuity {
				captureLog.Warn(
					"pts_discontinuity",
					"cam", id,
					"profile", profile,
					"pts_seconds", ptsSeconds,
				)
			}
			restart := false
			if cam.watchdog != nil {
				restart = cam.watchdog.Observe(
					now,
					max(time.Duration(0), observation.Drift),
					true,
				)
			}
			cam.state.WatchdogState = cam.watchdog.State()
			if cam.state.WatchdogState == "lagging" {
				cam.state.WatchdogLagEventsTotal++
			}
			if restart {
				cam.state.WatchdogRestartsTotal++
				cam.state.LastRestartReason = "frame_lag_watchdog"
			}
			cam.mu.Unlock()
			if restart {
				captureLog.Warn(
					"frame_lag_watchdog_restart",
					"cam", id,
					"profile", profile,
					"drift_ms", driftMS,
				)
				terminateCommand(cmd)
				_ = cmd.Wait()
				return errFrameLagWatchdog
			}

		case err := <-readDone:
			if err != nil {
				terminateCommand(cmd)
				_ = cmd.Wait()
				return err
			}

		case <-firstFrameTimer.C:
			if !gotFrame {
				terminateCommand(cmd)
				_ = cmd.Wait()
				cam.mu.Lock()
				cam.state.State = stateSlow
				cam.state.LastError = fmt.Sprintf("no frame received within %s", g.firstFrameTimeout)
				cam.state.HealthScore = g.healthScore(cam.state)
				cam.mu.Unlock()
				return fmt.Errorf("no frame received within %s", g.firstFrameTimeout)
			}
		}
	}
}

func readMJPEGFrames(
	reader io.Reader,
	mailbox *latestFrameMailbox,
	onFrame func(replaced bool, backlog int, frame gatewayFrame),
) error {
	const maxBufferBytes = 32 * 1024 * 1024

	buffer := make([]byte, 0, 1024*1024)
	chunk := make([]byte, 32*1024)

	for {
		readStarted := time.Now()
		n, err := reader.Read(chunk)
		pipeRead := time.Since(readStarted)
		if n > 0 {
			buffer = append(buffer, chunk[:n]...)

			for {
				parseStarted := time.Now()
				start := findJPEGStart(buffer)
				if start < 0 {
					if len(buffer) > 1 {
						buffer = append(buffer[:0], buffer[len(buffer)-1:]...)
					}
					break
				}

				if start > 0 {
					buffer = buffer[start:]
				}

				end := findJPEGEnd(buffer)
				if end < 0 {
					if len(buffer) > maxBufferBytes {
						return fmt.Errorf("mjpeg buffer exceeded %d bytes without JPEG end marker", maxBufferBytes)
					}
					break
				}

				frame := gatewayFrame{
					JPEG:       append([]byte(nil), buffer[:end+2]...),
					ReceivedAt: time.Now().UTC(),
					PipeRead:   pipeRead,
					JPEGParse:  time.Since(parseStarted),
				}
				channelStarted := time.Now()
				replaced, accepted := mailbox.Put(frame)
				frame.ChannelWait = time.Since(channelStarted)
				if onFrame != nil {
					onFrame(replaced, mailbox.Len(), frame)
				}
				if !accepted {
					return nil
				}

				buffer = buffer[end+2:]
			}
		}

		if err != nil {
			if err == io.EOF {
				return nil
			}
			return err
		}
	}
}

func readFFmpegProgress(
	reader io.Reader,
	onPTS func(time.Duration),
) []byte {
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 16*1024), 256*1024)
	var warnings strings.Builder
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		key, value, found := strings.Cut(line, "=")
		if found && key == "out_time_us" {
			microseconds, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
			if err == nil && microseconds >= 0 && onPTS != nil {
				onPTS(time.Duration(microseconds) * time.Microsecond)
			}
			continue
		}
		if found && (key == "frame" || key == "fps" || key == "progress" ||
			key == "speed" || key == "out_time" || key == "bitrate" ||
			key == "out_time_ms" || key == "total_size" ||
			key == "dup_frames" || key == "drop_frames") {
			continue
		}
		if warnings.Len()+len(line)+1 <= 16*1024 {
			warnings.WriteString(line)
			warnings.WriteByte('\n')
		}
	}
	return []byte(strings.TrimSpace(warnings.String()))
}

func findJPEGStart(buffer []byte) int {
	for i := 0; i < len(buffer)-1; i++ {
		if buffer[i] == 0xff && buffer[i+1] == 0xd8 {
			return i
		}
	}

	return -1
}

func findJPEGEnd(buffer []byte) int {
	for i := 2; i < len(buffer)-1; i++ {
		if buffer[i] == 0xff && buffer[i+1] == 0xd9 {
			return i
		}
	}

	return -1
}
