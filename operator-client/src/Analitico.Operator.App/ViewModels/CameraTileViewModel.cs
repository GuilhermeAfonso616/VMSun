using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Input;
using Avalonia.Threading;
using Avalonia.Media;
using LibVLCSharp.Shared;
using Analitico.Operator.App.Models;
using Analitico.Operator.App.Services;

namespace Analitico.Operator.App.ViewModels;

public sealed class CameraTileViewModel : ObservableObject, IDisposable
{
    private static readonly IBrush OkBrush = new SolidColorBrush(Color.FromRgb(22, 126, 74));
    private static readonly IBrush WarnBrush = new SolidColorBrush(Color.FromRgb(177, 105, 12));
    private static readonly IBrush NeutralBrush = new SolidColorBrush(Color.FromRgb(51, 65, 85));
    private static readonly IBrush BadBrush = new SolidColorBrush(Color.FromRgb(143, 28, 28));
    private static readonly IBrush SelectedBorder = new SolidColorBrush(Color.FromRgb(59, 130, 246));
    private static readonly IBrush DefaultBorder = new SolidColorBrush(Color.FromRgb(48, 56, 70));
    internal static readonly Lazy<LibVLC> SharedLibVlc = new(() => new LibVLC("--no-audio", "--rtsp-tcp", "--network-caching=120", "--clock-jitter=0", "--clock-synchro=0", "--avcodec-hw=any", "--codec=all"));

    static CameraTileViewModel()
    {
        Task.Run(() =>
        {
            try
            {
                _ = SharedLibVlc.Value;
            }
            catch (Exception ex)
            {
                AppLogger.Error("Erro ao inicializar LibVLC em segundo plano", ex);
            }
        });
    }

    private readonly SemaphoreSlim _playerOperationLock = new(1, 1);
    private readonly object _playerOperationSync = new();
    private Media? _media;
    private MediaPlayer? _mediaPlayer;
    private string? _currentPlaybackUrl;
    private bool _isPlaying;
    private bool _isOpening;
    private bool _isSelected;
    private bool _isIaBusy;
    private bool _hasPlaybackError;
    private bool _boxesEnabled = true;
    private double _minimumBoxConfidence = 0.40;
    private string _playbackMessage = "Pronto";
    private double _tileWidth = 300;
    private double _tileHeight = 210;
    private TrackCameraPayload? _lastTrackPayload;
    private string? _lastTrackSignature;
    private int _trackOverlayRevision;
    private DateTimeOffset? _lastPlaybackErrorAt;
    private DateTimeOffset? _lastStartAttemptAt;
    private DateTimeOffset? _lastErrorLogAt;
    private int _consecutiveErrorCount;
    private RelayCommand? _startIaCommand;
    private RelayCommand? _stopIaCommand;
    private CancellationTokenSource? _playerOperationCancellation;
    private TaskCompletionSource<object?> _stoppedSignal = NewStopSignal();
    private bool _disposed;
    private string? _highestOpenSeverity;

    public string? HighestOpenSeverity
    {
        get => _highestOpenSeverity;
        set => SetProperty(ref _highestOpenSeverity, value);
    }

    public CameraTileViewModel(OperatorCamera camera, int slot)
    {
        Camera = camera;
        Slot = slot;
        StartCommand = new RelayCommand(_ => Start());
        StopCommand = new RelayCommand(_ => Stop());
        SelectCommand = new RelayCommand(_ => SelectAction?.Invoke(this));
        PinCommand = new RelayCommand(_ => PinAction?.Invoke(this));
        StartIaCommand = _startIaCommand = new RelayCommand(_ => _ = StartIaAsync(), _ => !IsIaBusy);
        StopIaCommand = _stopIaCommand = new RelayCommand(_ => _ = StopIaAsync(), _ => !IsIaBusy);
    }

    public OperatorCamera Camera { get; private set; }

    public int Slot { get; }

    public string SlotLabel => $"{Slot:00}  {Camera.Name}";

    public string CameraIdText => $"#{Camera.Id}";

    public string PriorityText => string.IsNullOrWhiteSpace(Camera.Priority) ? "medium" : Camera.Priority;

    public string HealthText => string.IsNullOrWhiteSpace(Camera.HealthStatus) ? "sem saude" : Camera.HealthStatus;

    public string SourceText
    {
        get
        {
            if (!string.IsNullOrWhiteSpace(Camera.SourceStreamKind))
            {
                return Camera.SourceStreamKind;
            }

            return Camera.StreamUrlAvailable ? "RTSP" : "sem midia";
        }
    }

