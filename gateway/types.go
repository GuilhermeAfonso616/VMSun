package main

import (
	"context"
	"sync"
	"time"
)

const (
	stateIdle         = "idle"
	stateQueued       = "queued"
	stateStarting     = "starting"
	stateWarmingUp    = "warming_up"
	stateRunning      = "running"
	stateSlow         = "slow"
	stateDegraded     = "degraded"
	stateReconnecting = "reconnecting"
	stateFallback     = "fallback"
	stateOffline      = "offline"
	stateStopped      = "stopped_manual"
	stateUnstable     = "unstable"
)

const (
	reasonInitialStart  = "initial_start"
	reasonReconnect     = "reconnect"
	reasonManualRestart = "manual_restart"
	reasonFallback      = "fallback"
	reasonManualStop    = "manual_stop"
)

type CameraState struct {
	CameraID                        int        `json:"camera_id"`
	State                           string     `json:"state"`
	Priority                        int        `json:"priority"`
	EffectivePriority               int        `json:"effective_priority"`
	QueueWaitMS                     int64      `json:"queue_wait_ms"`
	FirstFrameMS                    int64      `json:"first_frame_ms"`
	LastFrameAgeMS                  int64      `json:"last_frame_age_ms"`
	LastFrameAt                     *time.Time `json:"last_frame_at,omitempty"`
	LastReconnectAt                 *time.Time `json:"last_reconnect_at,omitempty"`
	FailureCount                    int        `json:"failure_count"`
	ReconnectCount                  int        `json:"reconnect_count"`
	ConsecutiveFails                int        `json:"consecutive_fails"`
	DroppedFrames                   int64      `json:"dropped_frames"`
	GatewayFramesReceivedTotal      uint64     `json:"gateway_frames_received_total"`
	GatewayFramesPublishedTotal     uint64     `json:"gateway_frames_published_total"`
	GatewayFramesDroppedTotal       uint64     `json:"gateway_frames_dropped_total"`
	GatewayFramesDecodedTotal       uint64     `json:"gateway_frames_decoded_total"`
	GatewayFramesReplacedTotal      uint64     `json:"gateway_frames_replaced_total"`
	GatewayFramesStaleTotal         uint64     `json:"gateway_frames_stale_total"`
	GatewayDecodeFPS                float64    `json:"gateway_decode_fps"`
	GatewayPublishFPS               float64    `json:"gateway_publish_fps"`
	GatewayPipeBacklogEstimate      int        `json:"gateway_pipe_backlog_estimate"`
	FFmpegLatencyProfile            string     `json:"latency_profile"`
	FFmpegConfiguredProfile         string     `json:"configured_latency_profile"`
	FFmpegProfileFallbackTotal      uint64     `json:"gateway_ffmpeg_profile_fallback_total"`
	FFmpegProcessRestartsTotal      uint64     `json:"gateway_ffmpeg_process_restarts_total"`
	FFmpegPID                       int        `json:"ffmpeg_pid"`
	FFmpegGeneration                uint64     `json:"ffmpeg_generation"`
	FFmpegReady                     bool       `json:"ffmpeg_ready"`
	FFmpegStartSeconds              float64    `json:"gateway_ffmpeg_start_seconds"`
	FFmpegTimeToFirstFrameMS        int64      `json:"gateway_ffmpeg_time_to_first_frame_ms"`
	FrameIntervalMS                 float64    `json:"gateway_frame_interval_ms"`
	FrameLocalAgeMS                 float64    `json:"gateway_frame_local_age_ms"`
	FrameSourceEstimatedAgeMS       *float64   `json:"gateway_frame_source_estimated_age_ms"`
	FramePTSDriftMS                 *float64   `json:"gateway_frame_pts_drift_ms"`
	FramePTSSeconds                 *float64   `json:"gateway_frame_pts_seconds"`
	PipeReadMS                      float64    `json:"gateway_pipe_read_ms"`
	JPEGParseMS                     float64    `json:"gateway_jpeg_parse_ms"`
	JPEGPublishMS                   float64    `json:"gateway_jpeg_publish_ms"`
	ChannelWaitMS                   float64    `json:"gateway_channel_wait_ms"`
	LatestMailboxReplacements       uint64     `json:"gateway_latest_mailbox_replacements_total"`
	WatchdogState                   string     `json:"watchdog_state"`
	WatchdogLagEventsTotal          uint64     `json:"gateway_watchdog_lag_events_total"`
	WatchdogRestartsTotal           uint64     `json:"gateway_watchdog_restarts_total"`
	PTSDiscontinuitiesTotal         uint64     `json:"gateway_pts_discontinuities_total"`
	LastRestartReason               string     `json:"last_restart_reason,omitempty"`
	StreamGenerationID              string     `json:"stream_generation_id"`
	HealthScore                     int        `json:"health_score"`
	FlapCount                       int        `json:"flap_count"`
	CircuitOpen                     bool       `json:"circuit_open"`
	CircuitState                    string     `json:"circuit_state"`
	CircuitReason                   string     `json:"circuit_reason,omitempty"`
	CircuitOpenedAt                 *time.Time `json:"circuit_opened_at,omitempty"`
	CircuitOpenUntil                *time.Time `json:"circuit_open_until,omitempty"`
	CircuitRetryAfter               int64      `json:"retry_after_ms"`
	GatewayInstanceID               string     `json:"gateway_instance_id"`
	FailureEpoch                    uint64     `json:"failure_epoch"`
	FrameTransportMode              string     `json:"frame_transport_mode"`
	SharedBufferReady               bool       `json:"shared_buffer_ready"`
	SharedBufferGeneration          uint64     `json:"shared_buffer_generation"`
	SharedBufferCapacityBytes       int        `json:"shared_buffer_capacity_bytes"`
	SharedBufferSlots               int        `json:"shared_buffer_slots"`
	SharedBufferFramesWritten       uint64     `json:"shared_buffer_frames_written_total"`
	SharedBufferFramesOverwritten   uint64     `json:"shared_buffer_frames_overwritten_total"`
	SharedBufferWriteErrors         uint64     `json:"shared_buffer_write_errors_total"`
	SharedBufferPayloadTooLarge     uint64     `json:"shared_buffer_payload_too_large_total"`
	SharedBufferLastFrameID         uint64     `json:"shared_buffer_last_frame_id"`
	SharedBufferLastWriteAgeMS      int64      `json:"shared_buffer_last_write_age_ms"`
	FrameTransportHTTPFallbackTotal uint64     `json:"frame_transport_http_fallback_total"`
	FrameTransportHTTPRequestsTotal uint64     `json:"frame_transport_http_requests_total"`
	FrameTransportError             string     `json:"frame_transport_error,omitempty"`
	LastError                       string     `json:"last_error,omitempty"`
	SourceURL                       string     `json:"-"`
	QueuedAt                        *time.Time `json:"queued_at,omitempty"`
	StartedAt                       *time.Time `json:"started_at,omitempty"`
	LastStateChangeAt               *time.Time `json:"last_state_change_at,omitempty"`
}

