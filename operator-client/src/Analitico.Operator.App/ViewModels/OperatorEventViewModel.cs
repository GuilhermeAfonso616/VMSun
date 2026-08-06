using System;
using System.Threading.Tasks;
using System.Windows.Input;
using Avalonia.Media;
using Avalonia.Threading;
using Analitico.Operator.App.Models;

namespace Analitico.Operator.App.ViewModels;

public sealed class OperatorEventViewModel : ObservableObject
{
    private static readonly IBrush CriticalBrush = new SolidColorBrush(Color.FromRgb(163, 36, 36));
    private static readonly IBrush HighBrush = new SolidColorBrush(Color.FromRgb(177, 82, 19));
    private static readonly IBrush MediumBrush = new SolidColorBrush(Color.FromRgb(177, 105, 12));
    private static readonly IBrush NeutralBrush = new SolidColorBrush(Color.FromRgb(51, 65, 85));
    private bool _isBusy;

    public OperatorEventViewModel(OperatorEvent source)
    {
        Source = source;
        OpenCommand = new RelayCommand(_ => EvidenceAction?.Invoke(this, "snapshot"), _ => HasSnapshot);
        SnapshotCommand = new RelayCommand(_ => EvidenceAction?.Invoke(this, "snapshot"), _ => HasSnapshot);
        ClipCommand = new RelayCommand(_ => EvidenceAction?.Invoke(this, "clip"), _ => HasClip);
        AcknowledgeCommand = new RelayCommand(_ => _ = AcknowledgeAsync(), _ => !IsBusy && Source.CanAck);
        CloseCommand = new RelayCommand(_ => _ = CloseAsync(), _ => !IsBusy && Source.CanClose);
    }

    public OperatorEvent Source { get; }

    public Action<OperatorEventViewModel>? OpenAction { get; set; }

    public Action<OperatorEventViewModel, string>? EvidenceAction { get; set; }

    public Func<OperatorEventViewModel, Task>? AcknowledgeAction { get; set; }

    public Func<OperatorEventViewModel, Task>? CloseAction { get; set; }

    public ICommand OpenCommand { get; }

    public ICommand SnapshotCommand { get; }

    public ICommand ClipCommand { get; }

    public ICommand AcknowledgeCommand { get; }

    public ICommand CloseCommand { get; }

    public int Id => Source.Id;

    public int CameraId => Source.CameraId;

    public string CameraName => string.IsNullOrWhiteSpace(Source.CameraName) ? $"Camera {CameraId}" : Source.CameraName!;

    public string EventLabel => string.IsNullOrWhiteSpace(Source.EventTypeLabel) ? Source.EventType ?? "Evento" : Source.EventTypeLabel!;

    public string TimeLabel => Source.CreatedAtLabel ?? Source.CreatedAt ?? "-";

    public string SeverityLabel => Source.SeverityLabel ?? Source.Severity ?? "normal";

    public string StatusLabel => Source.StatusLabel ?? Source.Status ?? "-";

    public string ConfidenceLabel => Source.Confidence is null ? "-" : $"{Source.Confidence.Value:P0}";

    public bool HasSnapshot => !string.IsNullOrWhiteSpace(Source.SnapshotUrl);

    public bool HasClip => !string.IsNullOrWhiteSpace(Source.ClipUrl);

    public bool IsBusy
    {
        get => _isBusy;
        private set
        {
            void Apply()
            {
                if (!SetProperty(ref _isBusy, value)) return;
                if (AcknowledgeCommand is RelayCommand ack)
                {
                    ack.RaiseCanExecuteChanged();
                }

                if (CloseCommand is RelayCommand close)
                {
                    close.RaiseCanExecuteChanged();
                }
            }

            if (Dispatcher.UIThread.CheckAccess()) Apply();
            else Dispatcher.UIThread.Post(Apply);
        }
    }

    public IBrush SeverityBrush
    {
        get
        {
            var severity = (Source.Severity ?? "").Trim().ToLowerInvariant();
            return severity switch
            {
                "critical" => CriticalBrush,
                "high" => HighBrush,
                "medium" => MediumBrush,
                _ => NeutralBrush,
            };
        }
    }

    private async Task AcknowledgeAsync()
    {
        if (AcknowledgeAction is null || IsBusy)
        {
            return;
        }

        IsBusy = true;
        try
        {
            await AcknowledgeAction(this).ConfigureAwait(false);
        }
        finally
        {
            IsBusy = false;
        }
    }

    private async Task CloseAsync()
    {
        if (CloseAction is null || IsBusy)
        {
            return;
        }

        IsBusy = true;
        try
        {
            await CloseAction(this).ConfigureAwait(false);
        }
        finally
        {
            IsBusy = false;
        }
    }
}