    public string? MediaRtspUrl => Camera.MediaRtspUrl;

    public string? RawStreamUrl => Camera.RawStreamUrl;

    public string? MonitorStreamUrl => Camera.MonitorStreamUrl;

    public string? ProcessedStreamUrl => Camera.ProcessedStreamUrl;

    public string? BoxedStreamUrl => Camera.BoxedStreamUrl;

    public string? PlaybackUrl
    {
        get
        {
            return MediaRtspUrl
                ?? ProcessedStreamUrl
                ?? RawStreamUrl
                ?? BoxedStreamUrl;
        }
    }

    public bool UsesBoxedPlayback => false;

    public bool UsesClientOverlayPlayback => BoxesEnabled && IsStreamAvailable;

    public bool ShowTrackOverlay => BoxesEnabled;

    public MediaPlayer? MediaPlayer => _mediaPlayer;

    public Action<CameraTileViewModel>? SelectAction { get; set; }

    public Action<CameraTileViewModel>? PinAction { get; set; }

    public Func<CameraTileViewModel, Task>? StartIaAction { get; set; }

    public Func<CameraTileViewModel, Task>? StopIaAction { get; set; }

    public ICommand StartCommand { get; }

    public ICommand StopCommand { get; }

    public ICommand SelectCommand { get; }

    public ICommand PinCommand { get; }

    public ICommand StartIaCommand { get; }

    public ICommand StopIaCommand { get; }

    public ObservableCollection<TrackBoxViewModel> TrackBoxes { get; } = new();

    public double TileWidth
    {
        get => _tileWidth;
        set
        {
            if (SetProperty(ref _tileWidth, value))
            {
                OnPropertyChanged(nameof(VideoOverlayWidth));
            }
        }
    }

    public double TileHeight
    {
        get => _tileHeight;
        set
        {
            if (SetProperty(ref _tileHeight, value))
            {
                OnPropertyChanged(nameof(VideoOverlayHeight));
            }
        }
    }

    public double VideoOverlayWidth => Math.Max(1, TileWidth - 2);

    public double VideoOverlayHeight => Math.Max(1, TileHeight - 24);

    public int SourceFrameWidth => _lastTrackPayload?.SourceFrameWidth > 0
        ? _lastTrackPayload.SourceFrameWidth
        : 1152;

    public int SourceFrameHeight => _lastTrackPayload?.SourceFrameHeight > 0
        ? _lastTrackPayload.SourceFrameHeight
        : 1920;

    public bool IsStreamAvailable => !string.IsNullOrWhiteSpace(PlaybackUrl);

    public bool IsVisible => IsStreamAvailable && Camera.RegistrationOk;

    public bool IsPlaybackActive => _isPlaying || _isOpening;

    public bool CanAutoStartPlayer
    {
        get
        {
            var now = DateTimeOffset.UtcNow;
            if (_lastStartAttemptAt is not null && now - _lastStartAttemptAt.Value < TimeSpan.FromSeconds(3))
            {
                return false;
            }

            if (_lastPlaybackErrorAt is not null)
            {
                var backoff = _consecutiveErrorCount switch
                {
                    <= 1 => TimeSpan.FromSeconds(2),
                    2 => TimeSpan.FromSeconds(5),
                    _ => TimeSpan.FromSeconds(10),
                };
                if (now - _lastPlaybackErrorAt.Value < backoff)
                {
                    return false;
                }
            }

            return true;
        }
    }

    public bool IsSelected
    {
        get => _isSelected;
        set
        {
            if (SetProperty(ref _isSelected, value))
            {
                OnPropertyChanged(nameof(TileBorderBrush));
            }
        }
    }

    public bool BoxesEnabled
    {
        get => _boxesEnabled;
        set
        {
            if (!SetProperty(ref _boxesEnabled, value))
            {
                return;
            }

            if (value)
            {
                RebuildTrackBoxes();
            }
            else
            {
                TrackBoxes.Clear();
            }

            OnPropertyChanged(nameof(TrackBoxes));
            OnPropertyChanged(nameof(TrackStatusText));
            OnPropertyChanged(nameof(PlaybackUrl));
            OnPropertyChanged(nameof(UsesBoxedPlayback));
            OnPropertyChanged(nameof(UsesClientOverlayPlayback));
            OnPropertyChanged(nameof(ShowTrackOverlay));
            OnPropertyChanged(nameof(IsStreamAvailable));
            OnPropertyChanged(nameof(DetailMedia));
            OnPropertyChanged(nameof(EmptyMessage));
            OnPropertyChanged(nameof(HasPlaybackMessage));
            OnPropertyChanged(nameof(TrackOverlayRevision));
        }
    }

