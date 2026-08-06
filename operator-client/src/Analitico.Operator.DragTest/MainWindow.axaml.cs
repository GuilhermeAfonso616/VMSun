using System;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Threading;
using Analitico.Operator.DragTest.Controls;
using LibVLCSharp.Shared;

namespace Analitico.Operator.DragTest;

public sealed partial class MainWindow : Window
{
    private static readonly DataFormat<string> DragTextFormat =
        DataFormat.CreateStringApplicationFormat("analitico.dragtest.text");

    private const double DragStartThreshold = 5;
    private string? _pendingSlotText;
    private Point _pendingStart;
    private IPointer? _pendingPointer;
    private readonly LowLevelMouseHook _globalMouseHook = new();
    private readonly DragGhostWindow _dragGhostWindow = new();
    private readonly LibVLC _libVlc = new(
        "--no-audio",
        "--rtsp-tcp",
        "--network-caching=250",
        "--embedded-video",
        "--no-video-title-show");
    private MediaPlayer? _mediaPlayer;
    private Media? _media;
    private bool _videoViewAttached;
    private Point? _globalVideoDragStart;
    private bool _globalVideoDragging;
    private int _diagnosticOverlayRevision;

    public MainWindow()
    {
        DragTestLog.Info("MainWindow DragTest ctor iniciado.");
        InitializeComponent();
        CallbackCanvas.StatusChanged += OnCallbackCanvasStatusChanged;
        VideoView.AttachedToVisualTree += (_, _) =>
        {
            _videoViewAttached = true;
            EnsureEmbeddedPlayer();
            DragTestLog.Info("VideoView anexado a arvore visual; MediaPlayer vinculado ao host.");
        };
        VideoView.DetachedFromVisualTree += (_, _) =>
        {
            _videoViewAttached = false;
            DragTestLog.Info("VideoView removido da arvore visual.");
        };
        DragButton.AddHandler(
            InputElement.PointerPressedEvent,
            OnButtonDragStart,
            RoutingStrategies.Tunnel | RoutingStrategies.Bubble,
            handledEventsToo: true);
        _globalMouseHook.LeftButtonDown += OnGlobalMouseLeftButtonDown;
        _globalMouseHook.MouseMove += OnGlobalMouseMove;
        _globalMouseHook.LeftButtonUp += OnGlobalMouseLeftButtonUp;
        Opened += (_, _) =>
        {
            StartGlobalMouseHook();
            Dispatcher.UIThread.Post(StartCallbackCanvas, DispatcherPriority.Background);
        };
        Closed += (_, _) =>
        {
            DragTestLog.Info("MainWindow DragTest fechada; descartando LibVLC.");
            _globalMouseHook.Dispose();
            _dragGhostWindow.Close();
            _mediaPlayer?.Stop();
            _media?.Dispose();
            _mediaPlayer?.Dispose();
            CallbackCanvas.StatusChanged -= OnCallbackCanvasStatusChanged;
            CallbackCanvas.Dispose();
            _libVlc.Dispose();
        };
        DragTestLog.Info("MainWindow DragTest pronta.");
    }

    private void StartGlobalMouseHook()
    {
        try
        {
            _globalMouseHook.Start();
            DragTestLog.Info("Hook global de mouse iniciado no DragTest.");
            Log("hook global de mouse ativo");
        }
        catch (Exception exc)
        {
            DragTestLog.Error("Falha ao iniciar hook global de mouse no DragTest.", exc);
            Log($"erro no hook global: {exc.Message}");
        }
    }

    private async void OnTextDragStart(object? sender, PointerPressedEventArgs e)
    {
        await StartDragAsync("texto", e);
    }

    private async void OnPanelDragStart(object? sender, PointerPressedEventArgs e)
    {
        await StartDragAsync("painel", e);
    }

    private async void OnButtonDragStart(object? sender, PointerPressedEventArgs e)
    {
        Log("botao recebeu pointer press com handledEventsToo");
        await StartDragAsync("botao", e);
    }

    private async void OnVideoHeaderDragStart(object? sender, PointerPressedEventArgs e)
    {
        await StartDragAsync("video-header", e);
    }

    private async void OnVideoOverlayDragStart(object? sender, PointerPressedEventArgs e)
    {
        await StartDragAsync("video-overlay", e);
    }

    private void OnVideoNativeDragStarted(object? sender, NativeVideoDragEventArgs e)
    {
        Log($"video nativo: drag iniciado em {e.ScreenX},{e.ScreenY}");
        DragTestLog.Info($"Video nativo drag iniciado: screen={e.ScreenX},{e.ScreenY}");
    }

