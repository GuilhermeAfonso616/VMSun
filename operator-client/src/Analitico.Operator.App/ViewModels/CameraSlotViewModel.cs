using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Input;
using Avalonia.Media;
using Avalonia.Threading;
using LibVLCSharp.Shared;
using Analitico.Operator.App.Services;

namespace Analitico.Operator.App.ViewModels;

public sealed class CameraSlotViewModel : ObservableObject, IDisposable
{
    private static readonly IBrush SelectedBrush = new SolidColorBrush(Color.FromRgb(59, 130, 246));
    private static readonly IBrush FilledBrush = new SolidColorBrush(Color.FromRgb(48, 56, 70));
    private static readonly IBrush EmptyBrush = new SolidColorBrush(Color.FromRgb(30, 58, 95));
    private static readonly IBrush OkBrush = new SolidColorBrush(Color.FromRgb(22, 126, 74));
    private static readonly IBrush NeutralBrush = new SolidColorBrush(Color.FromRgb(51, 65, 85));
    private static readonly IBrush BadBrush = new SolidColorBrush(Color.FromRgb(143, 28, 28));
    private static readonly IBrush CriticalAlarmBrush = new SolidColorBrush(Color.FromRgb(220, 38, 38)); // SoRed
    private static readonly IBrush HighAlarmBrush = new SolidColorBrush(Color.FromRgb(216, 149, 0)); // SoAmber
    private static readonly Lazy<LibVLC> SharedLibVlc = new(() => new LibVLC("--no-audio", "--rtsp-tcp", "--network-caching=120", "--clock-jitter=0", "--clock-synchro=0"));

    static CameraSlotViewModel()
    {
        Task.Run(() =>
        {
            try
            {
                _ = SharedLibVlc.Value;
            }
            catch (Exception ex)
            {
                AppLogger.Error("Erro ao inicializar LibVLC em segundo plano no slot", ex);
            }
        });
    }

    private readonly SemaphoreSlim _playerOperationLock = new(1, 1);
    private readonly object _playerOperationSync = new();
    private CameraTileViewModel? _camera;
    private bool _isSelected;
    private double _tileWidth = 300;
    private double _tileHeight = 210;
    private Media? _media;
    private MediaPlayer? _mediaPlayer;
    private string? _currentPlaybackUrl;
    private int? _currentCameraId;
    private bool _isPlaying;
    private bool _isOpening;
    private bool _hasPlaybackError;
    private bool _videoSuppressed;
    private string _playbackMessage = "Pronto";
    private DateTimeOffset? _lastPlaybackErrorAt;
    private DateTimeOffset? _lastStartAttemptAt;
    private DateTimeOffset? _lastErrorLogAt;
    private int _consecutiveErrorCount;
    private CancellationTokenSource? _playerOperationCancellation;
    private TaskCompletionSource<object?> _stoppedSignal = NewStopSignal();
    private bool _disposed;
    private bool _isModalActive;

    public bool IsModalActive
    {
        get => _isModalActive;
        set
        {
            if (SetProperty(ref _isModalActive, value))
            {
                OnPropertyChanged(nameof(UseNativePlayback));
            }
        }
    }

    public CameraSlotViewModel(int slotNumber)
    {
        SlotNumber = slotNumber;
        SelectCommand = new RelayCommand(_ => SelectAction?.Invoke(this));
        AssignSelectedCommand = new RelayCommand(_ => AssignSelectedAction?.Invoke(this));
        ClearCommand = new RelayCommand(_ => ClearAction?.Invoke(this));
        FocusCommand = new RelayCommand(_ => FocusAction?.Invoke(this));
        ToggleStretchCommand = new RelayCommand(_ => ToggleStretchMode());
    }

    public int SlotNumber { get; }

    public Action<CameraSlotViewModel>? SelectAction { get; set; }

    public Action<CameraSlotViewModel>? AssignSelectedAction { get; set; }

    public Action<CameraSlotViewModel>? ClearAction { get; set; }

    public Action<CameraSlotViewModel>? FocusAction { get; set; }

    public ICommand SelectCommand { get; }

    public ICommand AssignSelectedCommand { get; }

    public ICommand ClearCommand { get; }

    public ICommand FocusCommand { get; }
    public ICommand ToggleStretchCommand { get; }

