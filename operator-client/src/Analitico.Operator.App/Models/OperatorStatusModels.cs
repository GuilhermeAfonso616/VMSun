using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Analitico.Operator.App.Models;

public sealed class DashboardMetricsResponse
{
    [JsonPropertyName("generated_at")]
    public string? GeneratedAt { get; set; }

    [JsonPropertyName("camera_total")]
    public int CameraTotal { get; set; }

    [JsonPropertyName("running_cameras")]
    public int RunningCameras { get; set; }

    [JsonPropertyName("camera_health")]
    public CameraHealthSummary CameraHealth { get; set; } = new();

    [JsonPropertyName("worker_count")]
    public int WorkerCount { get; set; }

    [JsonPropertyName("worker_cpu_total_percent")]
    public double WorkerCpuTotalPercent { get; set; }

    [JsonPropertyName("worker_rss_total_mb")]
    public double WorkerRssTotalMb { get; set; }

    [JsonPropertyName("worker_fps_total")]
    public double WorkerFpsTotal { get; set; }

    [JsonPropertyName("worker_processed_fps_total")]
    public double WorkerProcessedFpsTotal { get; set; }

    [JsonPropertyName("host_cpu_percent")]
    public double HostCpuPercent { get; set; }

    [JsonPropertyName("host_ram_percent")]
    public double HostRamPercent { get; set; }

    [JsonPropertyName("gpu")]
    public GpuStatus Gpu { get; set; } = new();
}

public sealed class CameraHealthSummary
{
    [JsonPropertyName("running")]
    public int Running { get; set; }

    [JsonPropertyName("degraded")]
    public int Degraded { get; set; }

    [JsonPropertyName("reconnecting")]
    public int Reconnecting { get; set; }

    [JsonPropertyName("offline")]
    public int Offline { get; set; }

    [JsonPropertyName("stopped")]
    public int Stopped { get; set; }
}

public sealed class GpuStatus
{
    [JsonPropertyName("available")]
    public bool Available { get; set; }

    [JsonPropertyName("name")]
    public string? Name { get; set; }

    [JsonPropertyName("utilization_percent")]
    public double? UtilizationPercent { get; set; }

    [JsonPropertyName("memory_used_mb")]
    public double? MemoryUsedMb { get; set; }

    [JsonPropertyName("memory_total_mb")]
    public double? MemoryTotalMb { get; set; }

    [JsonPropertyName("temperature_c")]
    public double? TemperatureC { get; set; }
}

public sealed class OneDriveStatusResponse
{
    [JsonPropertyName("enabled")]
    public bool Enabled { get; set; }

    [JsonPropertyName("archive_enabled")]
    public bool ArchiveEnabled { get; set; }

    [JsonPropertyName("client_id_configured")]
    public bool ClientIdConfigured { get; set; }

    [JsonPropertyName("token_exists")]
    public bool TokenExists { get; set; }

    [JsonPropertyName("has_refresh_token")]
    public bool HasRefreshToken { get; set; }

    [JsonPropertyName("refresh_enabled")]
    public bool RefreshEnabled { get; set; }

    [JsonPropertyName("expires_at")]
    public string? ExpiresAt { get; set; }

    [JsonPropertyName("token_error")]
    public string? TokenError { get; set; }

    [JsonPropertyName("refresh_error")]
    public string? RefreshError { get; set; }
}

public sealed class OneDriveReviewedEventsUploadResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("onedrive")]
    public OneDriveReviewedEventsUploadResult OneDrive { get; set; } = new();
}

public sealed class OneDriveReviewedEventsUploadResult
{
    [JsonPropertyName("reviewed_pending")]
    public int ReviewedPending { get; set; }

    [JsonPropertyName("events_processed")]
    public int EventsProcessed { get; set; }

    [JsonPropertyName("event_json_uploaded")]
    public int EventJsonUploaded { get; set; }

    [JsonPropertyName("snapshot_uploaded")]
    public int SnapshotUploaded { get; set; }

    [JsonPropertyName("clip_uploaded")]
    public int ClipUploaded { get; set; }

    [JsonPropertyName("missing_snapshot")]
    public int MissingSnapshot { get; set; }

    [JsonPropertyName("missing_clip")]
    public int MissingClip { get; set; }

    [JsonPropertyName("failed")]
    public int Failed { get; set; }

    [JsonPropertyName("reviewed_pending_after")]
    public int ReviewedPendingAfter { get; set; }
}

public sealed class DashboardEventsResponse
{
    [JsonPropertyName("open_events_count")]
    public int OpenEventsCount { get; set; }

    [JsonPropertyName("recent_events")]
    public List<OperatorEvent> RecentEvents { get; set; } = new();

    [JsonPropertyName("open_events")]
    public List<OperatorEvent> OpenEvents { get; set; } = new();

    [JsonPropertyName("latest_alarm_signature")]
    public string? LatestAlarmSignature { get; set; }

    [JsonPropertyName("alarm_should_play")]
    public bool AlarmShouldPlay { get; set; }
}

public sealed class OperatorEvent
{
    [JsonPropertyName("id")]
    public int Id { get; set; }

    [JsonPropertyName("camera_id")]
    public int CameraId { get; set; }