type FrameRecord struct {
	Seq        uint64
	CapturedAt time.Time
	JPEG       []byte
}

type CameraRuntime struct {
	mu                sync.RWMutex
	state             CameraState
	latestJPEG        []byte
	latestSeq         uint64
	frameRing         []FrameRecord
	frameRingMax      int
	cancel            context.CancelFunc
	running           bool
	lastStarted       time.Time
	generation        uint64
	sourceVersion     uint64
	flapEvents        []time.Time
	circuitOpenedAt   time.Time
	circuitOpenUntil  time.Time
	circuitReason     string
	circuitHalfOpen   bool
	failureEpoch      uint64
	frameHTTPRequests uint64
	frameMetricsAt    time.Time
	lastFrameRecorded time.Time
	ptsEstimator      ptsDriftEstimator
	watchdog          *lagWatchdog
}

type Gateway struct {
	mu                          sync.Mutex
	cameras                     map[int]*CameraRuntime
	ffmpegPath                  string
	reconnectDelay              time.Duration
	backboneReconnectDelay      time.Duration
	frameStaleAfter             time.Duration
	firstFrameTimeout           time.Duration
	snapshotWaitTimeout         time.Duration
	streamPollInterval          time.Duration
	outputFPS                   string
	jpegQuality                 string
	ffmpegHWAccelEnabled        bool
	ffmpegHWAccelFallback       bool
	ffmpegHWAccel               string
	ffmpegHWOutputFormat        string
	ffmpegVideoDecoder          string
	ffmpegGPUScaleEnabled       bool
	ffmpegGPUScaleWidth         int
	ffmpegGPUScaleHeight        int
	rtspTransport               string
	lowLatency                  bool
	ffmpegLatencyProfile        ffmpegLatencyProfile
	ffmpegBalancedCameras       cameraSelector
	ffmpegLowLatencyCameras     cameraSelector
	ffmpegProfileFallback       bool
	ffmpegProfileStrict         bool
	maxAnalyticFrameAge         time.Duration
	maxAnalyticFrameAgeCameras  cameraSelector
	lagWatchdogEnabled          bool
	lagWatchdogCameras          cameraSelector
	lagRestartThreshold         time.Duration
	lagRestartHold              time.Duration
	lagRestartCooldown          time.Duration
	lagRestartMaxPerHour        int
	frameRingMax                int
	maxConcurrentStarts         int
	maxConcurrentRecovers       int
	startStagger                time.Duration
	reconnectBackoffMax         time.Duration
	backboneReconnectBackoffMax time.Duration
	enableFallbackRTSP          bool
	defaultPriority             int
	maxRetryBeforeFB            int
	queueAgingSeconds           float64
	flapWindow                  time.Duration
	flapThreshold               int
	circuitBreaker              time.Duration
	nodeMaxActiveCameras        int
	healthInterval              time.Duration
	instanceID                  string
	sourcePolicy                string
	allowedRTSPHosts            map[string]struct{}
	orchCtx                     context.Context
	orchCancel                  context.CancelFunc
	queueMu                     sync.Mutex
	queueCond                   *sync.Cond
	startQueue                  StartPriorityQueue
	pendingJobs                 map[int]*StartJob
	activeStarts                map[int]*StartJob
	activeRecoveries            map[int]*StartJob
	activeStartCount            int
	activeRecoverCount          int
	jobSequence                 int64
	metricsMu                   sync.Mutex
	totalStarts                 int64
	totalStartFailures          int64
	totalFirstFrameMS           int64
	totalFirstFrames            int64
	frameTransport              *frameTransportManager
}

type sourceRequest struct {
	SourceURL string `json:"source_url"`
	Priority  int    `json:"priority,omitempty"`
	Reason    string `json:"reason,omitempty"`
}

type StartJob struct {
	CameraID      int
	SourceURL     string
	Priority      int
	Attempt       int
	Reason        string
	EnqueuedAt    time.Time
	NotBefore     time.Time
	Generation    uint64
	SourceVersion uint64
	sequence      int64
	index         int
}