    public CameraTileViewModel? Camera
    {
        get => _camera;
        set
        {
            var oldCamera = _camera;
            if (SetProperty(ref _camera, value))
            {
                if (oldCamera != null)
                {
                    oldCamera.PropertyChanged -= OnCameraPropertyChanged;
                }
                if (value != null)
                {
                    value.PropertyChanged += OnCameraPropertyChanged;
                }

                OnPropertyChanged(nameof(HasCamera));
                OnPropertyChanged(nameof(IsEmpty));
                OnPropertyChanged(nameof(SlotTitle));
                OnPropertyChanged(nameof(BorderBrush));
                OnPropertyChanged(nameof(BorderThickness));
                NotifyCameraBindings();
                ApplyCameraSize();
            }
        }
    }

    public bool IsSelected
    {
        get => _isSelected;
        set
        {
            if (SetProperty(ref _isSelected, value))
            {
                OnPropertyChanged(nameof(BorderBrush));
                OnPropertyChanged(nameof(BorderThickness));
            }
        }
    }

    public double TileWidth
    {
        get => _tileWidth;
        set
        {
            if (SetProperty(ref _tileWidth, value))
            {
                ApplyCameraSize();
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
                ApplyCameraSize();
            }
        }
    }

    public MediaPlayer? MediaPlayer => _mediaPlayer;

    public string? CallbackPlaybackUrl => Camera?.PlaybackUrl;

    public int CallbackFrameWidth => Camera?.SourceFrameWidth ?? 1152;

    public int CallbackFrameHeight => Camera?.SourceFrameHeight ?? 1920;

    public bool HasCamera => Camera is not null;

    public bool IsEmpty => Camera is null;

    public bool IsPlaybackActive => _isPlaying || _isOpening;

    public bool IsPlayingAssignedCamera => Camera is not null
        && _currentCameraId == Camera.Camera.Id
        && string.Equals(_currentPlaybackUrl, Camera.PlaybackUrl, StringComparison.OrdinalIgnoreCase)
        && IsPlaybackActive;

    public bool NeedsPlaybackSwitch => Camera is not null
        && (!string.Equals(_currentPlaybackUrl, Camera.PlaybackUrl, StringComparison.OrdinalIgnoreCase)
            || _currentCameraId != Camera.Camera.Id);

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

    public string SlotTitle => Camera is null
        ? $"Slot {SlotNumber:00}"
        : $"{SlotNumber:00}  {Camera.Camera.Name}";

    public string SlotBadgeText => $"{SlotNumber:00}";

    public bool VideoSuppressed
    {
        get => _videoSuppressed;
        set
        {
            if (SetProperty(ref _videoSuppressed, value))
            {
                OnPropertyChanged(nameof(UseCallbackPlayback));
                OnPropertyChanged(nameof(UseNativePlayback));
            }
        }
    }

    public string ImageBadgeText => Camera?.ImageBadgeText ?? "IMG -";

    public IBrush ImageBadgeBrush => Camera?.ImageBadgeBrush ?? NeutralBrush;

    public string IaBadgeText => Camera?.IaBadgeText ?? "IA -";

    public IBrush IaBadgeBrush => Camera?.IaBadgeBrush ?? NeutralBrush;

    public string CameraIdText => Camera?.CameraIdText ?? "-";

    public string CameraName => Camera?.Camera.Name ?? "Sem camera";

    public IEnumerable<TrackBoxViewModel> TrackBoxes => Camera is null ? Array.Empty<TrackBoxViewModel>() : Camera.TrackBoxes;

    public int TrackOverlayRevision => Camera?.TrackOverlayRevision ?? 0;

    public double VideoOverlayWidth => Camera?.VideoOverlayWidth ?? Math.Max(1, TileWidth - 2);

    public double VideoOverlayHeight => Camera?.VideoOverlayHeight ?? Math.Max(1, TileHeight - 24);

    public bool BoxesEnabled => Camera?.BoxesEnabled ?? false;

    public bool ShowTrackOverlay => Camera?.ShowTrackOverlay ?? false;

    public bool CanRequestTrackBoxes => Camera?.ShowTrackOverlay == true
        && !_hasPlaybackError;

    public bool CanRenderTrackOverlay => ShowTrackOverlay
        && Camera?.IsStreamAvailable == true
        && !_hasPlaybackError;

    public bool UseCallbackPlayback => CanRenderTrackOverlay && !VideoSuppressed;

    public bool UseNativePlayback => HasCamera && !CanRenderTrackOverlay && !VideoSuppressed && !IsModalActive;

    public string TrackStatusText => Camera?.TrackStatusText ?? "sem tracks";

