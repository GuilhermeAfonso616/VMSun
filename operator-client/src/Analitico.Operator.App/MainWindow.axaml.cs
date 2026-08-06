using System;
using System.Globalization;
using System.Linq;
using System.Windows.Input;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Platform;
using Avalonia.Threading;
using Avalonia.VisualTree;
using Analitico.Operator.App.Controls;
using Analitico.Operator.App.Services;
using Analitico.Operator.App.ViewModels;

namespace Analitico.Operator.App;

public partial class MainWindow : Window
{
    private static readonly DataFormat<string> CameraDragFormat =
        DataFormat.CreateStringApplicationFormat("analitico.operator.camera-id");
    private static readonly DataFormat<string> SourceSlotDragFormat =
        DataFormat.CreateStringApplicationFormat("analitico.operator.source-slot");
    private const double DragStartThreshold = 5;
    private readonly MainWindowViewModel _viewModel = new();
    private readonly LowLevelMouseHook _globalMouseHook = new();
    private readonly DragGhostWindow _dragGhostWindow = new();
    private CameraTileViewModel? _pendingDragCamera;
    private int? _pendingDragSourceSlot;
    private Point _pendingDragStart;
    private IPointer? _pendingDragPointer;
    private Control? _pendingDragCaptureTarget;
    private bool _pendingDragInProgress;
    private GlobalVideoDragState? _globalVideoDragState;

    public MainWindow()
    {
        AppLogger.Info("MainWindow ctor iniciado.");
        InitializeComponent();
        Icon = CreateWindowIcon();
        DataContext = _viewModel;
        KeyDown += OnWindowKeyDown;
        _globalMouseHook.LeftButtonDown += OnGlobalMouseLeftButtonDown;
        _globalMouseHook.MouseMove += OnGlobalMouseMove;
        _globalMouseHook.LeftButtonUp += OnGlobalMouseLeftButtonUp;
        Opened += (_, _) =>
        {
            AppLogger.Info("MainWindow aberta.");
            StartGlobalMouseHook();
            _viewModel.ConnectOnStartupIfEnabled();
        };
        Closing += (_, _) => AppLogger.Info("MainWindow fechando.");
        Closed += (_, _) =>
        {
            AppLogger.Info("MainWindow fechada; descartando ViewModel.");
            _globalMouseHook.Dispose();
            _dragGhostWindow.Close();
            _viewModel.Dispose();
        };
    }

    private static WindowIcon CreateWindowIcon()
    {
        using var stream = AssetLoader.Open(new Uri("avares://Analitico.Operator.App/Assets/sunorus-logo.png"));
        return new WindowIcon(stream);
    }

    private void OnSlotPointerPressed(object? sender, PointerPressedEventArgs e)
    {
        ClearPendingSlotDrag();
        var slot = ResolveSlotFromPointer(sender, e.Source);
        if (slot is null)
        {
            AppLogger.Warn($"Pointer press ignorado: slot nao resolvido. sender={sender?.GetType().Name ?? "-"}; source={e.Source?.GetType().Name ?? "-"}");
            return;
        }

        slot.SelectCommand.Execute(null);

        if (IsInteractiveSource(e.Source))
        {
            AppLogger.Info($"Pointer press interativo ignorado para drag: slot={slot.SlotNumber}; source={e.Source?.GetType().Name ?? "-"}");
            return;
        }

        var point = e.GetCurrentPoint(this);
        if (!point.Properties.IsLeftButtonPressed || slot.Camera is null)
        {
            return;
        }

        var captureTarget = sender as Control ?? e.Source as Control ?? this;
        _pendingDragCamera = slot.Camera;
        _pendingDragSourceSlot = slot.SlotNumber;
        _pendingDragStart = point.Position;
        _pendingDragPointer = e.Pointer;
        _pendingDragCaptureTarget = captureTarget;
        _pendingDragInProgress = false;
        e.Pointer.Capture(captureTarget);
        AppLogger.Info($"Drag pendente: camera={slot.Camera.Camera.Id}; slot={slot.SlotNumber}; source={e.Source?.GetType().Name ?? "-"}; capture={captureTarget.GetType().Name}");
        e.Handled = true;
    }