    public double MinimumBoxConfidence
    {
        get => _minimumBoxConfidence;
        set
        {
            var next = Math.Clamp(value, 0.0, 1.0);
            if (Math.Abs(_minimumBoxConfidence - next) < 0.0001)
            {
                return;
            }

            _minimumBoxConfidence = next;
            RebuildTrackBoxes();
        }
    }

    public int TrackOverlayRevision => _trackOverlayRevision;

    public string? VideoAspectRatio
    {
        get
        {
            if (_lastTrackPayload is null
                || _lastTrackPayload.SourceFrameWidth <= 0
                || _lastTrackPayload.SourceFrameHeight <= 0)
            {
                return null;
            }

            return $"{_lastTrackPayload.SourceFrameWidth.ToString(CultureInfo.InvariantCulture)}:{_lastTrackPayload.SourceFrameHeight.ToString(CultureInfo.InvariantCulture)}";
        }
    }

    public bool IsIaBusy
    {
        get => _isIaBusy;
        private set
        {
            void Apply()
            {
                if (!SetProperty(ref _isIaBusy, value)) return;
                _startIaCommand?.RaiseCanExecuteChanged();
                _stopIaCommand?.RaiseCanExecuteChanged();
                OnPropertyChanged(nameof(IaBadgeText));
                OnPropertyChanged(nameof(IaBadgeBrush));
            }

            if (Dispatcher.UIThread.CheckAccess()) Apply();
            else Dispatcher.UIThread.Post(Apply);
        }
    }

    public IBrush TileBorderBrush => IsSelected ? SelectedBorder : DefaultBorder;

    public string StatusBadgeText => _hasPlaybackError ? "P Erro" : _isPlaying ? "P OK" : _isOpening ? "P Abr" : "P Pronto";

    public IBrush StatusBadgeBrush => _hasPlaybackError ? BadBrush : _isPlaying ? OkBrush : NeutralBrush;

    public string ImageBadgeText => Camera.RegistrationOk ? "IMG OK" : "IMG Off";

    public IBrush ImageBadgeBrush => Camera.RegistrationOk ? OkBrush : BadBrush;

    public string IaBadgeText => IsIaBusy ? "IA ..." : Camera.IsRunning ? "IA ON" : "IA OFF";

    public IBrush IaBadgeBrush => IsIaBusy ? WarnBrush : Camera.IsRunning ? OkBrush : BadBrush;

    public IBrush HealthBadgeBrush => IsVisible ? OkBrush : BadBrush;

    public string LibraryLine => $"{CameraIdText}  {Camera.Name}";

    public string DetailTitle => $"{CameraIdText} - {Camera.Name}";

    public string DetailStatus => string.IsNullOrWhiteSpace(Camera.Status) ? "sem status" : Camera.Status;

    public string DetailWorker => Camera.IsRunning ? "IA em movimento" : "IA parada";

    public string DetailMedia
    {
        get
        {
            var playbackUrl = PlaybackUrl;
            if (string.IsNullOrWhiteSpace(playbackUrl))
            {
                return "sem midia publicada";
            }

            return playbackUrl;
        }
    }

    public string LastFrameText => string.IsNullOrWhiteSpace(Camera.LastFrameAt) ? "sem frame recente" : Camera.LastFrameAt;

    public string LastMetricsText => string.IsNullOrWhiteSpace(Camera.LastMetricsAt) ? "sem metricas" : Camera.LastMetricsAt;

    public string GatewayText => string.IsNullOrWhiteSpace(Camera.GatewayState) ? "-" : Camera.GatewayState;

    public string TrackStatusText
    {
        get
        {
            if (_lastTrackPayload is null)
            {
                return "sem tracks";
            }

            if (_lastTrackPayload.Stale)
            {
                return "tracks antigos";
            }

            var ageText = _lastTrackPayload.AgeSeconds is null
                ? "tempo n/d"
                : $"{_lastTrackPayload.AgeSeconds.Value:0.0}s";
            return $"{TrackBoxes.Count} box | {ageText}";
        }
    }

    public string EmptyMessage
    {
        get
        {
            if (!IsStreamAvailable)
            {
                return Camera.RegistrationReason ?? "Stream indisponivel";
            }

            if (_hasPlaybackError)
            {
                return _playbackMessage;
            }

            return _isPlaying ? "" : _playbackMessage;
        }
    }

    public bool HasPlaybackMessage => !string.IsNullOrWhiteSpace(EmptyMessage);