    public string FooterText => Camera?.FooterText ?? "Sem local - Sem grupo";

    public string StatusBadgeText => _hasPlaybackError ? "P Erro" : _isPlaying ? "P OK" : _isOpening ? "P Abr" : "P Pronto";

    public IBrush StatusBadgeBrush => _hasPlaybackError ? BadBrush : _isPlaying ? OkBrush : NeutralBrush;

    public string EmptyMessage
    {
        get
        {
            if (Camera is null)
            {
                return "";
            }

            if (!Camera.Camera.StreamUrlAvailable)
            {
                return Camera.Camera.RegistrationReason ?? "Stream indisponivel";
            }

            if (_hasPlaybackError)
            {
                return _playbackMessage;
            }

            return _isPlaying ? "" : _playbackMessage;
        }
    }

    public bool HasPlaybackMessage => !string.IsNullOrWhiteSpace(EmptyMessage);

    public IBrush BorderBrush
    {
        get
        {
            if (IsSelected) return SelectedBrush;
            if (HasCamera && Camera != null)
            {
                var severity = (Camera.HighestOpenSeverity ?? "").Trim().ToLowerInvariant();
                if (severity == "critical") return CriticalAlarmBrush;
                if (severity == "high") return HighAlarmBrush;
                return FilledBrush;
            }
            return EmptyBrush;
        }
    }

    public Avalonia.Thickness BorderThickness => (IsSelected || (Camera != null && (Camera.HighestOpenSeverity == "critical" || Camera.HighestOpenSeverity == "high")))
        ? new Avalonia.Thickness(2)
        : new Avalonia.Thickness(1);