    private async void OnSlotPointerMoved(object? sender, PointerEventArgs e)
    {
        if (_pendingDragCamera is null
            || _pendingDragPointer != e.Pointer
            || _pendingDragInProgress)
        {
            return;
        }

        var point = e.GetCurrentPoint(this);
        if (!point.Properties.IsLeftButtonPressed)
        {
            ClearPendingSlotDrag();
            return;
        }

        var delta = point.Position - _pendingDragStart;
        if (Math.Abs(delta.X) < DragStartThreshold && Math.Abs(delta.Y) < DragStartThreshold)
        {
            return;
        }

        _pendingDragInProgress = true;
        AppLogger.Info($"Drag confirmado por movimento: camera={_pendingDragCamera.Camera.Id}; origem={_pendingDragSourceSlot?.ToString(CultureInfo.InvariantCulture) ?? "-"}");
        try
        {
            e.Handled = true;
            await StartCameraDragAsync(_pendingDragCamera, _pendingDragSourceSlot, e);
        }
        finally
        {
            ClearPendingSlotDrag();
        }
    }

    private void OnSlotPointerReleased(object? sender, PointerReleasedEventArgs e)
    {
        ClearPendingSlotDrag();
    }

    private void OnSlotPointerCaptureLost(object? sender, PointerCaptureLostEventArgs e)
    {
        ClearPendingSlotDrag();
    }

    private void StartGlobalMouseHook()
    {
        try
        {
            _globalMouseHook.Start();
            AppLogger.Info("Hook global de mouse iniciado para drag sobre video.");
        }
        catch (Exception exc)
        {
            AppLogger.Error("Falha ao iniciar hook global de mouse.", exc);
            Program.LogException(exc);
        }
    }

    private void OnGlobalMouseLeftButtonDown(object? sender, LowLevelMouseEventArgs e)
    {
        Dispatcher.UIThread.Post(() =>
        {
            if (!IsActive)
            {
                return;
            }

            var hit = FindSlotHitAtScreenPoint(e.ScreenX, e.ScreenY);
            if (hit?.Slot.Camera is null || !IsVideoBodyHit(hit, e.ScreenX, e.ScreenY))
            {
                return;
            }

            _globalVideoDragState = new GlobalVideoDragState(
                hit.Slot.Camera.Camera.Id,
                hit.Slot.Camera.Camera.Name,
                hit.Slot.SlotNumber,
                new Point(e.ScreenX, e.ScreenY));
            AppLogger.Info($"Drag global pendente sobre video: camera={hit.Slot.Camera.Camera.Id}; origem={hit.Slot.SlotNumber}; screen={e.ScreenX},{e.ScreenY}");
        });
    }

    private void OnGlobalMouseMove(object? sender, LowLevelMouseEventArgs e)
    {
        Dispatcher.UIThread.Post(() =>
        {
            if (_globalVideoDragState is not { } state)
            {
                return;
            }

            if (state.IsDragging)
            {
                UpdateGlobalDragCue(state, e.ScreenX, e.ScreenY);
                return;
            }

            var delta = new Point(e.ScreenX, e.ScreenY) - state.StartScreen;
            if (Math.Abs(delta.X) < DragStartThreshold && Math.Abs(delta.Y) < DragStartThreshold)
            {
                return;
            }

            state.IsDragging = true;
            ShowGlobalDragCue(state, e.ScreenX, e.ScreenY);
            AppLogger.Info($"Drag global confirmado sobre video: camera={state.CameraId}; origem={state.SourceSlotNumber}; screen={e.ScreenX},{e.ScreenY}");
        });
    }