    private void OnVideoNativeDragCompleted(object? sender, NativeVideoDragEventArgs e)
    {
        var clientPoint = ScreenToWindowPoint(e.ScreenX, e.ScreenY);
        var targetOrigin = DropTarget.TranslatePoint(new Point(0, 0), this);
        var insideTarget = targetOrigin is not null
            && clientPoint.X >= targetOrigin.Value.X
            && clientPoint.Y >= targetOrigin.Value.Y
            && clientPoint.X <= targetOrigin.Value.X + DropTarget.Bounds.Width
            && clientPoint.Y <= targetOrigin.Value.Y + DropTarget.Bounds.Height;

        var message = insideTarget
            ? $"Recebido: video-native em {DateTime.Now:HH:mm:ss}"
            : $"Video nativo solto fora do alvo em {DateTime.Now:HH:mm:ss}";
        DropResultText.Text = message;
        Log($"video nativo: drop screen={e.ScreenX},{e.ScreenY}; alvo={insideTarget}");
        DragTestLog.Info($"Video nativo drop: screen={e.ScreenX},{e.ScreenY}; target={insideTarget}");
    }

    private void OnGlobalMouseLeftButtonDown(object? sender, LowLevelMouseEventArgs e)
    {
        Dispatcher.UIThread.Post(() =>
        {
            if (!IsActive || !IsPointInsideControl(VideoView, e.ScreenX, e.ScreenY))
            {
                return;
            }

            _globalVideoDragStart = new Point(e.ScreenX, e.ScreenY);
            _globalVideoDragging = false;
            Log($"video global: press em {e.ScreenX},{e.ScreenY}");
            DragTestLog.Info($"Video global press: screen={e.ScreenX},{e.ScreenY}");
        });
    }

    private void OnGlobalMouseMove(object? sender, LowLevelMouseEventArgs e)
    {
        Dispatcher.UIThread.Post(() =>
        {
            if (_globalVideoDragStart is null || _globalVideoDragging)
            {
                if (_globalVideoDragging)
                {
                    UpdateGlobalDragCue(e.ScreenX, e.ScreenY);
                }

                return;
            }

            var delta = new Point(e.ScreenX, e.ScreenY) - _globalVideoDragStart.Value;
            if (Math.Abs(delta.X) < DragStartThreshold && Math.Abs(delta.Y) < DragStartThreshold)
            {
                return;
            }

            _globalVideoDragging = true;
            ShowGlobalDragCue(e.ScreenX, e.ScreenY);
            Log($"video global: drag confirmado em {e.ScreenX},{e.ScreenY}");
            DragTestLog.Info($"Video global drag confirmado: screen={e.ScreenX},{e.ScreenY}");
        });
    }

    private void OnGlobalMouseLeftButtonUp(object? sender, LowLevelMouseEventArgs e)
    {
        Dispatcher.UIThread.Post(() =>
        {
            var hadStart = _globalVideoDragStart is not null;
            var wasDragging = _globalVideoDragging;
            _globalVideoDragStart = null;
            _globalVideoDragging = false;
            HideGlobalDragCue();

            if (!hadStart)
            {
                return;
            }

            var insideTarget = IsPointInsideControl(DropTarget, e.ScreenX, e.ScreenY);
            if (wasDragging && insideTarget)
            {
                DropResultText.Text = $"Recebido: video-global em {DateTime.Now:HH:mm:ss}";
                Log($"video global: drop no alvo {e.ScreenX},{e.ScreenY}");
            }
            else
            {
                Log($"video global: solto fora do alvo ou sem movimento. alvo={insideTarget}");
            }

            DragTestLog.Info($"Video global drop: screen={e.ScreenX},{e.ScreenY}; dragging={wasDragging}; target={insideTarget}");
        });
    }

    private void ShowGlobalDragCue(int screenX, int screenY)
    {
        _dragGhostWindow.SetText("Arrastando video real", "Solte no alvo verde", "captura global ativa");
        _dragGhostWindow.ShowOrMove(this, screenX, screenY);
        UpdateGlobalDragCue(screenX, screenY);
    }

    private void UpdateGlobalDragCue(int screenX, int screenY)
    {
        var insideTarget = IsPointInsideControl(DropTarget, screenX, screenY);
        _dragGhostWindow.SetText(
            "Arrastando video real",
            insideTarget ? "Solte agora no alvo" : "Solte no alvo verde",
            "captura global ativa");
        _dragGhostWindow.MoveNear(screenX, screenY);
    }