    [JsonPropertyName("camera_name")]
    public string? CameraName { get; set; }

    [JsonPropertyName("event_type")]
    public string? EventType { get; set; }

    [JsonPropertyName("event_type_label")]
    public string? EventTypeLabel { get; set; }

    [JsonPropertyName("severity")]
    public string? Severity { get; set; }

    [JsonPropertyName("severity_label")]
    public string? SeverityLabel { get; set; }

    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("status_label")]
    public string? StatusLabel { get; set; }

    [JsonPropertyName("confidence")]
    public double? Confidence { get; set; }

    [JsonPropertyName("snapshot_url")]
    public string? SnapshotUrl { get; set; }

    [JsonPropertyName("clip_url")]
    public string? ClipUrl { get; set; }

    [JsonPropertyName("created_at_label")]
    public string? CreatedAtLabel { get; set; }

    [JsonPropertyName("created_at")]
    public string? CreatedAt { get; set; }

    [JsonPropertyName("is_alarm_active")]
    public bool IsAlarmActive { get; set; }

    [JsonPropertyName("can_ack")]
    public bool CanAck { get; set; }

    [JsonPropertyName("can_close")]
    public bool CanClose { get; set; }
}

public sealed class HealthCamerasResponse
{
    [JsonPropertyName("generated_at")]
    public string? GeneratedAt { get; set; }

    [JsonPropertyName("camera_count")]
    public int CameraCount { get; set; }

    [JsonPropertyName("running_count")]
    public int RunningCount { get; set; }

    [JsonPropertyName("degraded_count")]
    public int DegradedCount { get; set; }

    [JsonPropertyName("warming_up_count")]
    public int WarmingUpCount { get; set; }

    [JsonPropertyName("reconnecting_count")]
    public int ReconnectingCount { get; set; }

    [JsonPropertyName("offline_count")]
    public int OfflineCount { get; set; }

    [JsonPropertyName("stopped_count")]
    public int StoppedCount { get; set; }

    [JsonPropertyName("cameras")]
    public List<CameraHealthDetail> Cameras { get; set; } = new();
}

public sealed class CameraHealthDetail
{
    [JsonPropertyName("camera_id")]
    public int CameraId { get; set; }

    [JsonPropertyName("camera_name")]
    public string? CameraName { get; set; }

    [JsonPropertyName("camera_status")]
    public string? CameraStatus { get; set; }

    [JsonPropertyName("worker_pid")]
    public int? WorkerPid { get; set; }

    [JsonPropertyName("worker_mode")]
    public string? WorkerMode { get; set; }

    [JsonPropertyName("health_status")]
    public string? HealthStatus { get; set; }

    [JsonPropertyName("is_running")]
    public bool IsRunning { get; set; }

    [JsonPropertyName("restart_count")]
    public int RestartCount { get; set; }

    [JsonPropertyName("last_restart_reason")]
    public string? LastRestartReason { get; set; }

    [JsonPropertyName("last_status_reason")]
    public string? LastStatusReason { get; set; }

    [JsonPropertyName("last_frame_at")]
    public string? LastFrameAt { get; set; }

    [JsonPropertyName("last_metrics_at")]
    public string? LastMetricsAt { get; set; }

    [JsonPropertyName("gateway_state")]
    public string? GatewayState { get; set; }

    [JsonPropertyName("metrics_age_seconds")]
    public double? MetricsAgeSeconds { get; set; }
}

public sealed class MonitorTracksResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("generated_at")]
    public string? GeneratedAt { get; set; }

    [JsonPropertyName("cameras")]
    public Dictionary<string, TrackCameraPayload> Cameras { get; set; } = new();
}

public sealed class TrackCameraPayload
{
    [JsonPropertyName("camera_id")]
    public int CameraId { get; set; }

    [JsonPropertyName("source_frame_width")]
    public int SourceFrameWidth { get; set; }

    [JsonPropertyName("source_frame_height")]
    public int SourceFrameHeight { get; set; }

    [JsonPropertyName("tracks")]
    public List<TrackBoxPayload> Tracks { get; set; } = new();

    [JsonPropertyName("age_seconds")]
    public double? AgeSeconds { get; set; }

    [JsonPropertyName("stale")]
    public bool Stale { get; set; }

    [JsonPropertyName("frame_id")]
    public long? FrameId { get; set; }

    [JsonPropertyName("generation_id")]
    public JsonElement? GenerationId { get; set; }

    [JsonPropertyName("tracks_published_at_ns")]
    public long? TracksPublishedAtNs { get; set; }

    [JsonIgnore]
    public long? ClientReceivedAtNs { get; set; }

    [JsonIgnore]
    public double? BackendToClientMs { get; set; }
}

public sealed class TrackBoxPayload
{
    [JsonPropertyName("bbox")]
    public List<double> Bbox { get; set; } = new();

    [JsonPropertyName("track_id")]
    public int? TrackId { get; set; }

    [JsonPropertyName("confidence")]
    public double? Confidence { get; set; }

    [JsonPropertyName("label")]
    public string? Label { get; set; }

    [JsonPropertyName("visual_status")]
    public string? VisualStatus { get; set; }
}
