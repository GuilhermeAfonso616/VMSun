using Avalonia.Media;
using Analitico.Operator.App.Models;

namespace Analitico.Operator.App.ViewModels;

public sealed class CameraHealthViewModel
{
    private static readonly IBrush OkBrush = new SolidColorBrush(Color.FromRgb(22, 126, 74));
    private static readonly IBrush WarnBrush = new SolidColorBrush(Color.FromRgb(177, 105, 12));
    private static readonly IBrush BadBrush = new SolidColorBrush(Color.FromRgb(143, 28, 28));
    private static readonly IBrush NeutralBrush = new SolidColorBrush(Color.FromRgb(51, 65, 85));

    public CameraHealthViewModel(CameraHealthDetail source)
    {
        Source = source;
    }

    public CameraHealthDetail Source { get; }

    public string IdText => $"#{Source.CameraId}";

    public string CameraName => string.IsNullOrWhiteSpace(Source.CameraName) ? $"Camera {Source.CameraId}" : Source.CameraName!;

    public string HealthText => string.IsNullOrWhiteSpace(Source.HealthStatus) ? Source.CameraStatus ?? "sem status" : Source.HealthStatus!;

    public string WorkerText => Source.IsRunning
        ? $"PID {Source.WorkerPid?.ToString() ?? "-"} | {Source.WorkerMode ?? "worker"}"
        : "sem worker";

    public string MetricsAgeText => Source.MetricsAgeSeconds is null ? "sem metricas" : $"{Source.MetricsAgeSeconds.Value:0.0}s";

    public string RestartText => Source.RestartCount <= 0 ? "sem restart" : $"{Source.RestartCount} restart";

    public string ReasonText => Source.LastStatusReason ?? Source.LastRestartReason ?? "-";

    public IBrush HealthBrush
    {
        get
        {
            var status = (Source.HealthStatus ?? Source.CameraStatus ?? "").Trim().ToLowerInvariant();
            return status switch
            {
                "running" or "healthy" => OkBrush,
                "degraded" or "warming_up" or "reconnecting" => WarnBrush,
                "offline" or "stopped" or "error" => BadBrush,
                _ => NeutralBrush,
            };
        }
    }
}