    private void HideGlobalDragCue()
    {
        _dragGhostWindow.HideGhost();
    }

    private void OnStartVideoClick(object? sender, RoutedEventArgs e)
    {
        try
        {
            StopCallbackCanvas();
            CallbackCanvas.IsVisible = false;
            VideoView.IsVisible = true;
            VideoDragOverlay.IsVisible = true;
            var url = RtspUrlBox.Text?.Trim();
            if (string.IsNullOrWhiteSpace(url))
            {
                Log("RTSP vazio");
                return;
            }

            var player = EnsureEmbeddedPlayer();
            if (!_videoViewAttached)
            {
                Log("VideoView ainda nao anexado; tente iniciar novamente em 1s");
                DragTestLog.Info("Start ignorado: VideoView ainda nao anexado.");
                return;
            }

            player.Stop();
            _media?.Dispose();
            _media = new Media(_libVlc, url, FromType.FromLocation);
            _media.AddOption(":rtsp-tcp");
            _media.AddOption(":network-caching=180");
            _media.AddOption(":live-caching=180");
            _media.AddOption(":drop-late-frames");
            _media.AddOption(":skip-frames");
            _media.AddOption(":no-video-title-show");
            var ok = player.Play(_media);
            ApplyCurrentAspect();
            Log($"video iniciado: {url}");
            DragTestLog.Info($"Video iniciado: ok={ok}; embeddedAttached={_videoViewAttached}; url={url}");
        }
        catch (Exception exc)
        {
            Log($"erro ao iniciar video: {exc.Message}");
            DragTestLog.Error("Erro ao iniciar video.", exc);
        }
    }

    private void OnStopVideoClick(object? sender, RoutedEventArgs e)
    {
        _mediaPlayer?.Stop();
        Log("video parado");
        DragTestLog.Info("Video parado.");
    }

    private void OnStartCallbackCanvasClick(object? sender, RoutedEventArgs e)
    {
        StartCallbackCanvas();
    }

    private void StartCallbackCanvas()
    {
        var url = RtspUrlBox.Text?.Trim();
        if (string.IsNullOrWhiteSpace(url))
        {
            Log("RTSP vazio para callback");
            return;
        }

        _mediaPlayer?.Stop();
        DiagnosticOverlay.IsVisible = false;
        VideoDragOverlay.IsVisible = false;
        VideoView.IsVisible = false;
        CallbackCanvas.IsVisible = true;
        CallbackCanvas.Start(url);
        Log($"CALLBACK iniciado, aguardando frames: {url}");
        DragTestLog.Info($"Callback canvas iniciado: {url}");
    }

    private void OnStopCallbackCanvasClick(object? sender, RoutedEventArgs e)
    {
        StopCallbackCanvas();
        Log("callback canvas parado");
        DragTestLog.Info("Callback canvas parado.");
    }

    private void StopCallbackCanvas()
    {
        CallbackCanvas.Stop();
        CallbackCanvas.IsVisible = false;
        VideoView.IsVisible = true;
        VideoDragOverlay.IsVisible = true;
    }

    private void OnCallbackCanvasStatusChanged(object? sender, string message)
    {
        Log(message);
        DragTestLog.Info($"Callback canvas status: {message}");
    }

    private void OnToggleDiagnosticOverlayClick(object? sender, RoutedEventArgs e)
    {
        DiagnosticOverlay.IsVisible = !DiagnosticOverlay.IsVisible;
        DiagnosticOverlay.Revision = ++_diagnosticOverlayRevision;
        Log(DiagnosticOverlay.IsVisible
            ? "overlay diagnostico ligado"
            : "overlay diagnostico desligado");
        DragTestLog.Info($"Overlay diagnostico: visible={DiagnosticOverlay.IsVisible}");
    }

    private void OnSetPortraitAspectClick(object? sender, RoutedEventArgs e)
    {
        var player = EnsureEmbeddedPlayer();
        player.Scale = 0;
        player.AspectRatio = "1152:1920";
        Log("aspect aplicado: 1152:1920");
        DragTestLog.Info("Aspect aplicado no DragTest: 1152:1920");
    }

    private void OnResetAspectClick(object? sender, RoutedEventArgs e)
    {
        var player = EnsureEmbeddedPlayer();
        player.Scale = 0;
        player.AspectRatio = null;
        Log("aspect resetado");
        DragTestLog.Info("Aspect resetado no DragTest.");
    }

    private void ApplyCurrentAspect()
    {
        var player = EnsureEmbeddedPlayer();
        player.Scale = 0;
        if (RtspUrlBox.Text?.Contains("cam_11", StringComparison.OrdinalIgnoreCase) == true)
        {
            player.AspectRatio = "1152:1920";
        }
    }