    private void OnGlobalMouseLeftButtonUp(object? sender, LowLevelMouseEventArgs e)
    {
        Dispatcher.UIThread.Post(async () =>
        {
            var state = _globalVideoDragState;
            _globalVideoDragState = null;
            HideGlobalDragCue();
            if (state is null)
            {
                return;
            }

            if (!state.IsDragging)
            {
                AppLogger.Info($"Drag global cancelado sem movimento: camera={state.CameraId}; origem={state.SourceSlotNumber}");
                return;
            }

            var targetSlot = FindSlotAtScreenPoint(e.ScreenX, e.ScreenY);
            if (targetSlot is null)
            {
                AppLogger.Warn($"Drop global de video sem destino: camera={state.CameraId}; origem={state.SourceSlotNumber}; screen={e.ScreenX},{e.ScreenY}");
                return;
            }

            if (targetSlot.SlotNumber == state.SourceSlotNumber)
            {
                AppLogger.Info($"Drop global sem alteracao: camera={state.CameraId}; slot={state.SourceSlotNumber}; screen={e.ScreenX},{e.ScreenY}");
                return;
            }

            try
            {
                AppLogger.Info($"Drop global do video: camera={state.CameraId}; origem={state.SourceSlotNumber}; destino={targetSlot.SlotNumber}; screen={e.ScreenX},{e.ScreenY}");
                await _viewModel.MoveOrAssignCameraToSlotAsync(state.CameraId, targetSlot, state.SourceSlotNumber);
                AppLogger.Info($"Drop global concluido: camera={state.CameraId}; origem={state.SourceSlotNumber}; destino={targetSlot.SlotNumber}");
            }
            catch (Exception exc)
            {
                AppLogger.Error("Excecao no drop global do video.", exc);
                Program.LogException(exc);
            }
        });
    }

    private void ShowGlobalDragCue(GlobalVideoDragState state, int screenX, int screenY)
    {
        _dragGhostWindow.SetText(
            "Movendo camera",
            $"{state.CameraName}  |  origem {state.SourceSlotNumber:00}",
            "Solte sobre outro slot");
        _dragGhostWindow.ShowOrMove(this, screenX, screenY);
        UpdateGlobalDragCue(state, screenX, screenY);
    }

    private void UpdateGlobalDragCue(GlobalVideoDragState state, int screenX, int screenY)
    {
        var targetSlot = FindSlotAtScreenPoint(screenX, screenY);
        var detail = targetSlot is null
            ? $"{state.CameraName}  |  solte sobre um slot"
            : targetSlot.SlotNumber == state.SourceSlotNumber
                ? $"{state.CameraName}  |  slot {targetSlot.SlotNumber:00} atual"
                : $"{state.CameraName}  |  destino {targetSlot.SlotNumber:00}";
        var hint = targetSlot is null
            ? "Solte sobre outro slot"
            : targetSlot.SlotNumber == state.SourceSlotNumber
                ? "Solte fora para cancelar"
                : "Solte para aplicar";

        _dragGhostWindow.SetText("Movendo camera", detail, hint);
        _dragGhostWindow.MoveNear(screenX, screenY);
    }

    private void HideGlobalDragCue()
    {
        _dragGhostWindow.HideGhost();
    }

    private void OnVideoNativeDragStarted(object? sender, NativeVideoDragEventArgs e)
    {
        var slot = ResolveSlotFromPointer(sender, null);
        if (slot?.Camera is null)
        {
            AppLogger.Warn($"Drag nativo sobre video ignorado: slot/camera nao resolvido. sender={sender?.GetType().Name ?? "-"}; screen={e.ScreenX},{e.ScreenY}");
            return;
        }

        AppLogger.Info($"Drag nativo iniciado sobre video: camera={slot.Camera.Camera.Id}; origem={slot.SlotNumber}; screen={e.ScreenX},{e.ScreenY}");
    }