    public string FooterText
    {
        get
        {
            var site = string.IsNullOrWhiteSpace(Camera.SiteName) ? "Sem local" : Camera.SiteName;
            var group = string.IsNullOrWhiteSpace(Camera.GroupName) ? "Sem grupo" : Camera.GroupName;
            return $"{site} - {group}";
        }
    }

    public void Start()
    {
        _ = StartAsync();
    }

    public void ApplyUpdate(OperatorCamera camera)
    {
        var previousUrl = MediaRtspUrl;
        Camera = camera;
        if (!string.Equals(previousUrl, MediaRtspUrl, StringComparison.OrdinalIgnoreCase) && !_isPlaying && !_isOpening)
        {
            Stop();
        }

        NotifyCameraChanged();
    }

    public void ApplyTracks(TrackCameraPayload? payload)
    {
        var incomingGeneration = payload?.GenerationId?.ToString() ?? string.Empty;
        var currentGeneration = _lastTrackPayload?.GenerationId?.ToString() ?? string.Empty;
        if (payload?.FrameId is > 0
            && _lastTrackPayload?.FrameId is > 0
            && string.Equals(incomingGeneration, currentGeneration, StringComparison.Ordinal)
            && payload.FrameId <= _lastTrackPayload.FrameId)
        {
            return;
        }

        var signature = BuildTrackSignature(payload);
        _lastTrackPayload = payload;

        if (payload?.FrameId is > 0 && !payload.Stale && payload.Tracks.Count == 0)
        {
            TrackBoxes.Clear();
            BumpTrackOverlayRevision();
        }

        if (string.Equals(_lastTrackSignature, signature, StringComparison.Ordinal))
        {
            OnPropertyChanged(nameof(TrackStatusText));
            return;
        }

        _lastTrackSignature = signature;
        OnPropertyChanged(nameof(VideoAspectRatio));
        OnPropertyChanged(nameof(SourceFrameWidth));
        OnPropertyChanged(nameof(SourceFrameHeight));
        RebuildTrackBoxes();
    }

    public void PreparePlayer()
    {
        EnsureMediaPlayer();
    }