    private void OnCameraPropertyChanged(object? sender, System.ComponentModel.PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(CameraTileViewModel.HighestOpenSeverity))
        {
            Dispatcher.UIThread.Post(() =>
            {
                OnPropertyChanged(nameof(BorderBrush));
                OnPropertyChanged(nameof(BorderThickness));
            });
        }
    }

    private Avalonia.Media.Stretch _stretchMode = Avalonia.Media.Stretch.Uniform;
    public Avalonia.Media.Stretch StretchMode
    {
        get => _stretchMode;
        set
        {
            if (SetProperty(ref _stretchMode, value))
            {
                OnPropertyChanged(nameof(StretchModeText));
            }
        }
    }

    public string StretchModeText => StretchMode == Avalonia.Media.Stretch.Uniform ? "Enquadrar" : "Preencher";

    public void ToggleStretchMode()
    {
        StretchMode = StretchMode == Avalonia.Media.Stretch.Uniform ? Avalonia.Media.Stretch.UniformToFill : Avalonia.Media.Stretch.Uniform;
    }

    public void ApplyCameraSize()
    {
        if (Camera is null)
        {
            return;
        }

        Camera.TileWidth = TileWidth;
        Camera.TileHeight = TileHeight;
        OnPropertyChanged(nameof(VideoOverlayWidth));
        OnPropertyChanged(nameof(VideoOverlayHeight));
    }

    private void NotifyCameraBindings()
    {
        ApplyVideoAspectRatio();
        OnPropertyChanged(nameof(ImageBadgeText));
        OnPropertyChanged(nameof(ImageBadgeBrush));
        OnPropertyChanged(nameof(IaBadgeText));
        OnPropertyChanged(nameof(IaBadgeBrush));
        OnPropertyChanged(nameof(CameraIdText));
        OnPropertyChanged(nameof(CameraName));
        OnPropertyChanged(nameof(TrackBoxes));
        OnPropertyChanged(nameof(TrackOverlayRevision));
        OnPropertyChanged(nameof(CallbackPlaybackUrl));
        OnPropertyChanged(nameof(CallbackFrameWidth));
        OnPropertyChanged(nameof(CallbackFrameHeight));
        OnPropertyChanged(nameof(VideoOverlayWidth));
        OnPropertyChanged(nameof(VideoOverlayHeight));
        OnPropertyChanged(nameof(BoxesEnabled));
        OnPropertyChanged(nameof(ShowTrackOverlay));
        OnPropertyChanged(nameof(CanRequestTrackBoxes));
        OnPropertyChanged(nameof(CanRenderTrackOverlay));
        OnPropertyChanged(nameof(UseCallbackPlayback));
        OnPropertyChanged(nameof(UseNativePlayback));
        OnPropertyChanged(nameof(TrackStatusText));
        OnPropertyChanged(nameof(FooterText));
        OnPropertyChanged(nameof(EmptyMessage));
        OnPropertyChanged(nameof(HasPlaybackMessage));
    }

    public void NotifyCameraOverlayChanged()
    {
        ApplyVideoAspectRatio();
        OnPropertyChanged(nameof(TrackBoxes));
        OnPropertyChanged(nameof(TrackOverlayRevision));
        OnPropertyChanged(nameof(CallbackPlaybackUrl));
        OnPropertyChanged(nameof(CallbackFrameWidth));
        OnPropertyChanged(nameof(CallbackFrameHeight));
        OnPropertyChanged(nameof(VideoOverlayWidth));
        OnPropertyChanged(nameof(VideoOverlayHeight));
        OnPropertyChanged(nameof(BoxesEnabled));
        OnPropertyChanged(nameof(ShowTrackOverlay));
        OnPropertyChanged(nameof(CanRequestTrackBoxes));
        OnPropertyChanged(nameof(CanRenderTrackOverlay));
        OnPropertyChanged(nameof(UseCallbackPlayback));
        OnPropertyChanged(nameof(UseNativePlayback));
        OnPropertyChanged(nameof(TrackStatusText));
        OnPropertyChanged(nameof(EmptyMessage));
        OnPropertyChanged(nameof(HasPlaybackMessage));
    }

    public void NotifyCameraBindingsChanged()
    {
        NotifyCameraBindings();
    }

    public async Task ReconcilePlayerAsync(CancellationToken cancellationToken = default, bool force = false)
    {
        var camera = Camera;
        if (camera is null)
        {
            await StopPlayerAsync(cancellationToken).ConfigureAwait(false);
            return;
        }

        if (!camera.IsStreamAvailable)
        {
            await StopPlayerAsync(cancellationToken).ConfigureAwait(false);
            UpdatePlaybackState(false, false, true, "Stream indisponivel");
            return;
        }

        if (camera.BoxesEnabled)
        {
            await StopPlayerAsync(cancellationToken).ConfigureAwait(false);
            _currentCameraId = camera.Camera.Id;
            _currentPlaybackUrl = camera.PlaybackUrl;
            UpdatePlaybackState(true, false, false, "Callback OK");
            OnPropertyChanged(nameof(CallbackPlaybackUrl));
            OnPropertyChanged(nameof(CallbackFrameWidth));
            OnPropertyChanged(nameof(CallbackFrameHeight));
            OnPropertyChanged(nameof(UseCallbackPlayback));
            OnPropertyChanged(nameof(UseNativePlayback));
            return;
        }

        if (IsPlayingAssignedCamera && !force)
        {
            return;
        }

        var forceForCameraChange = force || NeedsPlaybackSwitch;
        if (!forceForCameraChange && !CanAutoStartPlayer)
        {
            AppLogger.Warn($"Slot {SlotNumber:00} - auto-start ignorado por cooldown. camera={camera.Camera.Id}; url={camera.PlaybackUrl ?? "-"}");
            return;
        }

        await StartPlayerAsync(cancellationToken, force: forceForCameraChange).ConfigureAwait(false);
    }

    public async Task RestartPlayerAsync(CancellationToken cancellationToken = default)
    {
        await StartPlayerAsync(cancellationToken, force: true).ConfigureAwait(false);
    }

    public async Task StopPlayerAsync(CancellationToken cancellationToken = default, bool detachSurface = false)
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
            AppLogger.Warn($"Slot {SlotNumber:00} - stop cancelado.");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Slot {SlotNumber:00} - excecao ao parar player.", exc);
        }
        finally
        {
            _playerOperationLock.Release();
        }
    }

    private async Task StartPlayerAsync(CancellationToken cancellationToken, bool force)
    {
        var camera = Camera;
        var playbackUrl = camera?.PlaybackUrl;
        if (_disposed || camera is null || string.IsNullOrWhiteSpace(playbackUrl))
        {
            return;
        }

        var operationCancellation = ReplacePlayerOperationCancellation(cancellationToken);
        var token = operationCancellation.Token;

        await _playerOperationLock.WaitAsync(token).ConfigureAwait(false);
        try
        {
            if (_disposed || Camera is null || Camera.Camera.Id != camera.Camera.Id)
            {
                return;
            }

            AppLogger.Info($"Slot {SlotNumber:00} - start solicitado. camera={camera.Camera.Id}; force={force}; url={playbackUrl}");

            if (force)
            {
                _lastPlaybackErrorAt = null;
                _lastStartAttemptAt = null;
            }

            _lastStartAttemptAt = DateTimeOffset.UtcNow;
            await StopInternalAsync(token, detachSurface: false).ConfigureAwait(false);

            var media = new Media(SharedLibVlc.Value, playbackUrl, FromType.FromLocation);
            if (playbackUrl.StartsWith("rtsp", StringComparison.OrdinalIgnoreCase))
            {
                media.AddOption(":rtsp-tcp");
            }

            media.AddOption(":network-caching=80");
            media.AddOption(":live-caching=80");
            media.AddOption(":drop-late-frames");
            media.AddOption(":skip-frames");
            media.AddOption(":http-reconnect");
            media.AddOption(":avcodec-hw=none");
            media.AddOption(":clock-jitter=0");
            media.AddOption(":clock-synchro=0");

            var player = await Dispatcher.UIThread.InvokeAsync(EnsureMediaPlayer, DispatcherPriority.Background, token);
            var playResult = player.Play(media);

            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                _currentCameraId = camera.Camera.Id;
                _currentPlaybackUrl = playbackUrl;
                _media = media;

                ApplyVideoAspectRatio();
                UpdatePlaybackState(false, playResult, !playResult, playResult ? "Abrindo" : "Falha ao iniciar player");

                if (!playResult)
                {
                    AppLogger.Warn($"Slot {SlotNumber:00} - LibVLC retornou false no Play(). camera={camera.Camera.Id}; url={playbackUrl}");
                }
                else
                {
                    AppLogger.Info($"Slot {SlotNumber:00} - start concluido/enviado ao LibVLC. camera={camera.Camera.Id}; url={playbackUrl}");
                }
            }, DispatcherPriority.Background, token);
        }
        catch (OperationCanceledException)
        {
            AppLogger.Warn($"Slot {SlotNumber:00} - start cancelado.");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Slot {SlotNumber:00} - excecao ao iniciar player.", exc);
            UpdatePlaybackState(false, false, true, "Erro no player");
        }
        finally
        {
            _playerOperationLock.Release();
        }
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
        ApplyVideoAspectRatio();
        return _mediaPlayer;
    }

    private void ApplyVideoAspectRatio()
    {
        var player = _mediaPlayer;
        if (player is null)
        {
            return;
        }

        var aspectRatio = Camera?.VideoAspectRatio;
        player.Scale = 0;
        player.AspectRatio = string.IsNullOrWhiteSpace(aspectRatio) ? null : aspectRatio;
    }

    private async Task StopInternalAsync(CancellationToken token, bool detachSurface)
    {
        if (_mediaPlayer is null && _media is null && _currentPlaybackUrl is null)
        {
            return;
        }

        var url = _currentPlaybackUrl ?? Camera?.PlaybackUrl ?? "-";
        AppLogger.Info($"Slot {SlotNumber:00} - stop solicitado. detachSurface={detachSurface}; url={url}");

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
                    AppLogger.Warn($"Slot {SlotNumber:00} - excecao ao executar Stop() em background: {ex.Message}");
                }
            });
        }

        if (!ReferenceEquals(stoppedTask, Task.CompletedTask))
        {
            var completed = await Task.WhenAny(stoppedTask, Task.Delay(900, token)).ConfigureAwait(false);
            if (completed != stoppedTask)
            {
                AppLogger.Warn($"Slot {SlotNumber:00} - timeout aguardando evento Stopped.");
            }
        }

        MediaPlayer? detachedPlayer = null;
        await Dispatcher.UIThread.InvokeAsync(() =>
        {
            _media?.Dispose();
            _media = null;
            _currentPlaybackUrl = null;
            _currentCameraId = null;
            UpdatePlaybackState(false, false, false, "Pronto");
            if (detachSurface && _mediaPlayer is not null)
            {
                AppLogger.Info($"Slot {SlotNumber:00} - surface detach iniciado.");
                detachedPlayer = _mediaPlayer;
                detachedPlayer.Opening -= HandleOpening;
                detachedPlayer.Playing -= HandlePlaying;
                detachedPlayer.Stopped -= HandleStopped;
                detachedPlayer.EncounteredError -= HandleEncounteredError;
                _mediaPlayer = null;
                OnPropertyChanged(nameof(MediaPlayer));
                AppLogger.Info($"Slot {SlotNumber:00} - surface detach desbindado.");
            }
        }, DispatcherPriority.Background, token);

        if (detachedPlayer is not null)
        {
            await Dispatcher.UIThread.InvokeAsync(() => { }, DispatcherPriority.Render, token);
            await Task.Delay(220, token).ConfigureAwait(false);
            try
            {
                detachedPlayer.Dispose();
                AppLogger.Info($"Slot {SlotNumber:00} - surface detach concluido.");
            }
            catch (Exception exc)
            {
                AppLogger.Error($"Slot {SlotNumber:00} - excecao ao descartar player desanexado.", exc);
            }
        }

        AppLogger.Info($"Slot {SlotNumber:00} - stop concluido.");
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

    private void UpdatePlaybackState(bool isPlaying, bool isOpening, bool hasError, string message)
    {
        void Apply()
        {
            _isPlaying = isPlaying;
            _isOpening = isOpening;
            _hasPlaybackError = hasError;
            _playbackMessage = message;
            NotifyPlaybackState();
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

    private void NotifyPlaybackState()
    {
        if (Camera?.BoxesEnabled != true)
        {
            Camera?.ApplyTracks(null);
            OnPropertyChanged(nameof(TrackBoxes));
            OnPropertyChanged(nameof(TrackOverlayRevision));
        }

        OnPropertyChanged(nameof(IsPlaybackActive));
        OnPropertyChanged(nameof(IsPlayingAssignedCamera));
        OnPropertyChanged(nameof(CanRequestTrackBoxes));
        OnPropertyChanged(nameof(CanRenderTrackOverlay));
        OnPropertyChanged(nameof(UseCallbackPlayback));
        OnPropertyChanged(nameof(UseNativePlayback));
        OnPropertyChanged(nameof(StatusBadgeText));
        OnPropertyChanged(nameof(StatusBadgeBrush));
        OnPropertyChanged(nameof(EmptyMessage));
        OnPropertyChanged(nameof(HasPlaybackMessage));
    }

    private static TaskCompletionSource<object?> NewStopSignal()
    {
        return new TaskCompletionSource<object?>(TaskCreationOptions.RunContinuationsAsynchronously);
    }

    private void HandleOpening(object? sender, EventArgs args)
    {
        try
        {
            AppLogger.Info($"Slot {SlotNumber:00} - evento LibVLC Opening.");
            UpdatePlaybackState(false, true, false, "Abrindo");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Slot {SlotNumber:00} - excecao no callback Opening.", exc);
        }
    }

    private void HandlePlaying(object? sender, EventArgs args)
    {
        try
        {
            AppLogger.Info($"Slot {SlotNumber:00} - evento LibVLC Playing.");
            _lastPlaybackErrorAt = null;
            _consecutiveErrorCount = 0;
            UpdatePlaybackState(true, false, false, "Player OK");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Slot {SlotNumber:00} - excecao no callback Playing.", exc);
        }
    }

    private void HandleStopped(object? sender, EventArgs args)
    {
        try
        {
            AppLogger.Info($"Slot {SlotNumber:00} - evento LibVLC Stopped.");
            _stoppedSignal.TrySetResult(null);
            UpdatePlaybackState(false, false, false, "Pronto");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Slot {SlotNumber:00} - excecao no callback Stopped.", exc);
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
                AppLogger.Warn($"Slot {SlotNumber:00} - LibVLC EncounteredError (tentativa {_consecutiveErrorCount}). URL={_currentPlaybackUrl ?? Camera?.PlaybackUrl ?? "-"}");
            }

            UpdatePlaybackState(false, false, true, "Erro no player");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Slot {SlotNumber:00} - excecao no callback EncounteredError.", exc);
        }
    }

    public void Dispose()
    {
        _disposed = true;
        _playerOperationCancellation?.Cancel();
        try
        {
            _media?.Dispose();
            _media = null;
            if (_mediaPlayer is not null)
            {
                var playerToStop = _mediaPlayer;
                _mediaPlayer = null;
                playerToStop.Opening -= HandleOpening;
                playerToStop.Playing -= HandlePlaying;
                playerToStop.Stopped -= HandleStopped;
                playerToStop.EncounteredError -= HandleEncounteredError;
                Task.Run(() =>
                {
                    try
                    {
                        playerToStop.Stop();
                        playerToStop.Dispose();
                    }
                    catch {}
                });
            }
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Slot {SlotNumber:00} - excecao ao descartar player.", exc);
        }

        _playerOperationCancellation?.Dispose();
        _playerOperationLock.Dispose();
    }
}