    private async void OnVideoNativeDragCompleted(object? sender, NativeVideoDragEventArgs e)
    {
        var sourceSlot = ResolveSlotFromPointer(sender, null);
        if (sourceSlot?.Camera is null)
        {
            AppLogger.Warn($"Drop nativo sobre video ignorado: origem nao resolvida. sender={sender?.GetType().Name ?? "-"}; screen={e.ScreenX},{e.ScreenY}");
            return;
        }

        var targetSlot = FindSlotAtScreenPoint(e.ScreenX, e.ScreenY);
        if (targetSlot is null)
        {
            AppLogger.Warn($"Drop nativo sobre video sem destino: camera={sourceSlot.Camera.Camera.Id}; origem={sourceSlot.SlotNumber}; screen={e.ScreenX},{e.ScreenY}");
            return;
        }

        if (targetSlot.SlotNumber == sourceSlot.SlotNumber)
        {
            AppLogger.Info($"Drop nativo sem alteracao: camera={sourceSlot.Camera.Camera.Id}; slot={sourceSlot.SlotNumber}; screen={e.ScreenX},{e.ScreenY}");
            return;
        }

        try
        {
            var cameraId = sourceSlot.Camera.Camera.Id;
            AppLogger.Info($"Drop nativo do video: camera={cameraId}; origem={sourceSlot.SlotNumber}; destino={targetSlot.SlotNumber}; screen={e.ScreenX},{e.ScreenY}");
            await _viewModel.MoveOrAssignCameraToSlotAsync(cameraId, targetSlot, sourceSlot.SlotNumber);
            AppLogger.Info($"Drop nativo concluido: camera={cameraId}; origem={sourceSlot.SlotNumber}; destino={targetSlot.SlotNumber}");
        }
        catch (Exception exc)
        {
            AppLogger.Error("Excecao no drop nativo do video.", exc);
            Program.LogException(exc);
        }
    }

    private async void OnCameraDragStart(object? sender, PointerPressedEventArgs e)
    {
        if (sender is not Control { DataContext: CameraTileViewModel camera } || IsInteractiveSource(e.Source))
        {
            return;
        }

        var point = e.GetCurrentPoint(this);
        if (!point.Properties.IsLeftButtonPressed)
        {
            return;
        }

        e.Handled = true;
        await StartCameraDragAsync(camera, _viewModel.GetSlotNumberForCamera(camera.Camera.Id), e);
    }

    private async System.Threading.Tasks.Task StartCameraDragAsync(
        CameraTileViewModel camera,
        int? sourceSlotNumber,
        PointerEventArgs e)
    {
        var cameraId = camera.Camera.Id.ToString(CultureInfo.InvariantCulture);
        var textPayload = sourceSlotNumber is null
            ? cameraId
            : $"{cameraId}|{sourceSlotNumber.Value.ToString(CultureInfo.InvariantCulture)}";
        var data = new DataTransfer();
        data.Add(DataTransferItem.Create(CameraDragFormat, cameraId));
        data.Add(DataTransferItem.CreateText(textPayload));
        if (sourceSlotNumber is not null)
        {
            data.Add(DataTransferItem.Create(SourceSlotDragFormat, sourceSlotNumber.Value.ToString(CultureInfo.InvariantCulture)));
        }

        AppLogger.Info($"Drag iniciado: camera={camera.Camera.Id}; origem={sourceSlotNumber?.ToString(CultureInfo.InvariantCulture) ?? "biblioteca"}");
        try
        {
            await DragDrop.DoDragDropAsync(e, data, DragDropEffects.Move | DragDropEffects.Copy);
        }
        catch (Exception exc)
        {
            AppLogger.Error("Falha durante DragDrop.", exc);
            Program.LogException(exc);
        }
    }