    public async Task StartAsync(CancellationToken cancellationToken = default, bool force = false)
    {
        var operationCancellation = ReplacePlayerOperationCancellation(cancellationToken);
        var token = operationCancellation.Token;

        await _playerOperationLock.WaitAsync(token).ConfigureAwait(false);
        try
        {
            var playbackUrl = PlaybackUrl;
            if (_disposed || string.IsNullOrWhiteSpace(playbackUrl))
            {
                return;
            }

            AppLogger.Info($"Camera {Camera.Id} - start solicitado. force={force}; url={playbackUrl}");

            if (!force && !CanAutoStartPlayer)
            {
                AppLogger.Warn($"Camera {Camera.Id} - auto-start ignorado por cooldown. URL={playbackUrl}");
                return;
            }

            if (force)
            {
                _lastPlaybackErrorAt = null;
                _lastStartAttemptAt = null;
            }

            _lastStartAttemptAt = DateTimeOffset.UtcNow;
            await StopInternalAsync(token, detachSurface: false).ConfigureAwait(false);

            var player = await Dispatcher.UIThread.InvokeAsync(EnsureMediaPlayer, DispatcherPriority.Background, token);

            await Dispatcher.UIThread.InvokeAsync(() => { }, DispatcherPriority.Background, token);
            await Task.Delay(120, token).ConfigureAwait(false);

            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                _currentPlaybackUrl = playbackUrl;

                _media = new Media(SharedLibVlc.Value, playbackUrl, FromType.FromLocation);
                if (playbackUrl.StartsWith("rtsp", StringComparison.OrdinalIgnoreCase))
                {
                    _media.AddOption(":rtsp-tcp");
                }

                _media.AddOption(":network-caching=80");
                _media.AddOption(":live-caching=80");
                _media.AddOption(":drop-late-frames");
                _media.AddOption(":skip-frames");
                _media.AddOption(":http-reconnect");
                _media.AddOption(":avcodec-hw=none");
                _media.AddOption(":clock-jitter=0");
                _media.AddOption(":clock-synchro=0");

                UpdatePlaybackState(
                    isPlaying: false,
                    isOpening: true,
                    hasError: false,
                    message: "Abrindo");

                var playResult = player.Play(_media);

                if (!playResult)
                {
                    UpdatePlaybackState(
                        isPlaying: false,
                        isOpening: false,
                        hasError: true,
                        message: "Falha ao iniciar player");

                    AppLogger.Warn($"Camera {Camera.Id} - LibVLC retornou false no Play(). URL={playbackUrl}");
                }
                else
                {
                    AppLogger.Info($"Camera {Camera.Id} - start concluido/enviado ao LibVLC. URL={playbackUrl}");
                }
            }, DispatcherPriority.Background, token);
        }
        catch (OperationCanceledException)
        {
            AppLogger.Warn($"Camera {Camera.Id} - start cancelado.");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Camera {Camera.Id} - excecao ao iniciar player.", exc);
            UpdatePlaybackState(
                isPlaying: false,
                isOpening: false,
                hasError: true,
                message: "Erro no player");
        }
        finally
        {
            _playerOperationLock.Release();
        }
    }

    public void Stop()
    {
        _ = StopAsync();
    }

    public async Task StopAsync(CancellationToken cancellationToken = default, bool detachSurface = false)
    {
        var operationCancellation = ReplacePlayerOperationCancellation(cancellationToken);
        var token = operationCancellation.Token;

        await _playerOperationLock.WaitAsync(token).ConfigureAwait(false);
        try
        {
            await StopInternalAsync(token, detachSurface).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            AppLogger.Warn($"Camera {Camera.Id} - stop cancelado.");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Camera {Camera.Id} - excecao ao parar player.", exc);
        }
        finally
        {
            _playerOperationLock.Release();
        }
    }

    public void ResetPlayerSurface()
    {
        _ = StopAsync(detachSurface: true);
    }

    public bool MatchesSearch(string? searchText)
    {
        if (string.IsNullOrWhiteSpace(searchText))
        {
            return true;
        }

        var value = searchText.Trim();
        return Camera.Name.Contains(value, StringComparison.OrdinalIgnoreCase)
            || Camera.Id.ToString().Contains(value, StringComparison.OrdinalIgnoreCase)
            || (Camera.SiteName?.Contains(value, StringComparison.OrdinalIgnoreCase) ?? false)
            || (Camera.GroupName?.Contains(value, StringComparison.OrdinalIgnoreCase) ?? false);
    }

    public bool MatchesFilter(string filter)
    {
        return filter switch
        {
            "visible" => IsVisible,
            "alarm" => string.Equals(Camera.Priority, "critical", StringComparison.OrdinalIgnoreCase)
                || string.Equals(Camera.Priority, "high", StringComparison.OrdinalIgnoreCase),
            "offline" => !IsVisible,
            "running" => Camera.IsRunning,
            _ => true,
        };
    }

    private async Task StartIaAsync()
    {
        if (StartIaAction is null || IsIaBusy)
        {
            return;
        }

        IsIaBusy = true;
        try
        {
            await StartIaAction(this).ConfigureAwait(false);
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Camera {Camera.Id} - excecao em StartIaAsync.", exc);
        }
        finally
        {
            IsIaBusy = false;
        }
    }

    private async Task StopIaAsync()
    {
        if (StopIaAction is null || IsIaBusy)
        {
            return;
        }

        IsIaBusy = true;
        try
        {
            await StopIaAction(this).ConfigureAwait(false);
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Camera {Camera.Id} - excecao em StopIaAsync.", exc);
        }
        finally
        {
            IsIaBusy = false;
        }
    }

    private void UpdatePlaybackState(bool isPlaying, bool isOpening, bool hasError, string message)
    {
        void Apply()
        {
            _isPlaying = isPlaying;
            _isOpening = isOpening;
            _hasPlaybackError = hasError;
            _playbackMessage = message;
            NotifyState();
        }

        if (Dispatcher.UIThread.CheckAccess())
        {
            Apply();
        }
        else
        {
            Dispatcher.UIThread.Post(Apply);
        }
    }

    private void NotifyState()
    {
        OnPropertyChanged(nameof(StatusBadgeText));
        OnPropertyChanged(nameof(StatusBadgeBrush));
        OnPropertyChanged(nameof(HealthBadgeBrush));
        OnPropertyChanged(nameof(IsPlaybackActive));
        OnPropertyChanged(nameof(EmptyMessage));
        OnPropertyChanged(nameof(HasPlaybackMessage));
    }

    private void RebuildTrackBoxes()
    {
        var payload = _lastTrackPayload;
        if (!BoxesEnabled || payload is null || payload.SourceFrameWidth <= 0 || payload.SourceFrameHeight <= 0)
        {
            ClearExpiredTrackBoxes(TimeSpan.FromMilliseconds(payload?.Stale == true ? 1200 : 0));
            OnPropertyChanged(nameof(TrackStatusText));
            return;
        }

        if (payload.Stale)
        {
            ClearExpiredTrackBoxes(TimeSpan.FromMilliseconds(1200));
            OnPropertyChanged(nameof(TrackStatusText));
            return;
        }

        var existing = TrackBoxes.ToDictionary(box => box.Key, StringComparer.Ordinal);
        var activeKeys = new HashSet<string>(StringComparer.Ordinal);
        var index = 0;

        foreach (var track in payload.Tracks.Take(30))
        {
            if (track.Bbox.Count != 4)
            {
                continue;
            }

            if ((track.Confidence ?? 0.0) < MinimumBoxConfidence)
            {
                continue;
            }

            var x1 = Math.Clamp(track.Bbox[0], 0, payload.SourceFrameWidth);
            var y1 = Math.Clamp(track.Bbox[1], 0, payload.SourceFrameHeight);
            var x2 = Math.Clamp(track.Bbox[2], 0, payload.SourceFrameWidth);
            var y2 = Math.Clamp(track.Bbox[3], 0, payload.SourceFrameHeight);
            var width = Math.Max(1, x2 - x1);
            var height = Math.Max(1, y2 - y1);
            var key = BuildTrackKey(track, index++);
            activeKeys.Add(key);

            if (existing.TryGetValue(key, out var box))
            {
                box.Update(track, x1, y1, width, height);
            }
            else
            {
                TrackBoxes.Add(new TrackBoxViewModel(key, track, x1, y1, width, height));
            }
        }

        for (var i = TrackBoxes.Count - 1; i >= 0; i--)
        {
            if (!activeKeys.Contains(TrackBoxes[i].Key)
                && DateTimeOffset.UtcNow - TrackBoxes[i].LastSeenUtc > TimeSpan.FromMilliseconds(1200))
            {
                TrackBoxes.RemoveAt(i);
            }
        }

        OnPropertyChanged(nameof(TrackStatusText));
        BumpTrackOverlayRevision();
    }

    private void ClearExpiredTrackBoxes(TimeSpan maxAge)
    {
        if (maxAge <= TimeSpan.Zero)
        {
            TrackBoxes.Clear();
            return;
        }

        var now = DateTimeOffset.UtcNow;
        for (var i = TrackBoxes.Count - 1; i >= 0; i--)
        {
            if (now - TrackBoxes[i].LastSeenUtc > maxAge)
            {
                TrackBoxes.RemoveAt(i);
            }
        }

        BumpTrackOverlayRevision();
    }

    private static string BuildTrackKey(TrackBoxPayload track, int index)
    {
        if (track.TrackId is > 0)
        {
            return $"track:{track.TrackId.Value.ToString(CultureInfo.InvariantCulture)}";
        }

        var label = string.IsNullOrWhiteSpace(track.Label) ? "person" : track.Label.Trim();
        return $"det:{label}:{index.ToString(CultureInfo.InvariantCulture)}";
    }

    private void BumpTrackOverlayRevision()
    {
        _trackOverlayRevision++;
        OnPropertyChanged(nameof(TrackOverlayRevision));
    }

    private static string BuildTrackSignature(TrackCameraPayload? payload)
    {
        if (payload is null)
        {
            return "null";
        }

        var builder = new StringBuilder(256);
        builder.Append(payload.SourceFrameWidth.ToString(CultureInfo.InvariantCulture));
        builder.Append('x');
        builder.Append(payload.SourceFrameHeight.ToString(CultureInfo.InvariantCulture));
        builder.Append('|');
        builder.Append(payload.Stale ? '1' : '0');
        builder.Append('|');
        builder.Append(payload.Tracks.Count.ToString(CultureInfo.InvariantCulture));

        foreach (var track in payload.Tracks.Take(30))
        {
            builder.Append('|');
            builder.Append(track.TrackId?.ToString(CultureInfo.InvariantCulture) ?? "-");
            builder.Append(':');
            builder.Append(string.IsNullOrWhiteSpace(track.Label) ? "person" : track.Label.Trim());
            builder.Append(':');
            builder.Append(track.Confidence?.ToString("0.00", CultureInfo.InvariantCulture) ?? "-");
            builder.Append(':');

            for (var i = 0; i < Math.Min(4, track.Bbox.Count); i++)
            {
                if (i > 0)
                {
                    builder.Append(',');
                }

                builder.Append(Math.Round(track.Bbox[i], 1).ToString("0.0", CultureInfo.InvariantCulture));
            }
        }

        return builder.ToString();
    }

    private void NotifyCameraChanged()
    {
        OnPropertyChanged(nameof(Camera));
        OnPropertyChanged(nameof(SlotLabel));
        OnPropertyChanged(nameof(CameraIdText));
        OnPropertyChanged(nameof(PriorityText));
        OnPropertyChanged(nameof(HealthText));
        OnPropertyChanged(nameof(SourceText));
        OnPropertyChanged(nameof(MediaRtspUrl));
        OnPropertyChanged(nameof(RawStreamUrl));
        OnPropertyChanged(nameof(MonitorStreamUrl));
        OnPropertyChanged(nameof(ProcessedStreamUrl));
        OnPropertyChanged(nameof(BoxedStreamUrl));
        OnPropertyChanged(nameof(PlaybackUrl));
        OnPropertyChanged(nameof(UsesBoxedPlayback));
        OnPropertyChanged(nameof(UsesClientOverlayPlayback));
        OnPropertyChanged(nameof(ShowTrackOverlay));
        OnPropertyChanged(nameof(IsStreamAvailable));
        OnPropertyChanged(nameof(IsVisible));
        OnPropertyChanged(nameof(ImageBadgeText));
        OnPropertyChanged(nameof(ImageBadgeBrush));
        OnPropertyChanged(nameof(IaBadgeText));
        OnPropertyChanged(nameof(IaBadgeBrush));
        OnPropertyChanged(nameof(HealthBadgeBrush));
        OnPropertyChanged(nameof(LibraryLine));
        OnPropertyChanged(nameof(DetailTitle));
        OnPropertyChanged(nameof(DetailStatus));
        OnPropertyChanged(nameof(DetailWorker));
        OnPropertyChanged(nameof(DetailMedia));
        OnPropertyChanged(nameof(LastFrameText));
        OnPropertyChanged(nameof(LastMetricsText));
        OnPropertyChanged(nameof(GatewayText));
        OnPropertyChanged(nameof(TrackStatusText));
        OnPropertyChanged(nameof(SourceFrameWidth));
        OnPropertyChanged(nameof(SourceFrameHeight));
        OnPropertyChanged(nameof(EmptyMessage));
        OnPropertyChanged(nameof(HasPlaybackMessage));
        OnPropertyChanged(nameof(FooterText));
    }

    private MediaPlayer EnsureMediaPlayer()
    {
        if (_mediaPlayer is not null)
        {
            return _mediaPlayer;
        }

        _mediaPlayer = new MediaPlayer(SharedLibVlc.Value);
        _mediaPlayer.Opening += HandleOpening;
        _mediaPlayer.Playing += HandlePlaying;
        _mediaPlayer.Stopped += HandleStopped;
        _mediaPlayer.EncounteredError += HandleEncounteredError;
        OnPropertyChanged(nameof(MediaPlayer));
        return _mediaPlayer;
    }

    private async Task StopInternalAsync(CancellationToken token, bool detachSurface)
    {
        if (_mediaPlayer is null && _media is null)
        {
            return;
        }

        var url = _currentPlaybackUrl ?? PlaybackUrl ?? "-";
        AppLogger.Info($"Camera {Camera.Id} - stop solicitado. detachSurface={detachSurface}; url={url}");

        Task stoppedTask = Task.CompletedTask;
        MediaPlayer? playerToStop = null;
        await Dispatcher.UIThread.InvokeAsync(() =>
        {
            var shouldStopPlayer = _mediaPlayer is not null && (_mediaPlayer.IsPlaying || _isOpening || _isPlaying);
            if (shouldStopPlayer)
            {
                _stoppedSignal = NewStopSignal();
                stoppedTask = _stoppedSignal.Task;
                playerToStop = _mediaPlayer;
            }
        }, DispatcherPriority.Background, token);

        if (playerToStop is not null)
        {
            _ = Task.Run(() =>
            {
                try
                {
                    playerToStop.Stop();
                }
                catch (Exception ex)
                {
                    AppLogger.Warn($"Camera {Camera.Id} - excecao ao executar Stop() em background: {ex.Message}");
                }
            });
        }

        if (!ReferenceEquals(stoppedTask, Task.CompletedTask))
        {
            var completed = await Task.WhenAny(stoppedTask, Task.Delay(900, token)).ConfigureAwait(false);
            if (completed != stoppedTask)
            {
                AppLogger.Warn($"Camera {Camera.Id} - timeout aguardando evento Stopped.");
            }
        }

        MediaPlayer? detachedPlayer = null;
        await Dispatcher.UIThread.InvokeAsync(() =>
        {
            _media?.Dispose();
            _media = null;
            _currentPlaybackUrl = null;

            UpdatePlaybackState(
                isPlaying: false,
                isOpening: false,
                hasError: false,
                message: "Pronto");

            if (detachSurface && _mediaPlayer is not null)
            {
                AppLogger.Info($"Camera {Camera.Id} - surface detach iniciado.");
                detachedPlayer = _mediaPlayer;
                detachedPlayer.Opening -= HandleOpening;
                detachedPlayer.Playing -= HandlePlaying;
                detachedPlayer.Stopped -= HandleStopped;
                detachedPlayer.EncounteredError -= HandleEncounteredError;
                _mediaPlayer = null;
                OnPropertyChanged(nameof(MediaPlayer));
                AppLogger.Info($"Camera {Camera.Id} - surface detach desbindado.");
            }
        }, DispatcherPriority.Background, token);

        if (detachedPlayer is not null)
        {
            await Dispatcher.UIThread.InvokeAsync(() => { }, DispatcherPriority.Render, token);
            await Task.Delay(220, token).ConfigureAwait(false);
            try
            {
                detachedPlayer.Dispose();
                AppLogger.Info($"Camera {Camera.Id} - surface detach concluido.");
            }
            catch (Exception exc)
            {
                AppLogger.Error($"Camera {Camera.Id} - excecao ao descartar player desanexado.", exc);
            }
        }

        AppLogger.Info($"Camera {Camera.Id} - stop concluido.");
    }

    private CancellationTokenSource ReplacePlayerOperationCancellation(CancellationToken cancellationToken)
    {
        lock (_playerOperationSync)
        {
            _playerOperationCancellation?.Cancel();
            _playerOperationCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            return _playerOperationCancellation;
        }
    }

    private static TaskCompletionSource<object?> NewStopSignal()
    {
        return new TaskCompletionSource<object?>(TaskCreationOptions.RunContinuationsAsynchronously);
    }

    private void HandleOpening(object? sender, EventArgs args)
    {
        try
        {
            AppLogger.Info($"Camera {Camera.Id} - evento LibVLC Opening.");

            UpdatePlaybackState(
                isPlaying: false,
                isOpening: true,
                hasError: false,
                message: "Abrindo");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Camera {Camera.Id} - excecao no callback Opening.", exc);
        }
    }

    private void HandlePlaying(object? sender, EventArgs args)
    {
        try
        {
            AppLogger.Info($"Camera {Camera.Id} - evento LibVLC Playing.");
            _lastPlaybackErrorAt = null;
            _consecutiveErrorCount = 0;

            UpdatePlaybackState(
                isPlaying: true,
                isOpening: false,
                hasError: false,
                message: "Player OK");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Camera {Camera.Id} - excecao no callback Playing.", exc);
        }
    }

    private void HandleStopped(object? sender, EventArgs args)
    {
        try
        {
            AppLogger.Info($"Camera {Camera.Id} - evento LibVLC Stopped.");
            _stoppedSignal.TrySetResult(null);

            UpdatePlaybackState(
                isPlaying: false,
                isOpening: false,
                hasError: false,
                message: "Pronto");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Camera {Camera.Id} - excecao no callback Stopped.", exc);
        }
    }

    private void HandleEncounteredError(object? sender, EventArgs args)
    {
        try
        {
            _consecutiveErrorCount++;
            _lastPlaybackErrorAt = DateTimeOffset.UtcNow;
            var now = _lastPlaybackErrorAt.Value;
            if (_lastErrorLogAt is null || now - _lastErrorLogAt.Value > TimeSpan.FromSeconds(5))
            {
                _lastErrorLogAt = now;
                AppLogger.Warn($"Camera {Camera.Id} - LibVLC EncounteredError (tentativa {_consecutiveErrorCount}). URL={_currentPlaybackUrl ?? PlaybackUrl ?? "-"}");
            }

            UpdatePlaybackState(
                isPlaying: false,
                isOpening: false,
                hasError: true,
                message: $"Erro no player (tentativa {_consecutiveErrorCount}). Reconectando...");

            // Background reconnect retry after 5 seconds
            _ = Task.Run(async () =>
            {
                await Task.Delay(5000).ConfigureAwait(false);
                if (!_disposed && _currentPlaybackUrl != null)
                {
                    AppLogger.Info($"Camera {Camera.Id} - Tentando reconectar automaticamente. URL={_currentPlaybackUrl}");
                    await StartAsync(CancellationToken.None, force: true).ConfigureAwait(false);
                }
            });
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Camera {Camera.Id} - excecao no callback EncounteredError.", exc);
        }
    }

    public void Dispose()
    {
        _disposed = true;
        _playerOperationCancellation?.Cancel();
        ResetPlayerSurface();
    }
}