    private void OnSlotPointerPressed(object? sender, PointerPressedEventArgs e)
    {
        var point = e.GetCurrentPoint(this);
        if (!point.Properties.IsLeftButtonPressed)
        {
            return;
        }

        _pendingSlotText = "slot-fake";
        _pendingStart = point.Position;
        _pendingPointer = e.Pointer;
        e.Pointer.Capture(sender as Control ?? this);
        Log("slot fake: pointer press capturado");
        e.Handled = true;
    }

    private async void OnSlotPointerMoved(object? sender, PointerEventArgs e)
    {
        if (_pendingSlotText is null || _pendingPointer != e.Pointer)
        {
            return;
        }

        var point = e.GetCurrentPoint(this);
        if (!point.Properties.IsLeftButtonPressed)
        {
            ClearPendingSlotDrag();
            return;
        }

        var delta = point.Position - _pendingStart;
        if (Math.Abs(delta.X) < DragStartThreshold && Math.Abs(delta.Y) < DragStartThreshold)
        {
            return;
        }

        var payload = _pendingSlotText;
        ClearPendingSlotDrag();
        await StartDragAsync(payload, e);
    }

    private void OnSlotPointerReleased(object? sender, PointerReleasedEventArgs e)
    {
        ClearPendingSlotDrag();
    }

    private void OnSlotPointerCaptureLost(object? sender, PointerCaptureLostEventArgs e)
    {
        ClearPendingSlotDrag();
    }

    private void OnDropTargetDragOver(object? sender, DragEventArgs e)
    {
        e.DragEffects = e.DataTransfer.TryGetValue<string>(DragTextFormat) is null
            ? DragDropEffects.None
            : DragDropEffects.Move;
        e.Handled = true;
    }

    private void OnDropTargetDrop(object? sender, DragEventArgs e)
    {
        var value = e.DataTransfer.TryGetValue<string>(DragTextFormat) ?? e.DataTransfer.TryGetText() ?? "-";
        DropResultText.Text = $"Recebido: {value} em {DateTime.Now:HH:mm:ss}";
        Log($"drop recebido: {value}");
        e.Handled = true;
    }

    private async System.Threading.Tasks.Task StartDragAsync(string source, PointerEventArgs e)
    {
        var point = e.GetCurrentPoint(this);
        if (!point.Properties.IsLeftButtonPressed)
        {
            Log($"drag ignorado sem botao esquerdo: {source}");
            return;
        }

        var payload = $"{source} | {DateTime.Now:HH:mm:ss.fff}";
        var data = new DataTransfer();
        data.Add(DataTransferItem.Create(DragTextFormat, payload));
        data.Add(DataTransferItem.CreateText(payload));
        Log($"drag iniciado: {payload}");
        DragTestLog.Info($"Drag iniciado: {payload}");
        await DragDrop.DoDragDropAsync(e, data, DragDropEffects.Move | DragDropEffects.Copy);
        Log($"drag finalizado: {payload}");
        DragTestLog.Info($"Drag finalizado: {payload}");
        e.Handled = true;
    }

    private void ClearPendingSlotDrag()
    {
        _pendingPointer?.Capture(null);
        _pendingPointer = null;
        _pendingSlotText = null;
    }

    private void Log(string message)
    {
        LogText.Text = $"Log: {message}";
    }

    private Point ScreenToWindowPoint(int screenX, int screenY)
    {
        var scaling = RenderScaling <= 0 ? 1 : RenderScaling;
        return new Point(
            (screenX - Position.X) / scaling,
            (screenY - Position.Y) / scaling);
    }

    private bool IsPointInsideControl(Control control, int screenX, int screenY)
    {
        var clientPoint = ScreenToWindowPoint(screenX, screenY);
        var origin = control.TranslatePoint(new Point(0, 0), this);
        return origin is not null
            && clientPoint.X >= origin.Value.X
            && clientPoint.Y >= origin.Value.Y
            && clientPoint.X <= origin.Value.X + control.Bounds.Width
            && clientPoint.Y <= origin.Value.Y + control.Bounds.Height;
    }

    private MediaPlayer EnsureEmbeddedPlayer()
    {
        if (_mediaPlayer is null)
        {
            _mediaPlayer = new MediaPlayer(_libVlc);
        }

        if (VideoView.MediaPlayer != _mediaPlayer)
        {
            VideoView.MediaPlayer = _mediaPlayer;
        }

        return _mediaPlayer;
    }
}