    private async void OnSlotDrop(object? sender, DragEventArgs e)
    {
        if (sender is not Control { DataContext: CameraSlotViewModel slot })
        {
            return;
        }

        try
        {
            var rawText = e.DataTransfer.TryGetText();
            var raw = e.DataTransfer.TryGetValue<string>(CameraDragFormat) ?? rawText;
            var sourceSlotFromText = TryParseDragText(rawText, out var parsedCameraId, out var parsedSlotNumber)
                ? parsedSlotNumber
                : null;
            if (parsedCameraId is not null)
            {
                raw = parsedCameraId.Value.ToString(CultureInfo.InvariantCulture);
            }

            if (!int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var cameraId))
            {
                AppLogger.Warn($"Drop ignorado: payload invalido. raw={raw ?? "-"}");
                return;
            }

            var rawSourceSlot = e.DataTransfer.TryGetValue<string>(SourceSlotDragFormat);
            var sourceSlotNumber = int.TryParse(rawSourceSlot, NumberStyles.Integer, CultureInfo.InvariantCulture, out var slotNumber)
                ? slotNumber
                : sourceSlotFromText ?? _viewModel.GetSlotNumberForCamera(cameraId);

            AppLogger.Info($"Drop recebido: camera={cameraId}; origem={sourceSlotNumber?.ToString(CultureInfo.InvariantCulture) ?? "-"}; destino={slot.SlotNumber}");
            await _viewModel.MoveOrAssignCameraToSlotAsync(cameraId, slot, sourceSlotNumber);
            AppLogger.Info($"Drop concluido: camera={cameraId}; origem={sourceSlotNumber?.ToString(CultureInfo.InvariantCulture) ?? "-"}; destino={slot.SlotNumber}");
            e.DragEffects = sourceSlotNumber is null ? DragDropEffects.Copy : DragDropEffects.Move;
            e.Handled = true;
        }
        catch (Exception exc)
        {
            AppLogger.Error("Excecao no handler de drop do mosaico.", exc);
            Program.LogException(exc);
            e.Handled = true;
        }
    }

    private void OnSlotDragOver(object? sender, DragEventArgs e)
    {
        e.DragEffects = e.DataTransfer.TryGetValue<string>(CameraDragFormat) is not null
            ? DragDropEffects.Move | DragDropEffects.Copy
            : DragDropEffects.None;
        e.Handled = true;
    }

    private void OnWindowKeyDown(object? sender, KeyEventArgs e)
    {
        if (DataContext is not MainWindowViewModel viewModel)
        {
            return;
        }

        if (!viewModel.KeyboardShortcutsActive)
        {
            if (e.Key == Key.Escape && viewModel.IsSettingsOpen)
            {
                ExecuteCommand(viewModel.CloseSettingsCommand);
                e.Handled = true;
            }

            return;
        }

        if (e.Source is Control focusedControl && focusedControl.FindAncestorOfType<TextBox>() is not null)
        {
            return;
        }

        // Handle Alt + 1-9 for Spotlight focus on specific slots
        if ((e.KeyModifiers & KeyModifiers.Alt) != 0)
        {
            int slotIndex = e.Key switch
            {
                Key.D1 or Key.NumPad1 => 0,
                Key.D2 or Key.NumPad2 => 1,
                Key.D3 or Key.NumPad3 => 2,
                Key.D4 or Key.NumPad4 => 3,
                Key.D5 or Key.NumPad5 => 4,
                Key.D6 or Key.NumPad6 => 5,
                Key.D7 or Key.NumPad7 => 6,
                Key.D8 or Key.NumPad8 => 7,
                Key.D9 or Key.NumPad9 => 8,
                _ => -1
            };

            if (slotIndex >= 0 && slotIndex < viewModel.MosaicSlots.Count)
            {
                var slot = viewModel.MosaicSlots[slotIndex];
                if (slot != null && slot.Camera != null)
                {
                    ExecuteCommand(slot.FocusCommand);
                    e.Handled = true;
                    return;
                }
            }
        }

        if (e.Key == Key.Delete)
        {
            var selectedSlot = viewModel.MosaicSlots.FirstOrDefault(s => s.IsSelected);
            if (selectedSlot != null && selectedSlot.ClearCommand != null)
            {
                ExecuteCommand(selectedSlot.ClearCommand);
                e.Handled = true;
                return;
            }
        }

        if (e.Key == Key.S)
        {
            viewModel.SettingsAlarmPopupEnabled = !viewModel.SettingsAlarmPopupEnabled;
            e.Handled = true;
            return;
        }

        if (TryHandleGridShortcut(e.Key, viewModel))
        {
            e.Handled = true;
            return;
        }

        var command = e.Key switch
        {
            Key.F => viewModel.ToggleFocusCommand,
            Key.B => viewModel.ToggleBoxesCommand,
            Key.H => viewModel.ToggleHideOfflineCommand,
            Key.Space => viewModel.ToggleSequenceCommand,
            Key.F5 => viewModel.RefreshCommand,
            Key.Escape when viewModel.IsMosaicFullscreen => viewModel.ToggleMosaicFullscreenCommand,
            Key.Escape => viewModel.IsFocusMode ? viewModel.ToggleFocusCommand : null,
            _ => null,
        };

        if (e.Key == Key.F11)
        {
            ToggleFullscreen();
            e.Handled = true;
            return;
        }

        if (command is null)
        {
            return;
        }

        ExecuteCommand(command);
        e.Handled = true;
    }

    private static bool TryHandleGridShortcut(Key key, MainWindowViewModel viewModel)
    {
        var grid = key switch
        {
            Key.D1 or Key.NumPad1 => "1",
            Key.D2 or Key.NumPad2 => "2",
            Key.D4 or Key.NumPad4 => "4",
            Key.D6 or Key.NumPad6 => "6",
            Key.D8 or Key.NumPad8 => "8",
            Key.D9 or Key.NumPad9 => "9",
            _ => null,
        };

        if (grid is null)
        {
            return false;
        }

        ExecuteCommand(viewModel.SetGridCommand, grid);
        return true;
    }

    private void OnMosaicHostSizeChanged(object? sender, SizeChangedEventArgs e)
    {
        _viewModel.UpdateViewportSize(e.NewSize.Width, e.NewSize.Height);
    }

    private void OnFullscreenClick(object? sender, Avalonia.Interactivity.RoutedEventArgs e)
    {
        ToggleFullscreen();
        e.Handled = true;
    }

    private void ToggleFullscreen()
    {
        WindowState = WindowState == WindowState.FullScreen
            ? WindowState.Maximized
            : WindowState.FullScreen;
    }

    private static void ExecuteCommand(ICommand command)
    {
        ExecuteCommand(command, null);
    }

    private static void ExecuteCommand(ICommand command, object? parameter)
    {
        if (command.CanExecute(parameter))
        {
            command.Execute(parameter);
        }
    }

    private static bool IsInteractiveSource(object? source)
    {
        if (source is not Control control)
        {
            return false;
        }

        return control is Button
            || control is TextBox
            || control.FindAncestorOfType<Button>() is not null
            || control.FindAncestorOfType<TextBox>() is not null;
    }

    private static CameraSlotViewModel? ResolveSlotFromPointer(object? sender, object? source)
    {
        if (sender is Control { DataContext: CameraSlotViewModel senderSlot })
        {
            return senderSlot;
        }

        var control = source as Control;
        while (control is not null)
        {
            if (control.DataContext is CameraSlotViewModel slot)
            {
                return slot;
            }

            control = control.Parent as Control;
        }

        return null;
    }

    private CameraSlotViewModel? FindSlotAtScreenPoint(int screenX, int screenY)
    {
        return FindSlotHitAtScreenPoint(screenX, screenY)?.Slot;
    }

    private SlotHit? FindSlotHitAtScreenPoint(int screenX, int screenY)
    {
        var clientPoint = ScreenToWindowPoint(screenX, screenY);
        return this.GetVisualDescendants()
            .OfType<Control>()
            .Select(control => new
            {
                Control = control,
                Slot = control.DataContext as CameraSlotViewModel,
                Origin = control.TranslatePoint(new Point(0, 0), this),
            })
            .Where(item => item.Slot is not null
                && item.Origin is not null
                && item.Control.Bounds.Width > 0
                && item.Control.Bounds.Height > 0)
            .Where(item =>
                clientPoint.X >= item.Origin!.Value.X
                && clientPoint.Y >= item.Origin.Value.Y
                && clientPoint.X <= item.Origin.Value.X + item.Control.Bounds.Width
                && clientPoint.Y <= item.Origin.Value.Y + item.Control.Bounds.Height)
            .OrderByDescending(item => item.Control.Bounds.Width * item.Control.Bounds.Height)
            .Select(item => new SlotHit(
                item.Slot!,
                item.Origin!.Value,
                new Size(item.Control.Bounds.Width, item.Control.Bounds.Height)))
            .FirstOrDefault();
    }

    private bool IsVideoBodyHit(SlotHit hit, int screenX, int screenY)
    {
        var clientPoint = ScreenToWindowPoint(screenX, screenY);
        var top = hit.Origin.Y + 22;
        var bottom = hit.Origin.Y + Math.Max(22, hit.Size.Height - 30);
        return clientPoint.Y >= top && clientPoint.Y <= bottom;
    }

    private Point ScreenToWindowPoint(int screenX, int screenY)
    {
        var scaling = RenderScaling <= 0 ? 1 : RenderScaling;
        return new Point(
            (screenX - Position.X) / scaling,
            (screenY - Position.Y) / scaling);
    }

    private static bool TryParseDragText(string? raw, out int? cameraId, out int? sourceSlotNumber)
    {
        cameraId = null;
        sourceSlotNumber = null;

        if (string.IsNullOrWhiteSpace(raw))
        {
            return false;
        }

        var parts = raw.Split('|', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length == 0
            || !int.TryParse(parts[0], NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsedCameraId))
        {
            return false;
        }

        cameraId = parsedCameraId;
        if (parts.Length > 1
            && int.TryParse(parts[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsedSlot))
        {
            sourceSlotNumber = parsedSlot;
        }

        return true;
    }

    private void ClearPendingSlotDrag()
    {
        if (_pendingDragPointer is not null && _pendingDragCaptureTarget is not null)
        {
            _pendingDragPointer.Capture(null);
        }

        _pendingDragCamera = null;
        _pendingDragSourceSlot = null;
        _pendingDragPointer = null;
        _pendingDragCaptureTarget = null;
        _pendingDragInProgress = false;
    }

    private sealed record SlotHit(CameraSlotViewModel Slot, Point Origin, Size Size);

    private void OnLogoutClick(object? sender, Avalonia.Interactivity.RoutedEventArgs e)
    {
        AppLogger.Info("Logout solicitado pelo operador.");
        Session.Token = null;
        Session.Username = null;
        Session.UserRole = null;
        Session.UserName = null;

        // Logout explicito: esquece credenciais salvas para nao reconectar sozinho
        _viewModel.ClearRememberedCredentials();

        // Troca de janela adiada para fora do processamento do clique
        Dispatcher.UIThread.Post(() =>
        {
            var loginWin = new LoginWindow(suppressAutoLogin: true);
            if (Application.Current?.ApplicationLifetime is Avalonia.Controls.ApplicationLifetimes.IClassicDesktopStyleApplicationLifetime desktop)
            {
                desktop.MainWindow = loginWin;
            }
            loginWin.Show();
            Close();
        });
    }

    private sealed class GlobalVideoDragState
    {
        public GlobalVideoDragState(int cameraId, string cameraName, int sourceSlotNumber, Point startScreen)
        {
            CameraId = cameraId;
            CameraName = cameraName;
            SourceSlotNumber = sourceSlotNumber;
            StartScreen = startScreen;
        }

        public int CameraId { get; }

        public string CameraName { get; }

        public int SourceSlotNumber { get; }

        public Point StartScreen { get; }

        public bool IsDragging { get; set; }
    }
}
