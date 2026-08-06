using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace Analitico.Operator.App.Models;

public sealed class OperatorBootstrapResponse
{
    [JsonPropertyName("server_version")]
    public string? ServerVersion { get; set; }

    [JsonPropertyName("server_release_tag")]
    public string? ServerReleaseTag { get; set; }

    [JsonPropertyName("server_commit")]
    public string? ServerCommit { get; set; }

    [JsonPropertyName("operator_api_version")]
    public int? OperatorApiVersion { get; set; }

    [JsonPropertyName("recommended_operator_client_version")]
    public string? RecommendedOperatorClientVersion { get; set; }

    [JsonPropertyName("server_time_utc")]
    public string? ServerTimeUtc { get; set; }

    [JsonPropertyName("rtsp_public_base_url")]
    public string? RtspPublicBaseUrl { get; set; }

    [JsonPropertyName("camera_count")]
    public int CameraCount { get; set; }

    [JsonPropertyName("cameras")]
    public List<OperatorCamera> Cameras { get; set; } = new();
}

public sealed class OperatorCamera
{
    [JsonPropertyName("id")]
    public int Id { get; set; }

    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("site_name")]
    public string? SiteName { get; set; }

    [JsonPropertyName("group_name")]
    public string? GroupName { get; set; }

    [JsonPropertyName("priority")]
    public string? Priority { get; set; }

    [JsonPropertyName("status")]
    public string? Status { get; set; }

    [JsonPropertyName("source_channel")]
    public int? SourceChannel { get; set; }

    [JsonPropertyName("source_type")]
    public string? SourceType { get; set; }

    [JsonPropertyName("source_stream_kind")]
    public string? SourceStreamKind { get; set; }

    [JsonPropertyName("webrtc_path")]
    public string? WebRtcPath { get; set; }

    [JsonPropertyName("webrtc_player_url")]
    public string? WebRtcPlayerUrl { get; set; }

    [JsonPropertyName("media_rtsp_url")]
    public string? MediaRtspUrl { get; set; }

    [JsonPropertyName("processed_stream_url")]
    public string? ProcessedStreamUrl { get; set; }

    [JsonPropertyName("boxed_stream_url")]
    public string? BoxedStreamUrl { get; set; }

    [JsonPropertyName("raw_stream_url")]
    public string? RawStreamUrl { get; set; }

    [JsonPropertyName("monitor_stream_url")]
    public string? MonitorStreamUrl { get; set; }

    [JsonPropertyName("stream_url_available")]
    public bool StreamUrlAvailable { get; set; }

    [JsonPropertyName("registration_ok")]
    public bool RegistrationOk { get; set; }

    [JsonPropertyName("registration_reason")]
    public string? RegistrationReason { get; set; }

    [JsonPropertyName("health_status")]
    public string? HealthStatus { get; set; }

    [JsonPropertyName("is_running")]
    public bool IsRunning { get; set; }

    [JsonPropertyName("last_frame_at")]
    public string? LastFrameAt { get; set; }

    [JsonPropertyName("last_metrics_at")]
    public string? LastMetricsAt { get; set; }

    [JsonPropertyName("gateway_state")]
    public string? GatewayState { get; set; }
}
