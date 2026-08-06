using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Input;
using Microsoft.Win32;
using Avalonia.Threading;
using Avalonia.Media.Imaging;
using LibVLCSharp.Shared;
using Analitico.Operator.App.Models;
using Analitico.Operator.App.Services;

namespace Analitico.Operator.App.ViewModels;

public sealed class MainWindowViewModel : ObservableObject, IDisposable
{
    private static readonly JsonSerializerOptions PersistJsonOptions = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private static readonly string DefaultServerUrl = "http://192.168.2.62:8000";

    private readonly AnaliticoApiClient _apiClient = new();
    private readonly SemaphoreSlim _mosaicLayoutLock = new(1, 1);
    private bool _settingsLoaded;
    private bool _isRestoringSettings;
    private CancellationTokenSource? _loadCancellation;
    private CancellationTokenSource? _playbackStartCancellation;
    private CancellationTokenSource? _mosaicReconcileCancellation;
    private CancellationTokenSource? _refreshCancellation;
    private CancellationTokenSource? _trackCancellation;
    private CancellationTokenSource? _sequenceCancellation;
    private CancellationTokenSource? _performanceCancellation;
    private CancellationTokenSource? _alarmPopupAutoCloseCancellation;
    private readonly HashSet<int> _announcedAlarmPopupEventIds = new();
    private readonly HashSet<int> _dismissedEventIds = new();
    private string _serverUrl = DefaultServerUrl;
    private string _statusMessage = "Informe o servidor e conecte.";
    private string _searchText = "";
    private string _libraryFilter = "all";
    private string _sidePanelMode = "library";
    private string _currentViewName = "Monitor 1";
    private string? _serverVersion;
    private string? _serverReleaseTag;
    private string? _serverCommit;
    private string? _recommendedOperatorClientVersion;
    private int? _operatorApiVersion;
    private bool _serverVersionChecked;
    private string? _serverBuildSignature;
    private bool _isBusy;
    private bool _hideOfflineEnabled;
    private bool _boxesEnabled = true;
    private bool _spotlightEnabled = true;
    private bool _alarmPopupEnabled = true;
    private OperatorEventViewModel? _alarmPopupEvent;
    private bool _isSequencing;
    private bool _isFocusMode;
    private bool _isMosaicFullscreen;
    private bool _isRearrangingMosaic;
    private bool _sidePanelExpanded = true;
    private bool _isSettingsOpen;
    private bool _isViewManagerOpen;
    private bool _isViewActivationConfirmationOpen;
    private string _viewPresetNameDraft = "";
    private ViewPresetViewModel? _selectedSavedView;
    private ViewPresetViewModel? _pendingViewPreset;
    private bool _autoConnectOnStartup;
    private bool _restoreLastLayout = true;
    private int _sequenceIntervalSeconds = 8;
    private bool _sequenceSavedViews;
    private int _boxRefreshIntervalMs = 200;
    private int _minimumBoxConfidencePercent = 40;
    private int _gridSize = 16;
    private int _focusBackupGrid = 16;
    private List<int?> _focusBackupCameraIds = new();
    private CameraTileViewModel? _selectedCamera;
    private CameraSlotViewModel? _selectedSlot;
    private DashboardMetricsResponse? _metrics;
    private DashboardEventsResponse? _events;
    private HealthCamerasResponse? _health;
    private DateTimeOffset? _lastRefreshAt;
    private DateTimeOffset? _lastPerformanceSampleAt;
    private DateTimeOffset? _lastPerformanceUploadAt;
    private TimeSpan _lastProcessCpuTime;
    private double _operatorCpuPercent;
    private double _operatorRamMb;
    private bool _performanceLogUploadEnabled = true;
    private string _performanceUploadStatus = "Aguardando conexao";
    private string _driveTokenInput = "";
    private string _driveTokenStatusText = "Drive: aguardando token";
    private double _viewportWidth = 1280;
    private double _viewportHeight = 720;
    private int _gridColumns = 4;
    private int _gridRows = 4;
    private List<int?> _startupCameraIds = new();
    private bool? _rememberMe;
    private string? _username;
    private string? _password;

    public MainWindowViewModel()
    {
        LoadAppSettings();
        if (!string.IsNullOrEmpty(Session.Token))
        {
            _apiClient.AuthToken = Session.Token;
        }
        if (!string.IsNullOrEmpty(Session.ServerUrl))
        {
            ServerUrl = Session.ServerUrl;
        }
        LoadCommand = new RelayCommand(_ => _ = LoadAsync(), _ => !IsBusy);
        RefreshCommand = new RelayCommand(_ => _ = RefreshOperationalDataAsync(isPolling: false), _ => !IsBusy && Cameras.Count > 0);
        StopAllCommand = new RelayCommand(_ => StopAll());
        ReconnectSelectedPlayerCommand = new RelayCommand(_ => _ = ReconnectSelectedPlayerAsync(), _ => SelectedCamera is not null && !IsBusy);
        StartSelectedIaCommand = new RelayCommand(_ => _ = StartSelectedIaAsync(), _ => SelectedCamera is not null && !IsBusy);
        StopSelectedIaCommand = new RelayCommand(_ => _ = StopSelectedIaAsync(), _ => SelectedCamera is not null && !IsBusy);
        SetGridCommand = new RelayCommand(SetGrid);
        SetLibraryFilterCommand = new RelayCommand(SetLibraryFilter);
        SetSidePanelCommand = new RelayCommand(SetSidePanel);
        ToggleSidePanelCommand = new RelayCommand(_ => ToggleSidePanel());
        ToggleHideOfflineCommand = new RelayCommand(_ => ToggleHideOffline());
        ToggleBoxesCommand = new RelayCommand(_ => ToggleBoxes());
        ToggleSpotlightCommand = new RelayCommand(_ => ToggleSpotlight());
        PinSpotlightCameraCommand = new RelayCommand(_ => PinSpotlightCamera(), _ => SpotlightAlarm is not null);
        CloseAlarmPopupCommand = new RelayCommand(_ => CloseAlarmPopup());
        ClearAlarmPopupQueueCommand = new RelayCommand(_ => ClearAlarmPopupQueue());
        PinAlarmPopupCameraCommand = new RelayCommand(_ => PinAlarmPopupCamera(), _ => AlarmPopupEvent is not null);
        OpenEvidenceInBrowserCommand = new RelayCommand(_ => OpenEvidenceInBrowser());
        CloseEvidenceModalCommand = new RelayCommand(_ => CloseEvidenceModal());
        AutoFillCommand = new RelayCommand(_ => AutoFillMosaic(autoStart: true), _ => Cameras.Count > 0);
        ClearMosaicCommand = new RelayCommand(_ => ClearMosaic());
        SaveViewCommand = new RelayCommand(_ => OpenViewManager(), _ => Cameras.Count > 0);
        CloseViewManagerCommand = new RelayCommand(_ => CloseViewManager());
        RefreshViewPresetsCommand = new RelayCommand(_ => _ = RefreshViewPresetsAsync());
        ConfirmSaveViewCommand = new RelayCommand(_ => _ = SaveCurrentViewAsync());
        RequestActivateViewCommand = new RelayCommand(_ => RequestActivateSelectedView());
        ConfirmActivateViewCommand = new RelayCommand(_ => ConfirmActivateView());
        CancelActivateViewCommand = new RelayCommand(_ => IsViewActivationConfirmationOpen = false);
        ToggleSequenceCommand = new RelayCommand(_ => ToggleSequence(), _ => Cameras.Count > 0);
        ToggleFocusCommand = new RelayCommand(_ => ToggleFocusMode(), _ => SelectedCamera is not null || MosaicSlots.Any(slot => slot.HasCamera));
        ToggleMosaicFullscreenCommand = new RelayCommand(_ => ToggleMosaicFullscreen());
        CollectDiagnosticsCommand = new RelayCommand(_ => _ = CollectDiagnosticsAsync(), _ => !IsBusy);
        SaveDriveTokenCommand = new RelayCommand(_ => _ = SaveDriveTokenAsync(), _ => !IsBusy);
        UploadReviewedEventsToDriveCommand = new RelayCommand(_ => _ = UploadReviewedEventsToDriveAsync(), _ => !IsBusy);
        OpenLogsFolderCommand = new RelayCommand(_ => OpenLogsFolder());
        ResetOperationalPreferencesCommand = new RelayCommand(_ => ResetOperationalPreferences());
        OpenSettingsCommand = new RelayCommand(_ => IsSettingsOpen = true);
        CloseSettingsCommand = new RelayCommand(_ => IsSettingsOpen = false);
        ToggleLibraryViewCommand = new RelayCommand(_ => ShowLibraryAsTree = !ShowLibraryAsTree);
        ExportBackupCommand = new RelayCommand(async _ => await ExportBackupAsync(), _ => Session.UserRole == "admin");
        ImportBackupCommand = new RelayCommand(async _ => await ImportBackupAsync(), _ => Session.UserRole == "admin");
        LoadSavedViews();
        EnsureSlots(_gridSize);
        _settingsLoaded = true;
        StartPerformanceLoop();
    }

    public ObservableCollection<CameraTileViewModel> Cameras { get; } = new();

    public ObservableCollection<CameraSlotViewModel> MosaicSlots { get; } = new();

    public ObservableCollection<CameraTileViewModel> VisibleCameras { get; } = new();

    public ObservableCollection<CameraTileViewModel> LibraryCameras { get; } = new();

    public ObservableCollection<ViewPresetViewModel> SavedViews { get; } = new();

    public ObservableCollection<OperatorEventViewModel> OpenEvents { get; } = new();

    public ObservableCollection<OperatorEventViewModel> RecentEvents { get; } = new();

    public ObservableCollection<CameraHealthViewModel> HealthItems { get; } = new();

    public ObservableCollection<StatusMetricViewModel> StatusCards { get; } = new();

    public ICommand LoadCommand { get; }

    public ICommand RefreshCommand { get; }

    public ICommand StopAllCommand { get; }

    public ICommand ReconnectSelectedPlayerCommand { get; }

    public ICommand StartSelectedIaCommand { get; }

    public ICommand StopSelectedIaCommand { get; }

    public ICommand SetGridCommand { get; }

    public ICommand SetLibraryFilterCommand { get; }

    public ICommand SetSidePanelCommand { get; }

    public ICommand ToggleSidePanelCommand { get; }

    public ICommand ToggleHideOfflineCommand { get; }

    public ICommand ToggleBoxesCommand { get; }

    public ICommand ToggleSpotlightCommand { get; }

    public ICommand PinSpotlightCameraCommand { get; }

    public ICommand CloseAlarmPopupCommand { get; }

    public ICommand ClearAlarmPopupQueueCommand { get; }

    public ICommand PinAlarmPopupCameraCommand { get; }

    public ICommand OpenEvidenceInBrowserCommand { get; }

    public ICommand CloseEvidenceModalCommand { get; }


    public ICommand AutoFillCommand { get; }

    public ICommand ClearMosaicCommand { get; }

    public ICommand SaveViewCommand { get; }

    public ICommand CloseViewManagerCommand { get; }

    public ICommand RefreshViewPresetsCommand { get; }

    public ICommand ConfirmSaveViewCommand { get; }

    public ICommand RequestActivateViewCommand { get; }

    public ICommand ConfirmActivateViewCommand { get; }

    public ICommand CancelActivateViewCommand { get; }

    public ICommand ToggleSequenceCommand { get; }

    public ICommand ToggleFocusCommand { get; }

    public ICommand ToggleMosaicFullscreenCommand { get; }

    public ICommand CollectDiagnosticsCommand { get; }

    public ICommand SaveDriveTokenCommand { get; }

    public ICommand UploadReviewedEventsToDriveCommand { get; }

    public ICommand OpenLogsFolderCommand { get; }

    public ICommand ResetOperationalPreferencesCommand { get; }

    public ICommand OpenSettingsCommand { get; }

    public ICommand CloseSettingsCommand { get; }
    public ICommand ToggleLibraryViewCommand { get; }

    public string ServerUrl
    {
        get => _serverUrl;
        set
        {
            if (SetProperty(ref _serverUrl, value))
            {
                SaveAppSettings();
            }
        }
    }

    public string StatusMessage
    {
        get => _statusMessage;
        private set
        {
            if (SetProperty(ref _statusMessage, value))
            {
                OnPropertyChanged(nameof(OperatorHeadline));
            }
        }
    }

    public string SearchText
    {
        get => _searchText;
        set
        {
            if (SetProperty(ref _searchText, value))
            {
                RefreshLibrary();
            }
        }
    }

    public string CurrentViewName
    {
        get => _currentViewName;
        set
        {
            if (SetProperty(ref _currentViewName, value))
            {
                SaveAppSettings();
            }
        }
    }

    public string LibraryFilter
    {
        get => _libraryFilter;
        private set
        {
            if (SetProperty(ref _libraryFilter, value))
            {
                OnPropertyChanged(nameof(AllFilterSelected));
                OnPropertyChanged(nameof(VisibleFilterSelected));
                OnPropertyChanged(nameof(OfflineFilterSelected));
                OnPropertyChanged(nameof(RunningFilterSelected));
                OnPropertyChanged(nameof(AlarmFilterSelected));
            }
        }
    }

    public string SidePanelMode
    {
        get => _sidePanelMode;
        private set
        {
            if (SetProperty(ref _sidePanelMode, value))
            {
                OnPropertyChanged(nameof(LibraryPanelSelected));
                OnPropertyChanged(nameof(AlarmsPanelSelected));
                OnPropertyChanged(nameof(DiagnosticsPanelSelected));
                OnPropertyChanged(nameof(KeyboardShortcutsActive));
                OnPropertyChanged(nameof(ShortcutFooterText));
            }
        }
    }

    public int GridSize
    {
        get => _gridSize;
        private set
        {
            if (SetProperty(ref _gridSize, value))
            {
                OnPropertyChanged(nameof(Grid1Selected));
                OnPropertyChanged(nameof(Grid2Selected));
                OnPropertyChanged(nameof(Grid4Selected));
                OnPropertyChanged(nameof(Grid6Selected));
                OnPropertyChanged(nameof(Grid8Selected));
                OnPropertyChanged(nameof(Grid9Selected));
                OnPropertyChanged(nameof(Grid12Selected));
                OnPropertyChanged(nameof(Grid16Selected));
                OnPropertyChanged(nameof(Grid25Selected));
                OnPropertyChanged(nameof(CurrentMosaicSummary));
            }
        }
    }

    public bool Grid1Selected => GridSize == 1;
    public bool Grid2Selected => GridSize == 2;
    public bool Grid4Selected => GridSize == 4;
    public bool Grid6Selected => GridSize == 6;
    public bool Grid8Selected => GridSize == 8;
    public bool Grid9Selected => GridSize == 9;
    public bool Grid12Selected => GridSize == 12;
    public bool Grid16Selected => GridSize == 16;
    public bool Grid25Selected => GridSize == 25;
    public bool IsAdmin => Session.UserRole == "admin";
    public bool IsAdminOrSupervisor => Session.UserRole == "admin" || Session.UserRole == "supervisor";
    public bool IsNotViewer => Session.UserRole != "viewer";

    public bool IsSettingsOpen
    {
        get => _isSettingsOpen;
        private set
        {
            if (SetProperty(ref _isSettingsOpen, value))
            {
                foreach (var slot in MosaicSlots)
                {
                    slot.VideoSuppressed = value;
                }

                OnPropertyChanged(nameof(SettingsPanelSelected));
                OnPropertyChanged(nameof(KeyboardShortcutsActive));
                OnPropertyChanged(nameof(ShortcutFooterText));
            }
        }
    }

    public bool IsViewManagerOpen
    {
        get => _isViewManagerOpen;
        private set
        {
            if (SetProperty(ref _isViewManagerOpen, value))
            {
                OnPropertyChanged(nameof(KeyboardShortcutsActive));
                OnPropertyChanged(nameof(ShortcutFooterText));
            }
        }
    }

    public bool IsViewActivationConfirmationOpen
    {
        get => _isViewActivationConfirmationOpen;
        private set => SetProperty(ref _isViewActivationConfirmationOpen, value);
    }

    public string ViewPresetNameDraft
    {
        get => _viewPresetNameDraft;
        set
        {
            if (SetProperty(ref _viewPresetNameDraft, value))
            {
                OnPropertyChanged(nameof(CanSaveViewPreset));
            }
        }
    }

    public ViewPresetViewModel? SelectedSavedView
    {
        get => _selectedSavedView;
        set
        {
            if (SetProperty(ref _selectedSavedView, value))
            {
                OnPropertyChanged(nameof(CanActivateSelectedView));
                OnPropertyChanged(nameof(SelectedSavedViewSummary));
            }
        }
    }

    public ViewPresetViewModel? PendingViewPreset
    {
        get => _pendingViewPreset;
        private set
        {
            if (SetProperty(ref _pendingViewPreset, value))
            {
                OnPropertyChanged(nameof(PendingViewPresetSummary));
            }
        }
    }

    public bool CanSaveViewPreset => !string.IsNullOrWhiteSpace(ViewPresetNameDraft) && MosaicSlots.Any(slot => slot.Camera is not null);

    public bool CanActivateSelectedView => SelectedSavedView is not null;

    public string SelectedSavedViewSummary => SelectedSavedView?.Summary ?? "Selecione uma visao para ativar.";

    public string PendingViewPresetSummary => PendingViewPreset?.Summary ?? "";

    public string CurrentMosaicSummary => $"{GridSize} slots | {DisplayedCameraCount} cameras no mosaico";

    public bool AutoConnectOnStartup
    {
        get => _autoConnectOnStartup;
        set
        {
            if (SetProperty(ref _autoConnectOnStartup, value))
            {
                SaveAppSettings();
            }
        }
    }

    public bool RestoreLastLayout
    {
        get => _restoreLastLayout;
        set
        {
            if (SetProperty(ref _restoreLastLayout, value))
            {
                SaveAppSettings();
            }
        }
    }

    public int SequenceIntervalSeconds
    {
        get => _sequenceIntervalSeconds;
        set
        {
            var next = Math.Clamp(value, 3, 60);
            if (SetProperty(ref _sequenceIntervalSeconds, next))
            {
                SaveAppSettings();
            }
        }
    }

    public bool SequenceSavedViews
    {
        get => _sequenceSavedViews;
        set
        {
            if (SetProperty(ref _sequenceSavedViews, value))
            {
                SaveAppSettings();
            }
        }
    }

    public int BoxRefreshIntervalMs
    {
        get => _boxRefreshIntervalMs;
        set
        {
            var next = Math.Clamp(value, 100, 2000);
            if (SetProperty(ref _boxRefreshIntervalMs, next))
            {
                SaveAppSettings();
                RestartTrackLoop();
            }
        }
    }

    public int MinimumBoxConfidencePercent
    {
        get => _minimumBoxConfidencePercent;
        set
        {
            var next = Math.Clamp(value, 0, 100);
            if (SetProperty(ref _minimumBoxConfidencePercent, next))
            {
                ApplyMinimumBoxConfidence();
                SaveAppSettings();
            }
        }
    }

    public bool SettingsHideOfflineEnabled
    {
        get => HideOfflineEnabled;
        set
        {
            if (value != HideOfflineEnabled)
            {
                ToggleHideOffline();
                OnPropertyChanged();
            }
        }
    }

    public bool SettingsBoxesEnabled
    {
        get => BoxesEnabled;
        set
        {
            if (value != BoxesEnabled)
            {
                ToggleBoxes();
                OnPropertyChanged();
            }
        }
    }

    public bool SettingsSpotlightEnabled
    {
        get => SpotlightEnabled;
        set
        {
            if (value != SpotlightEnabled)
            {
                SpotlightEnabled = value;
                StatusMessage = value
                    ? "Spotlight de alarme ativado"
                    : "Spotlight de alarme desativado";
                OnPropertyChanged();
            }
        }
    }

    public bool SettingsAlarmPopupEnabled
    {
        get => AlarmPopupEnabled;
        set
        {
            if (value != AlarmPopupEnabled)
            {
                AlarmPopupEnabled = value;
                StatusMessage = value
                    ? "Popup central de alarmes ativado"
                    : "Popup central de alarmes desativado";
                OnPropertyChanged();
            }
        }
    }

    public bool SettingsPerformanceLogUploadEnabled
    {
        get => PerformanceLogUploadEnabled;
        set
        {
            if (value != PerformanceLogUploadEnabled)
            {
                PerformanceLogUploadEnabled = value;
                OnPropertyChanged();
            }
        }
    }

    public bool PerformanceLogUploadEnabled
    {
        get => _performanceLogUploadEnabled;
        set
        {
            if (SetProperty(ref _performanceLogUploadEnabled, value))
            {
                if (value)
                {
                    _lastPerformanceUploadAt = null;
                }

                SaveAppSettings();
                OnPropertyChanged(nameof(SettingsPerformanceLogUploadEnabled));
                OnPropertyChanged(nameof(PerformanceUploadStatusText));
            }
        }
    }

    private bool _isEvidenceModalOpen;
    private string _evidenceModalTitle = "";
    private string _evidenceModalSubtitle = "";
    private Bitmap? _evidenceImage;
    private bool _isEvidenceImageVisible;
    private bool _isEvidenceVideoVisible;
    private bool _isEvidenceVideoLoading;
    private MediaPlayer? _evidenceMediaPlayer;
    private Uri? _lastEvidenceUri;
    private CancellationTokenSource? _evidenceCancellation;

    public bool IsEvidenceModalOpen
    {
        get => _isEvidenceModalOpen;
        private set => SetProperty(ref _isEvidenceModalOpen, value);
    }

    public string EvidenceModalTitle
    {
        get => _evidenceModalTitle;
        private set => SetProperty(ref _evidenceModalTitle, value);
    }

    public string EvidenceModalSubtitle
    {
        get => _evidenceModalSubtitle;
        private set => SetProperty(ref _evidenceModalSubtitle, value);
    }

    public Bitmap? EvidenceImage
    {
        get => _evidenceImage;
        private set => SetProperty(ref _evidenceImage, value);
    }

    public bool IsEvidenceImageVisible
    {
        get => _isEvidenceImageVisible;
        private set => SetProperty(ref _isEvidenceImageVisible, value);
    }

    public bool IsEvidenceVideoVisible
    {
        get => _isEvidenceVideoVisible;
        private set => SetProperty(ref _isEvidenceVideoVisible, value);
    }

    public bool IsEvidenceVideoLoading
    {
        get => _isEvidenceVideoLoading;
        private set => SetProperty(ref _isEvidenceVideoLoading, value);
    }

    public MediaPlayer? EvidenceMediaPlayer
    {
        get => _evidenceMediaPlayer;
        private set => SetProperty(ref _evidenceMediaPlayer, value);
    }

    public bool KeyboardShortcutsActive => !IsSettingsOpen && !IsViewManagerOpen && !IsViewActivationConfirmationOpen;

    public string ShortcutFooterText => KeyboardShortcutsActive
        ? "Atalhos: 1/2/4/6/8/9 mosaico | Esc sair foco/mosaico cheio | F11 janela | F foco | B boxes | H ocultar offline | Espaco sequencia"
        : "Atalhos operacionais desativados enquanto Configuracoes estiver aberta";

    public CameraTileViewModel? SelectedCamera
    {
        get => _selectedCamera;
        private set
        {
            if (_selectedCamera == value)
            {
                return;
            }

            if (_selectedCamera is not null)
            {
                _selectedCamera.IsSelected = false;
            }

            _selectedCamera = value;
            if (_selectedCamera is not null)
            {
                _selectedCamera.IsSelected = true;
            }

            OnPropertyChanged();
            OnPropertyChanged(nameof(HasSelectedCamera));
            OnPropertyChanged(nameof(SelectedCameraTitle));
            OnPropertyChanged(nameof(SelectedCameraFooterText));
            OnPropertyChanged(nameof(SelectedCameraDetailStatus));
            OnPropertyChanged(nameof(SelectedCameraDetailWorker));
            OnPropertyChanged(nameof(SelectedCameraLastFrameText));
            OnPropertyChanged(nameof(SelectedCameraGatewayText));
            RaiseCommandStates();
        }
    }

    public CameraSlotViewModel? SelectedSlot
    {
        get => _selectedSlot;
        private set
        {
            if (_selectedSlot == value)
            {
                return;
            }

            if (_selectedSlot is not null)
            {
                _selectedSlot.IsSelected = false;
            }

            _selectedSlot = value;
            if (_selectedSlot is not null)
            {
                _selectedSlot.IsSelected = true;
            }

            OnPropertyChanged();
            OnPropertyChanged(nameof(SelectedSlotText));
        }
    }

    public bool HideOfflineEnabled
    {
        get => _hideOfflineEnabled;
        private set
        {
            if (SetProperty(ref _hideOfflineEnabled, value))
            {
                OnPropertyChanged(nameof(HideOfflineText));
                OnPropertyChanged(nameof(SettingsHideOfflineEnabled));
            }
        }
    }

    private bool _showLibraryAsTree = true;
    public bool ShowLibraryAsTree
    {
        get => _showLibraryAsTree;
        set
        {
            if (SetProperty(ref _showLibraryAsTree, value))
            {
                OnPropertyChanged(nameof(LibraryViewIcon));
            }
        }
    }

    public string LibraryViewIcon => _showLibraryAsTree ? "\uE8EC" : "\uE8F2";

    private bool _shareViewPreset;
    public bool ShareViewPreset
    {
        get => _shareViewPreset;
        set => SetProperty(ref _shareViewPreset, value);
    }

    private string _backupPassword = "";
    public string BackupPassword
    {
        get => _backupPassword;
        set => SetProperty(ref _backupPassword, value);
    }

    public ICommand ExportBackupCommand { get; set; }
    public ICommand ImportBackupCommand { get; set; }

    public ObservableCollection<CameraGroupNode> LibraryTreeNodes { get; } = new();

    public bool BoxesEnabled
    {
        get => _boxesEnabled;
        private set
        {
            if (SetProperty(ref _boxesEnabled, value))
            {
                ApplyBoxesEnabled();
                OnPropertyChanged(nameof(BoxesText));
                OnPropertyChanged(nameof(SettingsBoxesEnabled));
            }
        }
    }

    public bool SpotlightEnabled
    {
        get => _spotlightEnabled;
        private set
        {
            if (SetProperty(ref _spotlightEnabled, value))
            {
                OnPropertyChanged(nameof(SpotlightText));
                OnPropertyChanged(nameof(SettingsSpotlightEnabled));
                NotifySpotlightChanged();
                SaveAppSettings();
            }
        }
    }

    public bool AlarmPopupEnabled
    {
        get => _alarmPopupEnabled;
        private set
        {
            if (SetProperty(ref _alarmPopupEnabled, value))
            {
                OnPropertyChanged(nameof(SettingsAlarmPopupEnabled));
                SaveAppSettings();
                if (!value)
                {
                    CloseAlarmPopup();
                }
            }
        }
    }

    public OperatorEventViewModel? AlarmPopupEvent
    {
        get => _alarmPopupEvent;
        private set
        {
            if (SetProperty(ref _alarmPopupEvent, value))
            {
                OnPropertyChanged(nameof(HasAlarmPopup));
                OnPropertyChanged(nameof(AlarmPopupTitle));
                OnPropertyChanged(nameof(AlarmPopupSubtitle));
                OnPropertyChanged(nameof(AlarmPopupEventSeverityBrush));
                OnPropertyChanged(nameof(AlarmPopupEventSeverityLabel));
                OnPropertyChanged(nameof(AlarmPopupEventStatusLabel));
                OnPropertyChanged(nameof(AlarmPopupEventId));
                OnPropertyChanged(nameof(AlarmPopupEventHasClip));
                OnPropertyChanged(nameof(AlarmPopupEventOpenCommand));
                OnPropertyChanged(nameof(AlarmPopupEventClipCommand));
                OnPropertyChanged(nameof(AlarmPopupEventAcknowledgeCommand));
                OnPropertyChanged(nameof(AlarmPopupEventCloseCommand));
                if (PinAlarmPopupCameraCommand is RelayCommand pinPopup)
                {
                    pinPopup.RaiseCanExecuteChanged();
                }

                foreach (var slot in MosaicSlots)
                {
                    slot.IsModalActive = value is not null;
                }
            }
        }
    }

    public bool HasAlarmPopup => AlarmPopupEvent is not null;

    public Avalonia.Media.IBrush? AlarmPopupEventSeverityBrush => AlarmPopupEvent?.SeverityBrush;

    public string AlarmPopupEventSeverityLabel => AlarmPopupEvent?.SeverityLabel ?? "";

    public string AlarmPopupEventStatusLabel => AlarmPopupEvent?.StatusLabel ?? "";

    public string AlarmPopupEventId => AlarmPopupEvent?.Id.ToString(System.Globalization.CultureInfo.InvariantCulture) ?? "";

    public bool AlarmPopupEventHasClip => AlarmPopupEvent?.HasClip ?? false;

    public System.Windows.Input.ICommand? AlarmPopupEventOpenCommand => AlarmPopupEvent?.OpenCommand;

    public System.Windows.Input.ICommand? AlarmPopupEventClipCommand => AlarmPopupEvent?.ClipCommand;

    public System.Windows.Input.ICommand? AlarmPopupEventAcknowledgeCommand => AlarmPopupEvent?.AcknowledgeCommand;

    public System.Windows.Input.ICommand? AlarmPopupEventCloseCommand => AlarmPopupEvent?.CloseCommand;

    public string AlarmPopupTitle => AlarmPopupEvent is null
        ? "Novo alarme"
        : $"{AlarmPopupEvent.CameraName} - {AlarmPopupEvent.EventLabel}";

    public string AlarmPopupSubtitle => AlarmPopupEvent is null
        ? ""
        : $"{AlarmPopupEvent.TimeLabel} | {AlarmPopupEvent.SeverityLabel} | {AlarmPopupEvent.ConfidenceLabel}";

    public bool IsSequencing
    {
        get => _isSequencing;
        private set
        {
            if (SetProperty(ref _isSequencing, value))
            {
                OnPropertyChanged(nameof(SequenceText));
            }
        }
    }

    public bool IsFocusMode
    {
        get => _isFocusMode;
        private set
        {
            if (SetProperty(ref _isFocusMode, value))
            {
                OnPropertyChanged(nameof(FocusText));
            }
        }
    }

    public bool IsMosaicFullscreen
    {
        get => _isMosaicFullscreen;
        private set
        {
            if (SetProperty(ref _isMosaicFullscreen, value))
            {
                OnPropertyChanged(nameof(IsChromeVisible));
                OnPropertyChanged(nameof(MosaicFullscreenText));
                OnPropertyChanged(nameof(MosaicGridRow));
                OnPropertyChanged(nameof(MosaicGridColumn));
                OnPropertyChanged(nameof(MosaicGridRowSpan));
                OnPropertyChanged(nameof(MosaicGridColumnSpan));
                OnPropertyChanged(nameof(ShortcutFooterText));
            }
        }
    }

    public bool IsChromeVisible => !IsMosaicFullscreen;

    public string MosaicFullscreenText => IsMosaicFullscreen ? "Sair da tela cheia do mosaico" : "Tela cheia do mosaico";

    public int MosaicGridRow => IsMosaicFullscreen ? 0 : 1;

    public int MosaicGridColumn => 0;

    public int MosaicGridRowSpan => IsMosaicFullscreen ? 3 : 1;

    public int MosaicGridColumnSpan => IsMosaicFullscreen ? 2 : 1;

    public bool AllFilterSelected => LibraryFilter == "all";

    public bool VisibleFilterSelected => LibraryFilter == "visible";

    public bool OfflineFilterSelected => LibraryFilter == "offline";

    public bool RunningFilterSelected => LibraryFilter == "running";

    public bool AlarmFilterSelected => LibraryFilter == "alarm";

    public bool LibraryPanelSelected => SidePanelMode == "library";

    public bool AlarmsPanelSelected => SidePanelMode == "alarms";

    public bool DiagnosticsPanelSelected => SidePanelMode == "diagnostics";

    public bool SettingsPanelSelected => IsSettingsOpen;

    public bool HasSelectedCamera => SelectedCamera is not null;

    public bool SidePanelExpanded
    {
        get => _sidePanelExpanded;
        private set
        {
            if (SetProperty(ref _sidePanelExpanded, value))
            {
                OnPropertyChanged(nameof(SidePanelCollapsed));
                OnPropertyChanged(nameof(SidePanelWidth));
                OnPropertyChanged(nameof(SidePanelToggleText));
                OnPropertyChanged(nameof(KeyboardShortcutsActive));
                OnPropertyChanged(nameof(ShortcutFooterText));
                SaveAppSettings();
            }
        }
    }

    public bool SidePanelCollapsed => !SidePanelExpanded;

    public double SidePanelWidth => SidePanelExpanded ? 318 : 44;

    public string SidePanelToggleText => SidePanelExpanded ? ">" : "<";

    public string SelectedCameraTitle => SelectedCamera?.DetailTitle ?? "Nenhuma camera selecionada";

    public string SelectedCameraFooterText => SelectedCamera?.FooterText ?? "Selecione uma camera";

    public string SelectedCameraDetailStatus => SelectedCamera?.DetailStatus ?? "-";

    public string SelectedCameraDetailWorker => SelectedCamera?.DetailWorker ?? "-";

    public string SelectedCameraLastFrameText => SelectedCamera?.LastFrameText ?? "-";

    public string SelectedCameraGatewayText => SelectedCamera?.GatewayText ?? "-";

    public string SelectedSlotText => SelectedSlot is null ? "Slot livre" : $"Slot {SelectedSlot.SlotNumber:00}";

    public string HideOfflineText => HideOfflineEnabled ? "Ocultar offline: ON" : "Ocultar offline: OFF";

    public bool ServerBoxesAvailable => Cameras.Any(camera => !string.IsNullOrWhiteSpace(camera.BoxedStreamUrl));

    public string BoxesText => !BoxesEnabled
        ? "Boxes: OFF"
        : ServerBoxesAvailable
            ? "Boxes: ON"
            : "Boxes: N/D";

    public string SpotlightText => SpotlightEnabled ? "Ligado" : "Desligado";

    public OperatorEventViewModel? SpotlightAlarm
    {
        get
        {
            if (!SpotlightEnabled)
            {
                return null;
            }

            return OpenEvents.FirstOrDefault(IsSpotlightCandidate) ?? OpenEvents.FirstOrDefault();
        }
    }

    public bool HasSpotlightAlarm => SpotlightAlarm is not null;

    public bool SpotlightEmptyVisible => SpotlightAlarm is null;

    public string SpotlightAlarmCameraName => SpotlightAlarm?.CameraName ?? "";

    public string SpotlightAlarmEventLabel => SpotlightAlarm?.EventLabel ?? "";

    public string SpotlightAlarmTimeLabel => SpotlightAlarm?.TimeLabel ?? "";

    public System.Windows.Input.ICommand? SpotlightAlarmAcknowledgeCommand => SpotlightAlarm?.AcknowledgeCommand;

    public System.Windows.Input.ICommand? SpotlightAlarmOpenCommand => SpotlightAlarm?.OpenCommand;

    public string SpotlightStateText => !SpotlightEnabled
        ? "Spotlight desativado. A fila de alarmes continua abaixo."
        : OpenEvents.Count == 0
            ? "Sem alarme aberto para destacar."
            : "Sem alarme prioritario para destacar.";


    public string SequenceText => IsSequencing ? "Parar sequencia" : "Sequencia";

    public string FocusText => IsFocusMode ? "Sair do foco" : "Foco";

    public int TotalCameraCount => Cameras.Count;

    public int VisibleCameraCount => Cameras.Count(camera => camera.IsVisible);

    public int DisplayedCameraCount => MosaicSlots.Count(slot => slot.Camera is not null);

    public int RunningCameraCount => Cameras.Count(camera => camera.Camera.IsRunning);

    public int OfflineCameraCount => Cameras.Count(camera => !camera.IsVisible);

    public int OpenEventsCount => OpenEvents.Count;

    public string MonitorSummary => $"{TotalCameraCount} cameras | {DisplayedCameraCount} exibidas | {RunningCameraCount} IA | {OpenEventsCount} alarmes";

    public string OperatorHeadline => IsBusy ? "Processando" : Cameras.Count == 0 ? "Desconectado" : "Operacao ativa";

    public string OperatorSubhead => $"{LastRefreshText} | {ResourceSummary}";

    public string AppVersionText => AppBuildInfo.ShortText;

    public string VersionAlignmentText
    {
        get
        {
            if (!_serverVersionChecked)
            {
                return $"App v{AppBuildInfo.Version} | Web -- | nao verificado";
            }

            if (string.IsNullOrWhiteSpace(_serverVersion))
            {
                return $"App v{AppBuildInfo.Version} | Web legado | atualizar servidor";
            }

            return $"App v{AppBuildInfo.Version} | Web v{_serverVersion} | {VersionAlignmentStatus}";
        }
    }

    public string VersionAlignmentStatus
    {
        get
        {
            if (!_serverVersionChecked)
            {
                return "nao verificado";
            }

            if (string.IsNullOrWhiteSpace(_serverVersion))
            {
                return "atualizar servidor";
            }

            if (_operatorApiVersion is null)
            {
                return "servidor legado";
            }

            if (_operatorApiVersion != AppBuildInfo.OperatorApiVersion)
            {
                return "API incompativel";
            }

            if (string.IsNullOrWhiteSpace(_recommendedOperatorClientVersion)
                || string.Equals(_recommendedOperatorClientVersion, AppBuildInfo.Version, StringComparison.OrdinalIgnoreCase))
            {
                return string.Equals(_serverVersion, AppBuildInfo.Version, StringComparison.OrdinalIgnoreCase)
                    ? "alinhado"
                    : "compativel";
            }

            return CompareVersions(_recommendedOperatorClientVersion, AppBuildInfo.Version) > 0
                ? "atualizar app"
                : "atualizar servidor";
        }
    }

    public string LastRefreshText => _lastRefreshAt is null ? "sem atualizacao" : $"atualizado {_lastRefreshAt.Value:HH:mm:ss}";

    public string ResourceSummary
    {
        get
        {
            if (_metrics is null)
            {
                return "recursos pendentes";
            }

            var gpu = _metrics.Gpu.Available ? _metrics.Gpu.Name ?? "GPU ativa" : "sem GPU";
            return $"CPU {_metrics.HostCpuPercent:0}% | RAM {_metrics.HostRamPercent:0}% | workers {_metrics.WorkerCount} | {gpu}";
        }
    }

    public string PerformanceCpuText => $"{_operatorCpuPercent:0}%";

    public string PerformanceRamText => _operatorRamMb <= 0
        ? "--"
        : $"{_operatorRamMb:0} MB";

    public string PerformanceFpsText => _metrics is null
        ? "--"
        : _metrics.WorkerFpsTotal.ToString("0.0", CultureInfo.InvariantCulture);

    public string PerformanceUpdatedText => _lastPerformanceSampleAt is null
        ? "medindo"
        : _lastPerformanceSampleAt.Value.ToLocalTime().ToString("HH:mm:ss", CultureInfo.InvariantCulture);

    public string PerformanceUploadStatusText => PerformanceLogUploadEnabled
        ? _performanceUploadStatus
        : "Envio de performance para Drive desativado";

    public string DriveTokenInput
    {
        get => _driveTokenInput;
        set => SetProperty(ref _driveTokenInput, value);
    }

    public string DriveTokenStatusText
    {
        get => _driveTokenStatusText;
        private set => SetProperty(ref _driveTokenStatusText, value);
    }

    public bool IsBusy
    {
        get => _isBusy;
        private set
        {
            if (SetProperty(ref _isBusy, value))
            {
                OnPropertyChanged(nameof(OperatorHeadline));
                RaiseCommandStates();
            }
        }
    }

    public void AssignCameraToSlot(int cameraId, CameraSlotViewModel slot)
    {
        var camera = Cameras.FirstOrDefault(item => item.Camera.Id == cameraId);
        if (camera is null)
        {
            return;
        }

        AssignCameraToSlot(camera, slot);
    }

    public int GridColumns
    {
        get => _gridColumns;
        private set => SetProperty(ref _gridColumns, value);
    }

    public int GridRows
    {
        get => _gridRows;
        private set => SetProperty(ref _gridRows, value);
    }

    public int? GetSlotNumberForCamera(int cameraId)
    {
        return MosaicSlots.FirstOrDefault(slot => slot.Camera?.Camera.Id == cameraId)?.SlotNumber;
    }

    public async Task MoveOrAssignCameraToSlotAsync(int cameraId, CameraSlotViewModel targetSlot, int? sourceSlotNumber)
    {
        AppLogger.Info($"Drop iniciado: camera={cameraId}; origem={sourceSlotNumber?.ToString(CultureInfo.InvariantCulture) ?? "biblioteca"}; destino={targetSlot.SlotNumber}");

        await _mosaicLayoutLock.WaitAsync();
        List<CameraTileViewModel> removedCandidates = new();
        try
        {
            var camera = Cameras.FirstOrDefault(item => item.Camera.Id == cameraId);
            if (camera is null)
            {
                AppLogger.Warn($"Drop ignorado: camera {cameraId} nao existe mais.");
                return;
            }

            var sourceSlot = sourceSlotNumber is null
                ? null
                : MosaicSlots.FirstOrDefault(slot => slot.SlotNumber == sourceSlotNumber.Value);

            if (sourceSlot == targetSlot)
            {
                AppLogger.Info($"Drop sem alteracao: origem igual ao destino no slot {targetSlot.SlotNumber}.");
                SelectSlot(targetSlot);
                SelectCamera(camera);
                return;
            }

            StopSequence();
            _isRearrangingMosaic = true;
            var previousVisible = VisibleCameras.ToList();
            var status = "";

            if (sourceSlot is not null && sourceSlot.Camera == camera)
            {
                var displaced = targetSlot.Camera;
                targetSlot.Camera = null;
                sourceSlot.Camera = null;
                targetSlot.ApplyCameraSize();
                sourceSlot.ApplyCameraSize();
                SyncVisibleCameras(autoStart: false);
                await Dispatcher.UIThread.InvokeAsync(() => { }, DispatcherPriority.Render);
                await Task.Delay(160);

                targetSlot.Camera = camera;
                sourceSlot.Camera = displaced;
                targetSlot.ApplyCameraSize();
                sourceSlot.ApplyCameraSize();

                status = displaced is null
                    ? $"{camera.Camera.Name} movida para o slot {targetSlot.SlotNumber:00}"
                    : $"{camera.Camera.Name} trocada com {displaced.Camera.Name}";
            }
            else
            {
                if (sourceSlotNumber is not null)
                {
                    AppLogger.Warn($"Drop com origem obsoleta: camera={cameraId}; origem={sourceSlotNumber.Value}; destino={targetSlot.SlotNumber}");
                }

                foreach (var otherSlot in MosaicSlots.Where(otherSlot => otherSlot.Camera == camera && otherSlot != targetSlot))
                {
                    otherSlot.Camera = null;
                    otherSlot.ApplyCameraSize();
                }

                var displaced = targetSlot.Camera;
                targetSlot.Camera = null;
                targetSlot.ApplyCameraSize();
                SyncVisibleCameras(autoStart: false);
                await Dispatcher.UIThread.InvokeAsync(() => { }, DispatcherPriority.Render);
                await Task.Delay(160);

                targetSlot.Camera = camera;
                targetSlot.ApplyCameraSize();
                status = displaced is null
                    ? $"{camera.Camera.Name} fixada no slot {targetSlot.SlotNumber:00}"
                    : $"{camera.Camera.Name} substituiu {displaced.Camera.Name}";
            }

            SelectSlot(targetSlot);
            SelectCamera(camera);
            SyncVisibleCameras(autoStart: false);
            removedCandidates = previousVisible.Where(previous => !VisibleCameras.Contains(previous)).ToList();
            StatusMessage = status;
            AppLogger.Info($"Swap logico aplicado: camera={cameraId}; destino={targetSlot.SlotNumber}; removidas={removedCandidates.Count}");
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Excecao no drag/drop da camera {cameraId} para slot {targetSlot.SlotNumber}.", exc);
            StatusMessage = $"Falha ao mover camera: {exc.Message}";
        }
        finally
        {
            _isRearrangingMosaic = false;
            _mosaicLayoutLock.Release();
        }

        ScheduleMosaicReconcile(force: false, stopCandidates: removedCandidates);
    }

    public void UpdateViewportSize(double width, double height)
    {
        var nextWidth = Math.Max(520, width);
        var nextHeight = Math.Max(320, height);
        if (Math.Abs(nextWidth - _viewportWidth) < 8 && Math.Abs(nextHeight - _viewportHeight) < 8)
        {
            return;
        }

        _viewportWidth = nextWidth;
        _viewportHeight = nextHeight;
        EnsureSlots(_gridSize);
    }

    private async Task LoadAsync()
    {
        StopRefreshLoop();
        StopSequence();
        _alarmPopupAutoCloseCancellation?.Cancel();
        _alarmPopupAutoCloseCancellation?.Dispose();
        _alarmPopupAutoCloseCancellation = null;
        _loadCancellation?.Cancel();
        _loadCancellation?.Dispose();
        _loadCancellation = new CancellationTokenSource();
        var token = _loadCancellation.Token;

        IsBusy = true;
        StatusMessage = "Conectando ao servidor...";
        AppLogger.Info($"Conectando no servidor: {ServerUrl}");

        try
        {
            var response = await _apiClient.GetBootstrapAsync(ServerUrl, token, registerPaths: true).ConfigureAwait(false);
            
            token.ThrowIfCancellationRequested();
            StatusMessage = "Carregando câmeras...";
            await Dispatcher.UIThread.InvokeAsync(() => ClearCameras());
            await Task.Delay(50, token).ConfigureAwait(false);
            
            token.ThrowIfCancellationRequested();
            await Dispatcher.UIThread.InvokeAsync(() => ApplyBootstrap(response, rebuildMissing: true));
            await Task.Delay(50, token).ConfigureAwait(false);

            token.ThrowIfCancellationRequested();
            StatusMessage = "Sincronizando biblioteca...";
            await Dispatcher.UIThread.InvokeAsync(() => RefreshLibrary());
            await Task.Delay(50, token).ConfigureAwait(false);

            token.ThrowIfCancellationRequested();
            StatusMessage = "Inicializando mosaicos...";
            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                if (RestoreLastLayout
                    && _startupCameraIds.Count > 0
                    && ApplyCameraIdsToSlots(_startupCameraIds, autoStart: true))
                {
                    StatusMessage = $"Layout restaurado com {DisplayedCameraCount} cameras";
                }
                else
                {
                    AutoFillMosaic(autoStart: true);
                }

                SelectCamera(VisibleCameras.FirstOrDefault() ?? Cameras.FirstOrDefault());
                NotifyCounters();
                AppLogger.Info($"Bootstrap carregado. Cameras={Cameras.Count}; RTSP base={response.RtspPublicBaseUrl}");
            });
            await Task.Delay(50, token).ConfigureAwait(false);

            token.ThrowIfCancellationRequested();
            StatusMessage = "Atualizando dados operacionais...";
            await RefreshOperationalDataAsync(isPolling: false, token).ConfigureAwait(false);
            StartRefreshLoop();
        }
        catch (Exception exc) when (exc is not OperationCanceledException)
        {
            AppLogger.Error("Falha ao conectar no servidor.", exc);
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Falha ao conectar: {exc.Message}");
            await Task.Delay(3000, CancellationToken.None).ConfigureAwait(false);
        }
        finally
        {
            await Dispatcher.UIThread.InvokeAsync(() => IsBusy = false);
        }
    }

    public void ConnectOnStartupIfEnabled()
    {
        if (!AutoConnectOnStartup || IsBusy)
        {
            return;
        }

        AppLogger.Info("Conexao automatica de inicializacao solicitada.");
        _ = LoadAsync();
    }

    private async Task RefreshOperationalDataAsync(bool isPolling, CancellationToken cancellationToken = default)
    {
        if (Cameras.Count == 0 && isPolling)
        {
            return;
        }

        try
        {
            var bootstrapTask = _apiClient.GetBootstrapAsync(ServerUrl, cancellationToken, registerPaths: false);
            var metricsTask = _apiClient.GetDashboardMetricsAsync(ServerUrl, cancellationToken);
            var eventsTask = _apiClient.GetDashboardEventsAsync(ServerUrl, cancellationToken);
            var healthTask = _apiClient.GetHealthCamerasAsync(ServerUrl, cancellationToken);

            Task<List<StoredViewPreset>>? presetsTask = null;
            if (!isPolling)
            {
                presetsTask = _apiClient.GetViewPresetsAsync(ServerUrl, cancellationToken);
            }

            if (presetsTask is not null)
            {
                await Task.WhenAll(bootstrapTask, metricsTask, eventsTask, healthTask, presetsTask).ConfigureAwait(false);
            }
            else
            {
                await Task.WhenAll(bootstrapTask, metricsTask, eventsTask, healthTask).ConfigureAwait(false);
            }

            var bootstrap = await bootstrapTask.ConfigureAwait(false);
            var metrics = await metricsTask.ConfigureAwait(false);
            var events = await eventsTask.ConfigureAwait(false);
            var health = await healthTask.ConfigureAwait(false);

            List<StoredViewPreset>? serverPresets = null;
            if (presetsTask is not null)
            {
                serverPresets = await presetsTask.ConfigureAwait(false);
            }

            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                ApplyBootstrap(bootstrap, rebuildMissing: false);
                ApplyMetrics(metrics);
                ApplyEvents(events);
                ApplyHealth(health);
                if (serverPresets is not null)
                {
                    ApplyViewPresets(serverPresets);
                }
                RefreshLibrary();
                SyncVisibleCameras(autoStart: !isPolling && !_isRearrangingMosaic);
                NotifyCounters();
                _lastRefreshAt = DateTimeOffset.Now;
                OnPropertyChanged(nameof(LastRefreshText));
                OnPropertyChanged(nameof(OperatorSubhead));
                StatusMessage = isPolling ? $"Operacao atualizada - {LastRefreshText}" : $"Atualizado - {LastRefreshText}";
            });
        }
        catch (Exception exc) when (exc is not OperationCanceledException)
        {
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Falha ao atualizar dados operacionais: {exc.Message}");
        }
    }

    private void ApplyBootstrap(OperatorBootstrapResponse response, bool rebuildMissing)
    {
        ApplyServerBuildInfo(response);
        var camerasById = Cameras.ToDictionary(camera => camera.Camera.Id);
        var incomingIds = response.Cameras.Select(camera => camera.Id).ToHashSet();

        foreach (var camera in response.Cameras.OrderBy(camera => camera.SiteName).ThenBy(camera => camera.GroupName).ThenBy(camera => camera.Name))
        {
            NormalizeCameraStreamUrls(camera);
            if (camerasById.TryGetValue(camera.Id, out var existing))
            {
                existing.ApplyUpdate(camera);
                continue;
            }

            Cameras.Add(BuildCameraTile(camera, Cameras.Count + 1));
        }

        foreach (var stale in Cameras.Where(camera => !incomingIds.Contains(camera.Camera.Id)).ToList())
        {
            if (!rebuildMissing)
            {
                continue;
            }

            foreach (var slot in MosaicSlots.Where(slot => slot.Camera == stale))
            {
                slot.Camera = null;
            }

            if (SelectedCamera == stale)
            {
                SelectedCamera = null;
            }

            stale.Dispose();
            Cameras.Remove(stale);
        }

        ApplyBoxesEnabled();
        NotifyAllSlotCameraBindings();
        OnPropertyChanged(nameof(ServerBoxesAvailable));
        OnPropertyChanged(nameof(BoxesText));
    }

    private void ApplyServerBuildInfo(OperatorBootstrapResponse response)
    {
        var nextSignature = string.Join(
            "|",
            response.ServerVersion,
            response.ServerReleaseTag,
            response.ServerCommit,
            response.OperatorApiVersion?.ToString(CultureInfo.InvariantCulture),
            response.RecommendedOperatorClientVersion);
        var buildChanged = !string.Equals(_serverBuildSignature, nextSignature, StringComparison.Ordinal);
        _serverBuildSignature = nextSignature;
        _serverVersionChecked = true;
        _serverVersion = response.ServerVersion;
        _serverReleaseTag = response.ServerReleaseTag;
        _serverCommit = response.ServerCommit;
        _operatorApiVersion = response.OperatorApiVersion;
        _recommendedOperatorClientVersion = response.RecommendedOperatorClientVersion;
        OnPropertyChanged(nameof(VersionAlignmentText));
        OnPropertyChanged(nameof(VersionAlignmentStatus));

        if (buildChanged)
        {
            AppLogger.Info(
                $"Versoes conectadas: app={AppBuildInfo.Version}; web={_serverVersion ?? "nao informada"}; " +
                $"api={_operatorApiVersion?.ToString(CultureInfo.InvariantCulture) ?? "nao informada"}; " +
                $"recomendado={_recommendedOperatorClientVersion ?? "nao informado"}; alinhamento={VersionAlignmentStatus}");
        }
    }

    private static int CompareVersions(string left, string right)
    {
        return Version.TryParse(left, out var leftVersion) && Version.TryParse(right, out var rightVersion)
            ? leftVersion.CompareTo(rightVersion)
            : string.Compare(left, right, StringComparison.OrdinalIgnoreCase);
    }

    private CameraTileViewModel BuildCameraTile(OperatorCamera camera, int slot)
    {
        var viewModel = new CameraTileViewModel(camera, slot)
        {
            SelectAction = SelectCamera,
            PinAction = PinCameraToMosaic,
            StartIaAction = StartIaForCameraAsync,
            StopIaAction = StopIaForCameraAsync,
            BoxesEnabled = BoxesEnabled,
            MinimumBoxConfidence = MinimumBoxConfidencePercent / 100.0,
        };
        return viewModel;
    }

    private void NormalizeCameraStreamUrls(OperatorCamera camera)
    {
        var baseUri = AnaliticoApiClient.NormalizeBaseUri(ServerUrl);
        camera.ProcessedStreamUrl = ResolveCameraStreamUrl(
            baseUri,
            camera.ProcessedStreamUrl,
            $"cameras/{camera.Id}/stream/processed");
        camera.BoxedStreamUrl = ResolveAdvertisedCameraStreamUrl(baseUri, camera.BoxedStreamUrl);
        camera.RawStreamUrl = ResolveCameraStreamUrl(
            baseUri,
            camera.RawStreamUrl,
            $"cameras/{camera.Id}/stream/raw");
        camera.MonitorStreamUrl = ResolveCameraStreamUrl(
            baseUri,
            camera.MonitorStreamUrl,
            $"monitor/gateway/cameras/{camera.Id}/stream/live");

        if (!string.IsNullOrWhiteSpace(camera.MediaRtspUrl))
        {
            var serverHost = baseUri.Host;
            var rtspUrl = camera.MediaRtspUrl.Trim();
            if (rtspUrl.Contains("://localhost") || rtspUrl.Contains("://127.0.0.1"))
            {
                camera.MediaRtspUrl = rtspUrl
                    .Replace("://localhost", "://" + serverHost)
                    .Replace("://127.0.0.1", "://" + serverHost);
            }
        }
    }

    private static string ResolveCameraStreamUrl(Uri baseUri, string? value, string fallbackRelativePath)
    {
        var candidate = string.IsNullOrWhiteSpace(value) ? fallbackRelativePath : value.Trim();
        if (Uri.TryCreate(candidate, UriKind.Absolute, out var absolute))
        {
            return absolute.ToString();
        }

        return new Uri(baseUri, candidate.TrimStart('/')).ToString();
    }

    private static string? ResolveAdvertisedCameraStreamUrl(Uri baseUri, string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return null;
        }

        var candidate = value.Trim();
        if (Uri.TryCreate(candidate, UriKind.Absolute, out var absolute))
        {
            return absolute.ToString();
        }

        return new Uri(baseUri, candidate.TrimStart('/')).ToString();
    }

    private CameraSlotViewModel BuildSlot(int slotNumber)
    {
        return new CameraSlotViewModel(slotNumber)
        {
            SelectAction = SelectSlot,
            AssignSelectedAction = AssignSelectedCameraToSlot,
            ClearAction = ClearSlot,
            FocusAction = FocusSlot,
        };
    }

    private void EnsureSlots(int gridSize)
    {
        var target = Math.Clamp(gridSize, 1, 25);
        var (columns, rows) = GridShapeForGrid(target);
        GridColumns = columns;
        GridRows = rows;
        while (MosaicSlots.Count < target)
        {
            MosaicSlots.Add(BuildSlot(MosaicSlots.Count + 1));
        }

        while (MosaicSlots.Count > target)
        {
            var last = MosaicSlots[^1];
            if (SelectedSlot == last)
            {
                SelectedSlot = null;
            }

            MosaicSlots.RemoveAt(MosaicSlots.Count - 1);
            last.Dispose();
        }

        var size = TileSizeForGrid(target);
        foreach (var slot in MosaicSlots)
        {
            slot.TileWidth = size.width;
            slot.TileHeight = size.height;
            slot.ApplyCameraSize();
        }
    }

    private void ApplyMetrics(DashboardMetricsResponse metrics)
    {
        _metrics = metrics;
        StatusCards.Clear();
        StatusCards.Add(new StatusMetricViewModel("Running", metrics.CameraHealth.Running.ToString(CultureInfo.InvariantCulture), "Dentro da janela saudavel"));
        StatusCards.Add(new StatusMetricViewModel("Stopped", metrics.CameraHealth.Stopped.ToString(CultureInfo.InvariantCulture), "Parada manual"));
        StatusCards.Add(new StatusMetricViewModel("Workers", metrics.WorkerCount.ToString(CultureInfo.InvariantCulture), $"{metrics.WorkerRssTotalMb:0} MB RAM"));
        StatusCards.Add(new StatusMetricViewModel("FPS", metrics.WorkerFpsTotal.ToString("0.0", CultureInfo.InvariantCulture), "Fluxo bruto dos workers"));
        StatusCards.Add(new StatusMetricViewModel("CPU/RAM", $"{metrics.HostCpuPercent:0}%/{metrics.HostRamPercent:0}%", "Host"));
        StatusCards.Add(new StatusMetricViewModel("GPU", metrics.Gpu.Available ? metrics.Gpu.Name ?? "ativa" : "indisponivel", metrics.Gpu.UtilizationPercent is null ? "sem leitura" : $"{metrics.Gpu.UtilizationPercent:0}%"));
        OnPropertyChanged(nameof(ResourceSummary));
        OnPropertyChanged(nameof(PerformanceFpsText));
        OnPropertyChanged(nameof(OperatorSubhead));
    }

    private void ApplyEvents(DashboardEventsResponse events)
    {
        _events = events;

        var serverOpenIds = events.OpenEvents.Select(item => item.Id).ToHashSet();
        foreach (var staleId in _dismissedEventIds.Where(id => !serverOpenIds.Contains(id)).ToList())
        {
            _dismissedEventIds.Remove(staleId);
        }

        OpenEvents.Clear();
        foreach (var item in events.OpenEvents.Take(25))
        {
            if (!_dismissedEventIds.Contains(item.Id))
            {
                OpenEvents.Add(BuildEvent(item));
            }
        }

        ShowAlarmPopupForNewEvent();

        RecentEvents.Clear();
        foreach (var item in events.RecentEvents.Take(40))
        {
            RecentEvents.Add(BuildEvent(item));
        }

        var severityByCameraId = new Dictionary<int, string>();
        foreach (var ev in events.OpenEvents)
        {
            if (ev.IsAlarmActive == false || _dismissedEventIds.Contains(ev.Id)) continue;
            var severity = (ev.Severity ?? "").Trim().ToLowerInvariant();
            if (string.IsNullOrEmpty(severity)) continue;

            if (severityByCameraId.TryGetValue(ev.CameraId, out var existing))
            {
                if (existing == "critical") continue;
                if (severity == "critical" || (existing != "high" && severity == "high"))
                {
                    severityByCameraId[ev.CameraId] = severity;
                }
            }
            else
            {
                severityByCameraId[ev.CameraId] = severity;
            }
        }

        foreach (var camVM in Cameras)
        {
            var severity = severityByCameraId.TryGetValue(camVM.Camera.Id, out var s) ? s : null;
            camVM.HighestOpenSeverity = severity;
        }

        OnPropertyChanged(nameof(OpenEventsCount));
        NotifySpotlightChanged();
    }

    private void ApplyViewPresets(List<StoredViewPreset> serverPresets)
    {
        // Once the authenticated server list is available, it is the source of
        // truth. Keeping an old workstation cache here could expose another
        // user's private mosaic on a shared operator terminal.
        SavedViews.Clear();
        foreach (var sp in serverPresets)
        {
            SavedViews.Add(BuildPreset(
                sp.Id,
                sp.Name,
                Math.Clamp(sp.GridSize, 1, 25),
                sp.CameraIds ?? new List<int?>(),
                sp.HideOffline,
                sp.BoxesEnabled,
                sp.IsShared,
                sp.OwnerUsername,
                sp.CanManage
            ));
        }

        SaveViewsToDisk();
        OnPropertyChanged(nameof(SelectedSavedViewSummary));
        OnPropertyChanged(nameof(CanActivateSelectedView));
    }

    private OperatorEventViewModel BuildEvent(OperatorEvent item)
    {
        return new OperatorEventViewModel(item)
        {
            OpenAction = OpenEventEvidence,
            EvidenceAction = OpenEventEvidence,
            AcknowledgeAction = AcknowledgeEventAsync,
            CloseAction = CloseEventAsync,
        };
    }

    private void OpenEventEvidence(OperatorEventViewModel item)
    {
        OpenEventEvidence(item, "snapshot");
    }

    private void OpenEventEvidence(OperatorEventViewModel item, string evidenceKind)
    {
        var useClip = string.Equals(evidenceKind, "clip", StringComparison.OrdinalIgnoreCase);
        var raw = useClip ? item.Source.ClipUrl : item.Source.SnapshotUrl;
        if (string.IsNullOrWhiteSpace(raw))
        {
            StatusMessage = useClip
                ? $"Evento #{item.Id} sem clipe disponivel."
                : $"Evento #{item.Id} sem evidencia visual.";
            return;
        }

        try
        {
            var baseUri = AnaliticoApiClient.NormalizeBaseUri(ServerUrl);
            var uri = Uri.TryCreate(raw, UriKind.Absolute, out var absolute)
                ? absolute
                : new Uri(baseUri, raw.TrimStart('/'));

            _lastEvidenceUri = uri;
            _evidenceCancellation?.Cancel();
            _evidenceCancellation?.Dispose();
            _evidenceCancellation = new CancellationTokenSource();
            var token = _evidenceCancellation.Token;

            // Stop native players in background slots to prevent bleed through (Native child HWND airspace)
            foreach (var slot in MosaicSlots)
            {
                slot.IsModalActive = true;
            }

            EvidenceModalTitle = $"Evidencia do Evento #{item.Id} - Camera {item.Source.CameraName ?? "-"}";
            EvidenceModalSubtitle = $"{item.Source.EventType ?? "Evento"} • {item.Source.CreatedAtLabel ?? ""}";
            IsEvidenceModalOpen = true;

            if (useClip)
            {
                IsEvidenceImageVisible = false;
                IsEvidenceVideoVisible = true;
                IsEvidenceVideoLoading = true;
                EvidenceImage = null;

                if (EvidenceMediaPlayer is null)
                {
                    EvidenceMediaPlayer = new MediaPlayer(CameraTileViewModel.SharedLibVlc.Value);
                    EvidenceMediaPlayer.Playing += (s, e) =>
                    {
                        Dispatcher.UIThread.Post(() => IsEvidenceVideoLoading = false);
                    };
                    EvidenceMediaPlayer.EncounteredError += (s, e) =>
                    {
                        Dispatcher.UIThread.Post(() =>
                        {
                            IsEvidenceVideoLoading = false;
                            StatusMessage = "Erro ao reproduzir clipe de video.";
                        });
                    };
                }

                var media = new Media(CameraTileViewModel.SharedLibVlc.Value, uri.ToString(), FromType.FromLocation);
                media.AddOption(":no-audio");
                EvidenceMediaPlayer.Media = media;
                EvidenceMediaPlayer.Play();

                StatusMessage = $"Reproduzindo clipe do evento #{item.Id}";
            }
            else
            {
                IsEvidenceImageVisible = true;
                IsEvidenceVideoVisible = false;
                IsEvidenceVideoLoading = false;
                EvidenceImage = null;

                if (EvidenceMediaPlayer is not null)
                {
                    var player = EvidenceMediaPlayer;
                    EvidenceMediaPlayer = null;
                    Task.Run(() =>
                    {
                        try
                        {
                            player.Stop();
                            player.Media?.Dispose();
                            player.Dispose();
                        }
                        catch {}
                    });
                }

                Task.Run(async () =>
                {
                    try
                    {
                        var bytes = await _apiClient.GetByteArrayAsync(uri, token).ConfigureAwait(false);
                        using var ms = new MemoryStream(bytes);
                        var bitmap = new Bitmap(ms);
                        await Dispatcher.UIThread.InvokeAsync(() =>
                        {
                            if (!token.IsCancellationRequested)
                            {
                                EvidenceImage = bitmap;
                            }
                        });
                    }
                    catch (Exception ex) when (ex is not OperationCanceledException)
                    {
                        AppLogger.Error($"Falha ao carregar imagem da evidencia do evento #{item.Id}.", ex);
                        await Dispatcher.UIThread.InvokeAsync(() =>
                        {
                            StatusMessage = $"Falha ao carregar imagem: {ex.Message}";
                        });
                    }
                }, token);

                StatusMessage = $"Carregando imagem do evento #{item.Id}";
            }
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Falha ao abrir evidencia do evento #{item.Id}.", exc);
            StatusMessage = $"Falha ao abrir evidencia: {exc.Message}";
        }
    }

    private void OpenEvidenceInBrowser()
    {
        if (_lastEvidenceUri is null) return;
        try
        {
            Process.Start(new ProcessStartInfo(_lastEvidenceUri.ToString())
            {
                UseShellExecute = true,
            });
            StatusMessage = "Abrindo evidencia no navegador padrao";
        }
        catch (Exception exc)
        {
            AppLogger.Error("Falha ao abrir evidencia no navegador.", exc);
            StatusMessage = $"Falha ao abrir no navegador: {exc.Message}";
        }
    }

    private void CloseEvidenceModal()
    {
        IsEvidenceModalOpen = false;
        _evidenceCancellation?.Cancel();
        _evidenceCancellation?.Dispose();
        _evidenceCancellation = null;

        if (EvidenceMediaPlayer is not null)
        {
            var player = EvidenceMediaPlayer;
            EvidenceMediaPlayer = null;
            Task.Run(() =>
            {
                try
                {
                    player.Stop();
                    player.Media?.Dispose();
                    player.Dispose();
                }
                catch {}
            });
        }

        EvidenceImage = null;

        // Restore active native video players in slots
        foreach (var slot in MosaicSlots)
        {
            slot.IsModalActive = HasAlarmPopup;
        }
    }

    private void ShowAlarmPopupForNewEvent()
    {
        if (!AlarmPopupEnabled)
        {
            return;
        }

        var currentOpenIds = OpenEvents.Select(item => item.Id).ToHashSet();
        foreach (var staleId in _announcedAlarmPopupEventIds.Where(id => !currentOpenIds.Contains(id)).ToList())
        {
            _announcedAlarmPopupEventIds.Remove(staleId);
        }
        if (AlarmPopupEvent is not null && !currentOpenIds.Contains(AlarmPopupEvent.Id))
        {
            CloseAlarmPopup();
        }

        var candidate = OpenEvents.FirstOrDefault(IsAlarmPopupCandidate);
        if (candidate is null || _announcedAlarmPopupEventIds.Contains(candidate.Id))
        {
            return;
        }

        _announcedAlarmPopupEventIds.Add(candidate.Id);
        AlarmPopupEvent = candidate;
        StatusMessage = $"Novo alarme: {candidate.CameraName} - {candidate.EventLabel}";
        ScheduleAlarmPopupAutoClose(candidate);
    }

    private void ScheduleAlarmPopupAutoClose(OperatorEventViewModel item)
    {
        _alarmPopupAutoCloseCancellation?.Cancel();
        _alarmPopupAutoCloseCancellation?.Dispose();
        _alarmPopupAutoCloseCancellation = new CancellationTokenSource();

        if (IsHighPriorityAlarm(item))
        {
            return;
        }

        var token = _alarmPopupAutoCloseCancellation.Token;
        _ = Task.Run(async () =>
        {
            try
            {
                await Task.Delay(TimeSpan.FromSeconds(12), token).ConfigureAwait(false);
                await Dispatcher.UIThread.InvokeAsync(() =>
                {
                    if (AlarmPopupEvent?.Id == item.Id)
                    {
                        AlarmPopupEvent = null;
                    }
                });
            }
            catch (OperationCanceledException)
            {
            }
        }, token);
    }

    private void CloseAlarmPopup()
    {
        _alarmPopupAutoCloseCancellation?.Cancel();
        _alarmPopupAutoCloseCancellation?.Dispose();
        _alarmPopupAutoCloseCancellation = null;
        AlarmPopupEvent = null;
    }

    private void ClearAlarmPopupQueue()
    {
        foreach (var alarm in OpenEvents)
        {
            _announcedAlarmPopupEventIds.Add(alarm.Id);
            _dismissedEventIds.Add(alarm.Id);
        }

        OpenEvents.Clear();
        OnPropertyChanged(nameof(OpenEventsCount));
        OnPropertyChanged(nameof(MonitorSummary));

        CloseAlarmPopup();
        StatusMessage = "Fila visual de alarmes limpa nesta sessao";
    }

    private void PinAlarmPopupCamera()
    {
        var alarm = AlarmPopupEvent;
        if (alarm is null)
        {
            return;
        }

        PinAlarmCamera(alarm, "Popup");
        CloseAlarmPopup();
    }

    private void ApplyHealth(HealthCamerasResponse health)
    {
        _health = health;
        HealthItems.Clear();
        foreach (var item in health.Cameras.OrderByDescending(camera => camera.IsRunning).ThenBy(camera => camera.CameraName).Take(80))
        {
            HealthItems.Add(new CameraHealthViewModel(item));
        }
    }

    private void SetGrid(object? parameter)
    {
        var raw = Convert.ToString(parameter, CultureInfo.InvariantCulture);
        if (!int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value))
        {
            return;
        }

        IsFocusMode = false;
        StopSequence();
        GridSize = Math.Clamp(value, 1, 25);
        AutoFillMosaic(autoStart: true);
        SaveAppSettings();
    }

    private void SetLibraryFilter(object? parameter)
    {
        var filter = Convert.ToString(parameter, CultureInfo.InvariantCulture);
        LibraryFilter = filter switch
        {
            "visible" => "visible",
            "offline" => "offline",
            "running" => "running",
            "alarm" => "alarm",
            _ => "all",
        };
        StopSequence();
        RefreshLibrary();
        AutoFillMosaic(autoStart: true);
        SaveAppSettings();
    }

    private void SetSidePanel(object? parameter)
    {
        var mode = Convert.ToString(parameter, CultureInfo.InvariantCulture);
        if (string.Equals(mode, "settings", StringComparison.OrdinalIgnoreCase))
        {
            IsSettingsOpen = true;
            return;
        }

        SidePanelMode = mode switch
        {
            "alarms" => "alarms",
            "diagnostics" => "diagnostics",
            _ => "library",
        };
        SidePanelExpanded = true;
        if (SidePanelMode == "diagnostics")
        {
            _ = RefreshDriveTokenStatusAsync();
        }

        SaveAppSettings();
    }

    private void ToggleSidePanel()
    {
        SidePanelExpanded = !SidePanelExpanded;
    }

    private void AutoFillMosaic(bool autoStart)
    {
        EnsureSlots(_gridSize);
        AssignCamerasToSlots(FilteredCameraQuery().Take(_gridSize).ToList(), preserveSelection: true);
        SyncVisibleCameras(autoStart);
        StatusMessage = $"Mosaico {_gridSize} preenchido com {DisplayedCameraCount} cameras";
        SaveAppSettings();
    }

    private void AssignCamerasToSlots(IReadOnlyList<CameraTileViewModel> cameras, bool preserveSelection)
    {
        EnsureSlots(_gridSize);
        for (var index = 0; index < MosaicSlots.Count; index++)
        {
            MosaicSlots[index].Camera = index < cameras.Count ? cameras[index] : null;
            MosaicSlots[index].ApplyCameraSize();
        }

        if (!preserveSelection || SelectedCamera is null || !Cameras.Contains(SelectedCamera))
        {
            SelectCamera(MosaicSlots.FirstOrDefault(slot => slot.Camera is not null)?.Camera ?? Cameras.FirstOrDefault());
        }
    }

    private bool ApplyCameraIdsToSlots(IReadOnlyList<int?> cameraIds, bool autoStart)
    {
        if (cameraIds.Count == 0)
        {
            return false;
        }

        EnsureSlots(_gridSize);
        var applied = false;
        for (var index = 0; index < MosaicSlots.Count; index++)
        {
            var cameraId = index < cameraIds.Count ? cameraIds[index] : null;
            var camera = cameraId is null
                ? null
                : Cameras.FirstOrDefault(item => item.Camera.Id == cameraId.Value);
            MosaicSlots[index].Camera = camera;
            MosaicSlots[index].ApplyCameraSize();
            applied |= camera is not null;
        }

        if (!applied)
        {
            return false;
        }

        SelectCamera(MosaicSlots.FirstOrDefault(slot => slot.Camera is not null)?.Camera ?? Cameras.FirstOrDefault());
        SyncVisibleCameras(autoStart);
        return true;
    }

    private void SyncVisibleCameras(bool autoStart, bool forceAutoStart = false)
    {
        EnsureSlots(_gridSize);
        var previousVisible = VisibleCameras.ToList();
        var nextCameras = MosaicSlots
            .Select(slot => slot.Camera)
            .Where(camera => camera is not null)
            .Cast<CameraTileViewModel>()
            .GroupBy(camera => camera.Camera.Id)
            .Select(group => group.First())
            .ToList();

        VisibleCameras.Clear();
        foreach (var camera in nextCameras)
        {
            VisibleCameras.Add(camera);
        }

        if (SelectedCamera is null || !Cameras.Contains(SelectedCamera))
        {
            SelectCamera(VisibleCameras.FirstOrDefault() ?? Cameras.FirstOrDefault());
        }

        var removedCandidates = previousVisible.Where(previous => !nextCameras.Contains(previous)).ToList();
        var playbackChanged = MosaicSlots.Any(slot => slot.Camera is not null && slot.NeedsPlaybackSwitch);
        if (autoStart || removedCandidates.Count > 0 || playbackChanged)
        {
            ScheduleMosaicReconcile(forceAutoStart || playbackChanged, removedCandidates);
            _ = RefreshTrackBoxesAsync(CancellationToken.None);
        }

        NotifyCounters();
        SaveAppSettings();
    }

    private void AssignCameraToSlot(CameraTileViewModel camera, CameraSlotViewModel slot)
    {
        _ = AssignCameraToSlotAsync(camera, slot, forceAutoStart: true);
    }

    private async Task AssignCameraToSlotAsync(CameraTileViewModel camera, CameraSlotViewModel slot, bool forceAutoStart = false)
    {
        await MoveOrAssignCameraToSlotAsync(camera.Camera.Id, slot, GetSlotNumberForCamera(camera.Camera.Id));
    }


    private void AssignSelectedCameraToSlot(CameraSlotViewModel slot)
    {
        if (SelectedCamera is null)
        {
            SelectSlot(slot);
            StatusMessage = "Selecione uma camera na biblioteca antes de preencher o slot.";
            return;
        }

        AssignCameraToSlot(SelectedCamera, slot);
    }

    private void PinCameraToMosaic(CameraTileViewModel camera)
    {
        var target = SelectedSlot is { } selected && selected.IsEmpty
            ? selected
            : MosaicSlots.FirstOrDefault(slot => slot.IsEmpty)
                ?? MosaicSlots.FirstOrDefault();

        if (target is null)
        {
            return;
        }

        AssignCameraToSlot(camera, target);
    }

    private void ClearSlot(CameraSlotViewModel slot)
    {
        StopSequence();
        var camera = slot.Camera;
        slot.Camera = null;
        slot.ApplyCameraSize();
        SelectSlot(slot);
        SyncVisibleCameras(autoStart: false);
        if (camera is not null)
        {
            ScheduleMosaicReconcile(force: false, stopCandidates: new[] { camera });
        }

        StatusMessage = $"Slot {slot.SlotNumber:00} limpo";
    }

    private void ClearMosaic()
    {
        StopSequence();
        var previousVisible = VisibleCameras.ToList();
        foreach (var slot in MosaicSlots)
        {
            slot.Camera = null;
            slot.ApplyCameraSize();
        }

        SyncVisibleCameras(autoStart: false);
        ScheduleMosaicReconcile(force: false, stopCandidates: previousVisible);
        StatusMessage = "Mosaico limpo";
    }

    private void SelectSlot(CameraSlotViewModel? slot)
    {
        SelectedSlot = slot;
        if (slot?.Camera is not null)
        {
            SelectCamera(slot.Camera);
        }
    }

    private void FocusSlot(CameraSlotViewModel slot)
    {
        if (slot.Camera is null)
        {
            SelectSlot(slot);
            return;
        }

        SelectCamera(slot.Camera);
        EnterFocusMode(slot.Camera);
    }

    private (double width, double height) TileSizeForGrid(int gridSize)
    {
        var (columns, rows) = GridShapeForGrid(gridSize);
        var width = Math.Floor((_viewportWidth - (columns * 8) - 12) / columns);
        var height = Math.Floor((_viewportHeight - (rows * 8) - 12) / rows);
        return (Math.Max(180, width), Math.Max(126, height));
    }

    private static (int columns, int rows) GridShapeForGrid(int gridSize)
    {
        return gridSize switch
        {
            <= 1 => (1, 1),
            <= 2 => (2, 1),
            <= 4 => (2, 2),
            <= 6 => (3, 2),
            <= 8 => (4, 2),
            <= 9 => (3, 3),
            <= 12 => (4, 3),
            <= 16 => (4, 4),
            _ => (5, 5),
        };
    }

    private void RefreshLibrary()
    {
        LibraryCameras.Clear();
        LibraryTreeNodes.Clear();

        var cameras = FilteredCameraQuery().ToList();
        foreach (var camera in cameras)
        {
            LibraryCameras.Add(camera);
        }

        // Group by SiteName, then GroupName
        var sites = cameras.GroupBy(c => string.IsNullOrWhiteSpace(c.Camera.SiteName) ? "Sem local" : c.Camera.SiteName.Trim())
                           .OrderBy(g => g.Key == "Sem local" ? "ZZZ" : g.Key);

        foreach (var siteGroup in sites)
        {
            var siteNode = new CameraGroupNode
            {
                Name = siteGroup.Key,
                Icon = "\uE80F" // Home/Site Icon
            };

            var groups = siteGroup.GroupBy(c => string.IsNullOrWhiteSpace(c.Camera.GroupName) ? "Geral" : c.Camera.GroupName.Trim())
                                  .OrderBy(g => g.Key == "Geral" ? "ZZZ" : g.Key);

            foreach (var groupOfSite in groups)
            {
                var groupNode = new CameraGroupNode
                {
                    Name = groupOfSite.Key,
                    Icon = "\uE8B7" // Folder Icon
                };

                foreach (var camera in groupOfSite)
                {
                    groupNode.SubNodes.Add(new CameraGroupNode
                    {
                        Name = camera.Camera.Name,
                        Camera = camera
                    });
                }

                if (groupNode.SubNodes.Count > 0)
                {
                    siteNode.SubNodes.Add(groupNode);
                }
            }

            if (siteNode.SubNodes.Count > 0)
            {
                LibraryTreeNodes.Add(siteNode);
            }
        }

        NotifyCounters();
    }

    private IEnumerable<CameraTileViewModel> FilteredCameraQuery()
    {
        return Cameras
            .Where(camera => !HideOfflineEnabled || camera.IsVisible)
            .Where(camera => camera.MatchesFilter(LibraryFilter))
            .Where(camera => camera.MatchesSearch(SearchText))
            .OrderByDescending(camera => camera.IsVisible)
            .ThenBy(camera => camera.Camera.SourceChannel ?? int.MaxValue)
            .ThenBy(camera => camera.Camera.Name);
    }

    private void SelectCamera(CameraTileViewModel? camera)
    {
        SelectedCamera = camera;
    }

    private void ToggleHideOffline()
    {
        HideOfflineEnabled = !HideOfflineEnabled;
        StopSequence();
        RefreshLibrary();
        AutoFillMosaic(autoStart: true);
        SaveAppSettings();
    }

    private void ToggleBoxes()
    {
        BoxesEnabled = !BoxesEnabled;
        if (!BoxesEnabled)
        {
            foreach (var camera in Cameras)
            {
                camera.ApplyTracks(null);
            }
        }
        else
        {
            _ = RefreshTrackBoxesAsync(CancellationToken.None);
        }

        var boxedAvailable = ServerBoxesAvailable;
        AppLogger.Info($"Boxes alterado: enabled={BoxesEnabled}; serverSupport={boxedAvailable}; playback={(BoxesEnabled ? "rtsp+callback-overlay" : "rtsp")}");
        StatusMessage = !BoxesEnabled
            ? "Boxes OFF - usando RTSP direto"
            : boxedAvailable
                ? "Boxes ON - usando RTSP com callback local"
                : "Boxes ON - usando RTSP com callback local; aguardando tracks";
        OnPropertyChanged(nameof(BoxesText));
        ScheduleMosaicReconcile(force: true);
        SaveAppSettings();
    }

    private void ToggleSpotlight()
    {
        SpotlightEnabled = !SpotlightEnabled;
        StatusMessage = SpotlightEnabled
            ? "Spotlight de alarme ativado"
            : "Spotlight de alarme desativado";
    }

    private void PinSpotlightCamera()
    {
        var alarm = SpotlightAlarm;
        if (alarm is null)
        {
            return;
        }

        PinAlarmCamera(alarm, "Spotlight");
    }

    private void PinAlarmCamera(OperatorEventViewModel alarm, string sourceLabel)
    {
        var camera = Cameras.FirstOrDefault(item => item.Camera.Id == alarm.CameraId);
        if (camera is null)
        {
            StatusMessage = $"Camera do alarme #{alarm.Id} nao encontrada no app.";
            return;
        }

        PinCameraToMosaic(camera);
        SetSidePanel("alarms");
        StatusMessage = $"Camera fixada pelo {sourceLabel}: {camera.Camera.Name}";
    }

    private void ApplyBoxesEnabled()
    {
        foreach (var camera in Cameras)
        {
            camera.BoxesEnabled = BoxesEnabled;
        }

        NotifyAllSlotCameraBindings();
    }

    private void NotifyAllSlotCameraBindings()
    {
        foreach (var slot in MosaicSlots)
        {
            slot.NotifyCameraBindingsChanged();
        }
    }

    private void ToggleFocusMode()
    {
        if (IsFocusMode)
        {
            RestoreFocusBackup();
            return;
        }

        var camera = SelectedCamera ?? MosaicSlots.FirstOrDefault(slot => slot.Camera is not null)?.Camera;
        if (camera is null)
        {
            return;
        }

        EnterFocusMode(camera);
    }

    private void ToggleMosaicFullscreen()
    {
        IsMosaicFullscreen = !IsMosaicFullscreen;
        StatusMessage = IsMosaicFullscreen
            ? "Mosaico em tela cheia"
            : "Tela cheia do mosaico encerrada";
    }

    private void EnterFocusMode(CameraTileViewModel camera)
    {
        StopSequence();
        _focusBackupGrid = _gridSize;
        _focusBackupCameraIds = MosaicSlots.Select(slot => slot.Camera?.Camera.Id).ToList();
        IsFocusMode = true;
        GridSize = 1;
        EnsureSlots(_gridSize);
        MosaicSlots[0].Camera = camera;
        MosaicSlots[0].ApplyCameraSize();
        SelectSlot(MosaicSlots[0]);
        SyncVisibleCameras(autoStart: true);
        StatusMessage = $"Foco em {camera.Camera.Name}";
    }

    private void RestoreFocusBackup()
    {
        IsFocusMode = false;
        GridSize = Math.Clamp(_focusBackupGrid, 1, 25);
        EnsureSlots(_gridSize);
        for (var index = 0; index < MosaicSlots.Count; index++)
        {
            var cameraId = index < _focusBackupCameraIds.Count ? _focusBackupCameraIds[index] : null;
            MosaicSlots[index].Camera = cameraId is null
                ? null
                : Cameras.FirstOrDefault(camera => camera.Camera.Id == cameraId.Value);
            MosaicSlots[index].ApplyCameraSize();
        }

        SyncVisibleCameras(autoStart: true);
        StatusMessage = "Foco encerrado";
    }

    private void ToggleSequence()
    {
        if (IsSequencing)
        {
            StopSequence();
            return;
        }

        StartSequence();
    }

    private void StartSequence()
    {
        if (Cameras.Count == 0)
        {
            return;
        }

        IsFocusMode = false;
        _sequenceCancellation?.Cancel();
        _sequenceCancellation?.Dispose();
        _sequenceCancellation = new CancellationTokenSource();
        IsSequencing = true;
        _ = SequenceLoopAsync(_sequenceCancellation.Token);
        StatusMessage = "Sequencia iniciada";
    }

    private async Task SequenceLoopAsync(CancellationToken token)
    {
        var page = 0;
        var presetIndex = 0;
        try
        {
            while (!token.IsCancellationRequested)
            {
                if (SequenceSavedViews)
                {
                    var presets = await Dispatcher.UIThread.InvokeAsync(() => SavedViews.ToList());
                    if (presets.Count == 0)
                    {
                        await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = "Ronda: nenhuma visão salva encontrada");
                        await Task.Delay(TimeSpan.FromSeconds(6), token).ConfigureAwait(false);
                        continue;
                    }

                    if (presetIndex >= presets.Count)
                    {
                        presetIndex = 0;
                    }

                    var preset = presets[presetIndex];
                    await Dispatcher.UIThread.InvokeAsync(() =>
                    {
                        LoadViewPreset(preset);
                        StatusMessage = $"Ronda: {preset.Name} ({presetIndex + 1}/{presets.Count})";
                    });

                    presetIndex++;
                }
                else
                {
                    var cameras = await Dispatcher.UIThread.InvokeAsync(() => FilteredCameraQuery().ToList());
                    if (cameras.Count == 0)
                    {
                        await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = "Sequencia sem cameras no filtro atual");
                        await Task.Delay(TimeSpan.FromSeconds(6), token).ConfigureAwait(false);
                        continue;
                    }

                    var maxPages = Math.Max(1, (int)Math.Ceiling(cameras.Count / (double)Math.Max(1, _gridSize)));
                    if (page >= maxPages)
                    {
                        page = 0;
                    }

                    var pageItems = cameras.Skip(page * _gridSize).Take(_gridSize).ToList();
                    await Dispatcher.UIThread.InvokeAsync(() =>
                    {
                        AssignCamerasToSlots(pageItems, preserveSelection: false);
                        SyncVisibleCameras(autoStart: true);
                        StatusMessage = $"Sequencia {page + 1}/{maxPages} - {DisplayedCameraCount} cameras";
                    });
                    page++;
                }

                await Task.Delay(TimeSpan.FromSeconds(SequenceIntervalSeconds), token).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    private void StopSequence()
    {
        _sequenceCancellation?.Cancel();
        _sequenceCancellation?.Dispose();
        _sequenceCancellation = null;
        IsSequencing = false;
    }

    private void ResetOperationalPreferences()
    {
        AutoConnectOnStartup = false;
        RestoreLastLayout = true;
        SequenceIntervalSeconds = 8;

        if (HideOfflineEnabled)
        {
            ToggleHideOffline();
        }

        if (!BoxesEnabled)
        {
            ToggleBoxes();
        }

        SpotlightEnabled = true;
        AlarmPopupEnabled = true;
        StatusMessage = "Preferencias operacionais restauradas";
        SaveAppSettings();
    }

    private void OpenViewManager()
    {
        IsSettingsOpen = false;
        ViewPresetNameDraft = string.IsNullOrWhiteSpace(CurrentViewName) ? "Monitor 1" : CurrentViewName.Trim();
        SelectedSavedView = null;
        PendingViewPreset = null;
        IsViewActivationConfirmationOpen = false;
        IsViewManagerOpen = true;
        _ = RefreshViewPresetsAsync();
    }

    private void CloseViewManager()
    {
        IsViewActivationConfirmationOpen = false;
        PendingViewPreset = null;
        IsViewManagerOpen = false;
    }

    private async Task RefreshViewPresetsAsync()
    {
        if (string.IsNullOrWhiteSpace(ServerUrl))
        {
            StatusMessage = "Informe o servidor para sincronizar as visoes.";
            return;
        }

        try
        {
            var presets = await _apiClient.GetViewPresetsAsync(ServerUrl, CancellationToken.None).ConfigureAwait(false);
            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                var selectedId = SelectedSavedView?.Id;
                ApplyViewPresets(presets);
                SelectedSavedView = SavedViews.FirstOrDefault(item => item.Id == selectedId);
                StatusMessage = $"{SavedViews.Count} visao(oes) sincronizada(s) com o web";
            });
        }
        catch (Exception exc)
        {
            Program.LogException(exc);
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = "Nao foi possivel sincronizar as visoes com o web.");
        }
    }

    private async Task SaveCurrentViewAsync()
    {
        var name = ViewPresetNameDraft.Trim();
        if (string.IsNullOrWhiteSpace(name))
        {
            StatusMessage = "Informe um nome para salvar a visao.";
            return;
        }

        var existing = SavedViews.FirstOrDefault(item => item.CanManage && string.Equals(item.Name, name, StringComparison.OrdinalIgnoreCase));
        var id = existing?.Id ?? ("view_" + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());

        var preset = BuildPreset(id, name, _gridSize, MosaicSlots.Select(slot => slot.Camera?.Camera.Id), HideOfflineEnabled, BoxesEnabled, IsAdmin && ShareViewPreset);

        try
        {
            var stored = new StoredViewPreset
            {
                Id = preset.Id,
                Name = preset.Name,
                GridSize = preset.GridSize,
                CameraIds = preset.CameraIds,
                HideOffline = preset.HideOffline,
                BoxesEnabled = preset.BoxesEnabled,
                IsShared = preset.IsShared
            };
            await _apiClient.SaveViewPresetAsync(ServerUrl, stored, CancellationToken.None).ConfigureAwait(false);
            await Dispatcher.UIThread.InvokeAsync(() => CurrentViewName = name);
            await RefreshViewPresetsAsync().ConfigureAwait(false);
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Visao salva e sincronizada: {name}");
        }
        catch (Exception exc)
        {
            Program.LogException(exc);
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = "Nao foi possivel salvar a visao no servidor.");
        }
    }

    private void RequestActivateSelectedView()
    {
        if (SelectedSavedView is null)
        {
            StatusMessage = "Selecione uma visao para ativar.";
            return;
        }

        PendingViewPreset = SelectedSavedView;
        IsViewActivationConfirmationOpen = true;
    }

    private void ConfirmActivateView()
    {
        if (PendingViewPreset is null)
        {
            IsViewActivationConfirmationOpen = false;
            return;
        }

        LoadViewPreset(PendingViewPreset);
        IsViewActivationConfirmationOpen = false;
        PendingViewPreset = null;
        IsViewManagerOpen = false;
    }

    private ViewPresetViewModel BuildPreset(string id, string name, int gridSize, IEnumerable<int?> cameraIds, bool hideOffline, bool boxesEnabled, bool isShared = false, string? ownerUsername = null, bool canManage = true)
    {
        var finalId = string.IsNullOrWhiteSpace(id) ? ("view_" + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()) : id;
        return new ViewPresetViewModel(finalId, name, gridSize, cameraIds, hideOffline, boxesEnabled, isShared, ownerUsername, canManage)
        {
            LoadAction = LoadViewPreset,
        };
    }

    private void LoadViewPreset(ViewPresetViewModel preset)
    {
        StopSequence();
        IsFocusMode = false;
        CurrentViewName = preset.Name;
        HideOfflineEnabled = preset.HideOffline;
        BoxesEnabled = preset.BoxesEnabled;
        GridSize = Math.Clamp(preset.GridSize, 1, 25);
        EnsureSlots(_gridSize);
        for (var index = 0; index < MosaicSlots.Count; index++)
        {
            var cameraId = index < preset.CameraIds.Count ? preset.CameraIds[index] : null;
            MosaicSlots[index].Camera = cameraId is null
                ? null
                : Cameras.FirstOrDefault(camera => camera.Camera.Id == cameraId.Value);
            MosaicSlots[index].ApplyCameraSize();
        }

        RefreshLibrary();
        SyncVisibleCameras(autoStart: true);
        StatusMessage = $"Visao carregada: {preset.Name}";
    }

    private void LoadSavedViews()
    {
        try
        {
            var path = ViewsPath();
            if (!File.Exists(path))
            {
                return;
            }

            var payload = JsonSerializer.Deserialize<List<StoredViewPreset>>(File.ReadAllText(path), PersistJsonOptions) ?? new();
            SavedViews.Clear();
            foreach (var item in payload.Where(item => !string.IsNullOrWhiteSpace(item.Name)).Take(20))
            {
                var id = string.IsNullOrWhiteSpace(item.Id) ? ("view_" + Guid.NewGuid().ToString("N")) : item.Id;
                SavedViews.Add(BuildPreset(
                    id,
                    item.Name,
                    Math.Clamp(item.GridSize, 1, 25),
                    item.CameraIds ?? new List<int?>(),
                    item.HideOffline,
                    item.BoxesEnabled,
                    item.IsShared,
                    item.OwnerUsername,
                    item.CanManage));
            }
        }
        catch (Exception exc)
        {
            Program.LogException(exc);
        }
    }

    private void LoadAppSettings()
    {
        try
        {
            var path = AppSettingsPath();
            if (!File.Exists(path))
            {
                return;
            }

            _isRestoringSettings = true;
            var settings = JsonSerializer.Deserialize<OperatorAppSettings>(File.ReadAllText(path), PersistJsonOptions);
            if (settings is null)
            {
                return;
            }

            _serverUrl = string.IsNullOrWhiteSpace(settings.ServerUrl) ? DefaultServerUrl : settings.ServerUrl.Trim();
            _currentViewName = string.IsNullOrWhiteSpace(settings.CurrentViewName) ? "Monitor 1" : settings.CurrentViewName.Trim();
            GridSize = Math.Clamp(settings.GridSize <= 0 ? 16 : settings.GridSize, 1, 25);
            _hideOfflineEnabled = settings.HideOffline;
            _boxesEnabled = settings.BoxesEnabled ?? true;
            _spotlightEnabled = settings.SpotlightEnabled ?? true;
            _alarmPopupEnabled = settings.AlarmPopupEnabled ?? true;
            _performanceLogUploadEnabled = settings.PerformanceLogUploadEnabled ?? true;
            _autoConnectOnStartup = settings.AutoConnectOnStartup ?? false;
            _restoreLastLayout = settings.RestoreLastLayout ?? true;
            _sequenceIntervalSeconds = Math.Clamp(settings.SequenceIntervalSeconds ?? 8, 3, 60);
            _sequenceSavedViews = settings.SequenceSavedViews ?? false;
            _boxRefreshIntervalMs = Math.Clamp(settings.BoxRefreshIntervalMs ?? 200, 100, 2000);
            _minimumBoxConfidencePercent = Math.Clamp(settings.MinimumBoxConfidencePercent ?? 40, 0, 100);
            _sidePanelExpanded = settings.SidePanelExpanded ?? true;
            _sidePanelMode = settings.SidePanelMode switch
            {
                "alarms" => "alarms",
                "diagnostics" => "diagnostics",
                _ => "library",
            };
            _startupCameraIds = settings.CameraIds ?? new List<int?>();
            _rememberMe = settings.RememberMe;
            _username = settings.Username;
            _password = settings.Password;
        }
        catch (Exception exc)
        {
            Program.LogException(exc);
        }
        finally
        {
            _isRestoringSettings = false;
        }
    }

    public void ClearRememberedCredentials()
    {
        _rememberMe = false;
        _username = null;
        _password = null;
        SaveAppSettings();
        AppLogger.Info("Credenciais lembradas removidas (logout).");
    }

    private void SaveAppSettings()
    {
        if (!_settingsLoaded || _isRestoringSettings)
        {
            return;
        }

        try
        {
            var path = AppSettingsPath();
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var settings = new OperatorAppSettings
            {
                ServerUrl = ServerUrl,
                CurrentViewName = CurrentViewName,
                GridSize = _gridSize,
                HideOffline = HideOfflineEnabled,
                BoxesEnabled = BoxesEnabled,
                SpotlightEnabled = SpotlightEnabled,
                AlarmPopupEnabled = AlarmPopupEnabled,
                PerformanceLogUploadEnabled = PerformanceLogUploadEnabled,
                AutoConnectOnStartup = AutoConnectOnStartup,
                RestoreLastLayout = RestoreLastLayout,
                SequenceIntervalSeconds = SequenceIntervalSeconds,
                SequenceSavedViews = SequenceSavedViews,
                BoxRefreshIntervalMs = BoxRefreshIntervalMs,
                MinimumBoxConfidencePercent = MinimumBoxConfidencePercent,
                SidePanelExpanded = SidePanelExpanded,
                SidePanelMode = SidePanelMode,
                CameraIds = MosaicSlots.Select(slot => slot.Camera?.Camera.Id).ToList(),
                RememberMe = _rememberMe,
                Username = _username,
                Password = _password
            };
            File.WriteAllText(path, JsonSerializer.Serialize(settings, PersistJsonOptions));
        }
        catch (Exception exc)
        {
            Program.LogException(exc);
        }
    }

    private void SaveViewsToDisk()
    {
        try
        {
            var path = ViewsPath();
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var payload = SavedViews
                .Select(item => new StoredViewPreset
                {
                    Id = item.Id,
                    Name = item.Name,
                    GridSize = item.GridSize,
                    CameraIds = item.CameraIds,
                    HideOffline = item.HideOffline,
                    BoxesEnabled = item.BoxesEnabled,
                })
                .ToList();
            File.WriteAllText(path, JsonSerializer.Serialize(payload, PersistJsonOptions));
        }
        catch (Exception exc)
        {
            Program.LogException(exc);
        }
    }

    private static string ViewsPath()
    {
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "AnaliticoOperator",
            "operator-views.json");
    }

    private static string AppSettingsPath()
    {
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "AnaliticoOperator",
            "operator-settings.json");
    }

    private async Task StartSelectedIaAsync()
    {
        if (SelectedCamera is not null)
        {
            await StartIaForCameraAsync(SelectedCamera).ConfigureAwait(false);
        }
    }

    private async Task ReconnectSelectedPlayerAsync()
    {
        var camera = SelectedCamera;
        if (camera is null)
        {
            return;
        }

        var slot = SelectedSlot?.Camera == camera
            ? SelectedSlot
            : MosaicSlots.FirstOrDefault(item => item.Camera == camera);

        if (slot is null)
        {
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Camera fora do mosaico: {camera.Camera.Name}");
            return;
        }

        try
        {
            StatusMessage = $"Reconectando player: {camera.Camera.Name}";
            await slot.RestartPlayerAsync(CancellationToken.None).ConfigureAwait(false);
        }
        catch (Exception exc)
        {
            AppLogger.Error($"Falha ao reconectar player da camera {camera.Camera.Id}.", exc);
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Falha ao reconectar player: {exc.Message}");
        }
    }

    private async Task StopSelectedIaAsync()
    {
        if (SelectedCamera is not null)
        {
            await StopIaForCameraAsync(SelectedCamera).ConfigureAwait(false);
        }
    }

    private async Task StartIaForCameraAsync(CameraTileViewModel camera)
    {
        try
        {
            await _apiClient.StartCameraAsync(ServerUrl, camera.Camera.Id, CancellationToken.None).ConfigureAwait(false);
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"IA iniciada: {camera.Camera.Name}");
            await Task.Delay(400).ConfigureAwait(false);
            await RefreshOperationalDataAsync(isPolling: false).ConfigureAwait(false);
        }
        catch (Exception exc)
        {
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Falha ao iniciar IA {camera.Camera.Name}: {exc.Message}");
        }
    }

    private async Task StopIaForCameraAsync(CameraTileViewModel camera)
    {
        try
        {
            await _apiClient.StopCameraAsync(ServerUrl, camera.Camera.Id, CancellationToken.None).ConfigureAwait(false);
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"IA parada: {camera.Camera.Name}");
            await Task.Delay(400).ConfigureAwait(false);
            await RefreshOperationalDataAsync(isPolling: false).ConfigureAwait(false);
        }
        catch (Exception exc)
        {
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Falha ao parar IA {camera.Camera.Name}: {exc.Message}");
        }
    }

    private async Task AcknowledgeEventAsync(OperatorEventViewModel item)
    {
        try
        {
            await _apiClient.AcknowledgeEventAsync(ServerUrl, item.Id, CancellationToken.None).ConfigureAwait(false);
            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                if (AlarmPopupEvent?.Id == item.Id)
                {
                    CloseAlarmPopup();
                }
            });
            await RefreshOperationalDataAsync(isPolling: false).ConfigureAwait(false);
        }
        catch (Exception exc)
        {
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Falha ao reconhecer evento #{item.Id}: {exc.Message}");
        }
    }

    private async Task CloseEventAsync(OperatorEventViewModel item)
    {
        try
        {
            await _apiClient.CloseEventAsync(ServerUrl, item.Id, CancellationToken.None).ConfigureAwait(false);
            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                if (AlarmPopupEvent?.Id == item.Id)
                {
                    CloseAlarmPopup();
                }
            });
            await RefreshOperationalDataAsync(isPolling: false).ConfigureAwait(false);
        }
        catch (Exception exc)
        {
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Falha ao fechar evento #{item.Id}: {exc.Message}");
        }
    }

    private void NotifyCounters()
    {
        OnPropertyChanged(nameof(TotalCameraCount));
        OnPropertyChanged(nameof(VisibleCameraCount));
        OnPropertyChanged(nameof(DisplayedCameraCount));
        OnPropertyChanged(nameof(CurrentMosaicSummary));
        OnPropertyChanged(nameof(CanSaveViewPreset));
        OnPropertyChanged(nameof(RunningCameraCount));
        OnPropertyChanged(nameof(OfflineCameraCount));
        OnPropertyChanged(nameof(OpenEventsCount));
        OnPropertyChanged(nameof(MonitorSummary));
        NotifySpotlightChanged();
    }

    private void NotifySpotlightChanged()
    {
        OnPropertyChanged(nameof(SpotlightAlarm));
        OnPropertyChanged(nameof(HasSpotlightAlarm));
        OnPropertyChanged(nameof(SpotlightEmptyVisible));
        OnPropertyChanged(nameof(SpotlightStateText));
        OnPropertyChanged(nameof(SpotlightAlarmCameraName));
        OnPropertyChanged(nameof(SpotlightAlarmEventLabel));
        OnPropertyChanged(nameof(SpotlightAlarmTimeLabel));
        OnPropertyChanged(nameof(SpotlightAlarmAcknowledgeCommand));
        OnPropertyChanged(nameof(SpotlightAlarmOpenCommand));
        if (PinSpotlightCameraCommand is RelayCommand pinSpotlight)
        {
            pinSpotlight.RaiseCanExecuteChanged();
        }
    }

    private static bool IsSpotlightCandidate(OperatorEventViewModel item)
    {
        var severity = (item.Source.Severity ?? "").Trim().ToLowerInvariant();
        return item.Source.IsAlarmActive || severity is "critical" or "high";
    }

    private static bool IsAlarmPopupCandidate(OperatorEventViewModel item)
    {
        var eventType = (item.Source.EventType ?? "").Trim().ToLowerInvariant();
        var eventLabel = (item.Source.EventTypeLabel ?? "").Trim().ToLowerInvariant();
        return IsSpotlightCandidate(item)
            || eventType.Contains("person", StringComparison.OrdinalIgnoreCase)
            || eventType.Contains("intrusion", StringComparison.OrdinalIgnoreCase)
            || eventType.Contains("invas", StringComparison.OrdinalIgnoreCase)
            || eventLabel.Contains("pessoa", StringComparison.OrdinalIgnoreCase)
            || eventLabel.Contains("invas", StringComparison.OrdinalIgnoreCase);
    }

    private static bool IsHighPriorityAlarm(OperatorEventViewModel item)
    {
        var severity = (item.Source.Severity ?? "").Trim().ToLowerInvariant();
        return item.Source.IsAlarmActive || severity is "critical" or "high";
    }

    private void StopAll()
    {
        _ = StopAllAsync();
    }

    private async Task StopAllAsync()
    {
        AppLogger.Info("Parando todos os players do mosaico.");

        CancelPlaybackStart();
        CancelMosaicReconcile();

        foreach (var slot in MosaicSlots.ToList())
        {
            await slot.StopPlayerAsync(CancellationToken.None).ConfigureAwait(false);
        }
    }

    private void ClearCameras(bool stopSlotPlayers = true)
    {
        CancelPlaybackStart();
        foreach (var camera in Cameras)
        {
            camera.Dispose();
        }

        Cameras.Clear();
        foreach (var slot in MosaicSlots)
        {
            slot.Camera = null;
            if (stopSlotPlayers)
            {
                _ = slot.StopPlayerAsync(CancellationToken.None);
            }
        }

        VisibleCameras.Clear();
        LibraryCameras.Clear();
        OpenEvents.Clear();
        RecentEvents.Clear();
        HealthItems.Clear();
        StatusCards.Clear();
        SelectedCamera = null;
        NotifyCounters();
    }

    private void ScheduleMosaicReconcile(
        bool force = false,
        IEnumerable<CameraTileViewModel>? stopCandidates = null)
    {
        var candidates = stopCandidates?
            .Where(camera => camera is not null)
            .Distinct()
            .ToList() ?? new List<CameraTileViewModel>();

        if (_isRearrangingMosaic && !force)
        {
            AppLogger.Info($"Reconciliação adiada: mosaico em rearranjo. stopCandidates={candidates.Count}");
            return;
        }

        CancelMosaicReconcile();
        _mosaicReconcileCancellation = new CancellationTokenSource();
        AppLogger.Info($"Start agendado: reconcile em 400ms; force={force}; stopCandidates={candidates.Count}");
        _ = ReconcileMosaicPlayersAsync(candidates, force, _mosaicReconcileCancellation.Token);
    }

    private async Task ReconcileMosaicPlayersAsync(
        IReadOnlyList<CameraTileViewModel> stopCandidates,
        bool force,
        CancellationToken token)
    {
        try
        {
            await Task.Delay(400, token).ConfigureAwait(false);
            await _mosaicLayoutLock.WaitAsync(token).ConfigureAwait(false);
            try
            {
                if (_isRearrangingMosaic)
                {
                    AppLogger.Info("Reconciliação cancelada: layout ainda em rearranjo.");
                    return;
                }

                var slotSnapshot = await Dispatcher.UIThread.InvokeAsync(() => MosaicSlots.ToList(), DispatcherPriority.Background, token);
                var visibleSnapshot = await Dispatcher.UIThread.InvokeAsync(() => VisibleCameras.ToList(), DispatcherPriority.Background, token);
                AppLogger.Info($"Reconcile iniciado: slots={slotSnapshot.Count}; visiveis={visibleSnapshot.Count}; stopCandidates={stopCandidates.Count}; force={force}");

                foreach (var slot in slotSnapshot.Where(slot => slot.Camera is null))
                {
                    token.ThrowIfCancellationRequested();
                    if (!slot.IsPlaybackActive)
                    {
                        continue;
                    }

                    AppLogger.Info($"Stop agendado por slot vazio: slot={slot.SlotNumber:00}");
                    await slot.StopPlayerAsync(token).ConfigureAwait(false);
                }

                foreach (var slot in slotSnapshot.Where(slot => slot.Camera is not null))
                {
                    token.ThrowIfCancellationRequested();
                    var camera = slot.Camera;
                    if (camera is null)
                    {
                        continue;
                    }

                    var forceSlot = force || slot.NeedsPlaybackSwitch;
                    if (!camera.IsStreamAvailable)
                    {
                        AppLogger.Info($"Stop agendado por stream indisponivel: slot={slot.SlotNumber:00}; camera={camera.Camera.Id}; nome={camera.Camera.Name}");
                        await slot.StopPlayerAsync(token).ConfigureAwait(false);
                        continue;
                    }

                    if (slot.IsPlayingAssignedCamera && !forceSlot)
                    {
                        continue;
                    }

                    if (!forceSlot && !slot.CanAutoStartPlayer)
                    {
                        continue;
                    }

                    AppLogger.Info($"Start agendado por reconciliacao: slot={slot.SlotNumber:00}; camera={camera.Camera.Id}; nome={camera.Camera.Name}; force={forceSlot}");
                    await slot.ReconcilePlayerAsync(token, force: forceSlot).ConfigureAwait(false);
                    await Task.Delay(120, token).ConfigureAwait(false);
                }

                AppLogger.Info("Reconcile concluido.");
            }
            finally
            {
                _mosaicLayoutLock.Release();
            }
        }
        catch (OperationCanceledException)
        {
            AppLogger.Info("Reconcile cancelado.");
        }
        catch (Exception exc)
        {
            AppLogger.Error("Falha na reconciliacao dos players do mosaico.", exc);
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Falha ao reconciliar players: {exc.Message}");
        }
    }

    private void StartRefreshLoop()
    {
        StopRefreshLoop();
        _refreshCancellation = new CancellationTokenSource();
        _ = RefreshLoopAsync(_refreshCancellation.Token);
        _trackCancellation = new CancellationTokenSource();
        _ = TrackLoopAsync(_trackCancellation.Token);
    }

    private void StartPerformanceLoop()
    {
        StopPerformanceLoop();
        _performanceCancellation = new CancellationTokenSource();
        _ = PerformanceLoopAsync(_performanceCancellation.Token);
    }

    private void StopPerformanceLoop()
    {
        _performanceCancellation?.Cancel();
        _performanceCancellation?.Dispose();
        _performanceCancellation = null;
    }

    private async Task PerformanceLoopAsync(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                await UpdateOperatorPerformanceAsync(token).ConfigureAwait(false);
                await MaybeUploadPerformanceLogAsync(token).ConfigureAwait(false);
                await Task.Delay(TimeSpan.FromSeconds(2), token).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                return;
            }
            catch (Exception exc)
            {
                AppLogger.Warn($"Falha ao medir performance local do operador: {exc.Message}");
                await Task.Delay(TimeSpan.FromSeconds(5), token).ConfigureAwait(false);
            }
        }
    }

    private async Task UpdateOperatorPerformanceAsync(CancellationToken token)
    {
        using var process = Process.GetCurrentProcess();
        var now = DateTimeOffset.UtcNow;
        var cpuTime = process.TotalProcessorTime;
        var elapsed = _lastPerformanceSampleAt is null
            ? TimeSpan.Zero
            : now - _lastPerformanceSampleAt.Value;

        var cpuPercent = _operatorCpuPercent;
        if (_lastPerformanceSampleAt is not null && elapsed.TotalMilliseconds > 0)
        {
            var cpuDelta = cpuTime - _lastProcessCpuTime;
            cpuPercent = cpuDelta.TotalMilliseconds / elapsed.TotalMilliseconds / Environment.ProcessorCount * 100.0;
            cpuPercent = Math.Clamp(cpuPercent, 0, 100);
        }

        var ramMb = process.WorkingSet64 / 1024.0 / 1024.0;

        _lastPerformanceSampleAt = now;
        _lastProcessCpuTime = cpuTime;

        await Dispatcher.UIThread.InvokeAsync(() =>
        {
            _operatorCpuPercent = cpuPercent;
            _operatorRamMb = ramMb;
            OnPropertyChanged(nameof(PerformanceCpuText));
            OnPropertyChanged(nameof(PerformanceRamText));
            OnPropertyChanged(nameof(PerformanceUpdatedText));
        }, DispatcherPriority.Background, token);
    }

    private async Task MaybeUploadPerformanceLogAsync(CancellationToken token)
    {
        if (!PerformanceLogUploadEnabled)
        {
            return;
        }

        var now = DateTimeOffset.UtcNow;
        if (_lastPerformanceUploadAt is not null && now - _lastPerformanceUploadAt.Value < TimeSpan.FromMinutes(1))
        {
            return;
        }

        var hasCameras = await Dispatcher.UIThread.InvokeAsync(
            () => Cameras.Count > 0,
            DispatcherPriority.Background,
            token);
        if (!hasCameras)
        {
            await Dispatcher.UIThread.InvokeAsync(
                () => SetPerformanceUploadStatus("Aguardando conexao para enviar performance"),
                DispatcherPriority.Background,
                token);
            return;
        }

        _lastPerformanceUploadAt = now;
        var payload = await Dispatcher.UIThread.InvokeAsync(
            () => BuildPerformanceLogPayload(now),
            DispatcherPriority.Background,
            token);

        try
        {
            await _apiClient.PostOperatorPerformanceLogAsync(ServerUrl, payload, token).ConfigureAwait(false);
            await Dispatcher.UIThread.InvokeAsync(
                () => SetPerformanceUploadStatus($"Performance enviada {now.ToLocalTime():HH:mm}"),
                DispatcherPriority.Background,
                token);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception exc)
        {
            AppLogger.Warn($"Falha ao enviar performance do operador: {exc.Message}");
            await Dispatcher.UIThread.InvokeAsync(
                () => SetPerformanceUploadStatus($"Falha ao enviar performance: {ShortError(exc.Message)}"),
                DispatcherPriority.Background,
                token);
        }
    }

    private Dictionary<string, object?> BuildPerformanceLogPayload(DateTimeOffset capturedAt)
    {
        var metrics = _metrics;
        return new Dictionary<string, object?>
        {
            ["captured_at_utc"] = capturedAt.UtcDateTime.ToString("O", CultureInfo.InvariantCulture),
            ["machine_name"] = Environment.MachineName,
            ["hardware"] = new Dictionary<string, object?>
            {
                ["machine_name"] = Environment.MachineName,
                ["user_name"] = Environment.UserName,
                ["os_version"] = Environment.OSVersion.ToString(),
                ["is_64_bit_os"] = Environment.Is64BitOperatingSystem,
                ["is_64_bit_process"] = Environment.Is64BitProcess,
                ["processor_count"] = Environment.ProcessorCount,
                ["cpu_name"] = GetLocalCpuName(),
                ["total_ram_mb"] = GetTotalPhysicalMemoryMb(),
            },
            ["app"] = new Dictionary<string, object?>
            {
                ["name"] = "Analitico Operator",
                ["version"] = AppBuildInfo.Version,
                ["build"] = AppBuildInfo.ShortText,
                ["operator_api_version"] = AppBuildInfo.OperatorApiVersion,
                ["server_url"] = ServerUrl,
            },
            ["operator_process"] = new Dictionary<string, object?>
            {
                ["cpu_percent"] = Math.Round(_operatorCpuPercent, 2),
                ["ram_mb"] = Math.Round(_operatorRamMb, 2),
                ["sampled_at_utc"] = _lastPerformanceSampleAt?.UtcDateTime.ToString("O", CultureInfo.InvariantCulture),
            },
            ["layout"] = new Dictionary<string, object?>
            {
                ["grid_size"] = _gridSize,
                ["displayed_cameras"] = DisplayedCameraCount,
                ["total_cameras"] = TotalCameraCount,
                ["visible_cameras"] = VisibleCameraCount,
                ["running_cameras"] = RunningCameraCount,
                ["open_events"] = OpenEventsCount,
                ["boxes_enabled"] = BoxesEnabled,
                ["hide_offline_enabled"] = HideOfflineEnabled,
            },
            ["server_metrics"] = metrics is null
                ? null
                : new Dictionary<string, object?>
                {
                    ["generated_at"] = metrics.GeneratedAt,
                    ["host_cpu_percent"] = metrics.HostCpuPercent,
                    ["host_ram_percent"] = metrics.HostRamPercent,
                    ["worker_count"] = metrics.WorkerCount,
                    ["worker_cpu_total_percent"] = metrics.WorkerCpuTotalPercent,
                    ["worker_rss_total_mb"] = metrics.WorkerRssTotalMb,
                    ["worker_fps_total"] = metrics.WorkerFpsTotal,
                    ["worker_processed_fps_total"] = metrics.WorkerProcessedFpsTotal,
                    ["gpu"] = new Dictionary<string, object?>
                    {
                        ["available"] = metrics.Gpu.Available,
                        ["name"] = metrics.Gpu.Name,
                        ["utilization_percent"] = metrics.Gpu.UtilizationPercent,
                        ["memory_used_mb"] = metrics.Gpu.MemoryUsedMb,
                        ["memory_total_mb"] = metrics.Gpu.MemoryTotalMb,
                        ["temperature_c"] = metrics.Gpu.TemperatureC,
                    },
                },
        };
    }

    private void SetPerformanceUploadStatus(string value)
    {
        if (_performanceUploadStatus == value)
        {
            return;
        }

        _performanceUploadStatus = value;
        OnPropertyChanged(nameof(PerformanceUploadStatusText));
    }

    private static string GetLocalCpuName()
    {
        if (OperatingSystem.IsWindows())
        {
            try
            {
                var value = Registry.GetValue(
                    @"HKEY_LOCAL_MACHINE\HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                    "ProcessorNameString",
                    null) as string;
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value.Trim();
                }
            }
            catch
            {
                // Best-effort hardware inventory.
            }
        }

        return Environment.GetEnvironmentVariable("PROCESSOR_IDENTIFIER") ?? "desconhecido";
    }

    private static double? GetTotalPhysicalMemoryMb()
    {
        if (!OperatingSystem.IsWindows())
        {
            return null;
        }

        try
        {
            var status = new MemoryStatusEx();
            status.dwLength = (uint)Marshal.SizeOf<MemoryStatusEx>();
            if (GlobalMemoryStatusEx(ref status))
            {
                return Math.Round(status.ullTotalPhys / 1024.0 / 1024.0, 0);
            }
        }
        catch
        {
            // Best-effort hardware inventory.
        }

        return null;
    }

    private static string ShortError(string value)
    {
        var clean = string.IsNullOrWhiteSpace(value) ? "erro desconhecido" : value.Trim();
        return clean.Length <= 80 ? clean : clean[..77] + "...";
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GlobalMemoryStatusEx(ref MemoryStatusEx lpBuffer);

    [StructLayout(LayoutKind.Sequential)]
    private struct MemoryStatusEx
    {
        public uint dwLength;
        public uint dwMemoryLoad;
        public ulong ullTotalPhys;
        public ulong ullAvailPhys;
        public ulong ullTotalPageFile;
        public ulong ullAvailPageFile;
        public ulong ullTotalVirtual;
        public ulong ullAvailVirtual;
        public ulong ullAvailExtendedVirtual;
    }

    private async Task RefreshLoopAsync(CancellationToken token)
    {
        try
        {
            while (!token.IsCancellationRequested)
            {
                await Task.Delay(TimeSpan.FromSeconds(5), token).ConfigureAwait(false);
                await RefreshOperationalDataAsync(isPolling: true, token).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    private async Task TrackLoopAsync(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            if (!BoxesEnabled)
            {
                await Task.Delay(TimeSpan.FromMilliseconds(350), token).ConfigureAwait(false);
                continue;
            }

            var visible = GetTrackVisibleCameras();
            if (visible.Count == 0)
            {
                await Task.Delay(TimeSpan.FromMilliseconds(350), token).ConfigureAwait(false);
                continue;
            }

            try
            {
                using var reconnectCancellation = CancellationTokenSource.CreateLinkedTokenSource(token);
                reconnectCancellation.CancelAfter(TimeSpan.FromSeconds(30));
                await _apiClient.StreamMonitorTracksAsync(
                    ServerUrl,
                    visible.Select(camera => camera.Camera.Id),
                    BoxRefreshIntervalMs,
                    response => ApplyTrackResponseAsync(response, token),
                    reconnectCancellation.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (!token.IsCancellationRequested)
            {
                // Reconexao periodica para atualizar a lista de cameras visiveis no stream.
            }
            catch (Exception exc) when (exc is not OperationCanceledException)
            {
                AppLogger.Warn($"SSE de boxes caiu; fallback polling. Motivo={exc.Message}");
                await RefreshTrackBoxesAsync(token).ConfigureAwait(false);
                await Task.Delay(TimeSpan.FromMilliseconds(BoxRefreshIntervalMs), token).ConfigureAwait(false);
            }
        }
    }

    private List<CameraTileViewModel> GetTrackVisibleCameras()
    {
        return MosaicSlots
            .Where(slot => slot.CanRequestTrackBoxes && slot.Camera is not null)
            .Select(slot => slot.Camera!)
            .GroupBy(camera => camera.Camera.Id)
            .Select(group => group.First())
            .ToList();
    }

    private void NotifySlotsForCamera(CameraTileViewModel camera)
    {
        foreach (var slot in MosaicSlots.Where(slot => slot.Camera == camera))
        {
            slot.NotifyCameraOverlayChanged();
        }
    }

    private async Task ApplyTrackResponseAsync(MonitorTracksResponse response, CancellationToken token)
    {
        var clientReceivedAtNs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() * 1_000_000;
        foreach (var payload in response.Cameras.Values)
        {
            payload.ClientReceivedAtNs = clientReceivedAtNs;
            if (payload.TracksPublishedAtNs is > 0)
            {
                payload.BackendToClientMs = Math.Max(
                    0,
                    (clientReceivedAtNs - payload.TracksPublishedAtNs.Value) / 1_000_000.0);
            }
        }
        await Dispatcher.UIThread.InvokeAsync(() =>
        {
            foreach (var camera in GetTrackVisibleCameras())
            {
                response.Cameras.TryGetValue(camera.Camera.Id.ToString(CultureInfo.InvariantCulture), out var payload);
                camera.ApplyTracks(payload);
                NotifySlotsForCamera(camera);
            }
        }, DispatcherPriority.Background, token);
    }

    private async Task RefreshTrackBoxesAsync(CancellationToken token)
    {
        if (!BoxesEnabled)
        {
            return;
        }

        var visible = GetTrackVisibleCameras();
        if (visible.Count == 0)
        {
            return;
        }

        try
        {
            var response = await _apiClient.GetMonitorTracksAsync(ServerUrl, visible.Select(camera => camera.Camera.Id), token).ConfigureAwait(false);
            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                foreach (var camera in visible)
                {
                    response.Cameras.TryGetValue(camera.Camera.Id.ToString(CultureInfo.InvariantCulture), out var payload);
                    camera.ApplyTracks(payload);
                    NotifySlotsForCamera(camera);
                }
            });
        }
        catch (Exception exc) when (exc is not OperationCanceledException)
        {
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Falha ao atualizar boxes IA: {exc.Message}");
        }
    }

    private async Task SaveDriveTokenAsync()
    {
        var token = DriveTokenInput.Trim();
        if (string.IsNullOrWhiteSpace(token))
        {
            DriveTokenStatusText = "Drive: cole o token antes de salvar";
            return;
        }

        IsBusy = true;
        DriveTokenStatusText = "Drive: salvando token...";
        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15));
            await _apiClient.SaveOneDriveTokenAsync(ServerUrl, token, cts.Token).ConfigureAwait(false);
            var status = await _apiClient.GetOneDriveStatusAsync(ServerUrl, cts.Token).ConfigureAwait(false);
            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                DriveTokenInput = "";
                DriveTokenStatusText = FormatDriveStatus(status);
                StatusMessage = "Token do Drive salvo no servidor";
            });
        }
        catch (Exception exc) when (exc is not OperationCanceledException)
        {
            AppLogger.Error("Falha ao salvar token do Drive.", exc);
            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                DriveTokenStatusText = $"Drive: falha ao salvar token - {ShortError(exc.Message)}";
                StatusMessage = "Falha ao salvar token do Drive";
            });
        }
        finally
        {
            await Dispatcher.UIThread.InvokeAsync(() => IsBusy = false);
        }
    }

    private async Task RefreshDriveTokenStatusAsync()
    {
        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8));
            var status = await _apiClient.GetOneDriveStatusAsync(ServerUrl, cts.Token).ConfigureAwait(false);
            await Dispatcher.UIThread.InvokeAsync(() => DriveTokenStatusText = FormatDriveStatus(status));
        }
        catch (Exception exc) when (exc is not OperationCanceledException)
        {
            await Dispatcher.UIThread.InvokeAsync(() => DriveTokenStatusText = $"Drive: status indisponivel - {ShortError(exc.Message)}");
        }
    }

    private async Task UploadReviewedEventsToDriveAsync()
    {
        IsBusy = true;
        DriveTokenStatusText = "Drive: enviando eventos avaliados...";
        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromMinutes(5));
            var response = await _apiClient.UploadReviewedEventsToOneDriveAsync(ServerUrl, 200, cts.Token).ConfigureAwait(false);
            var result = response.OneDrive;
            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                DriveTokenStatusText =
                    $"Drive: {result.EventsProcessed} eventos | json {result.EventJsonUploaded} | snapshots {result.SnapshotUploaded} | clips {result.ClipUploaded} | falhas {result.Failed}";
                StatusMessage = result.ReviewedPendingAfter > 0
                    ? $"Drive: ainda restam {result.ReviewedPendingAfter} eventos avaliados pendentes"
                    : "Drive: eventos avaliados pendentes enviados";
            });
        }
        catch (Exception exc) when (exc is not OperationCanceledException)
        {
            AppLogger.Error("Falha ao enviar eventos avaliados para o Drive.", exc);
            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                DriveTokenStatusText = $"Drive: falha no envio - {ShortError(exc.Message)}";
                StatusMessage = "Falha ao enviar eventos avaliados para o Drive";
            });
        }
        finally
        {
            await Dispatcher.UIThread.InvokeAsync(() => IsBusy = false);
        }
    }

    private static string FormatDriveStatus(OneDriveStatusResponse status)
    {
        if (!string.IsNullOrWhiteSpace(status.TokenError))
        {
            return $"Drive: token invalido ({status.TokenError})";
        }

        if (!string.IsNullOrWhiteSpace(status.RefreshError))
        {
            return $"Drive: refresh falhou ({ShortError(status.RefreshError)})";
        }

        var token = status.TokenExists ? "token salvo" : "sem token";
        var client = status.ClientIdConfigured ? "client ok" : "client pendente";
        var refresh = status.RefreshEnabled ? "refresh pronto" : "refresh pendente";
        var upload = status.ArchiveEnabled ? "upload ativo" : "upload desativado";
        return $"Drive: {token} | {client} | {refresh} | {upload}";
    }

    private async Task CollectDiagnosticsAsync()
    {
        IsBusy = true;
        StatusMessage = "Gerando diagnostico do app...";

        try
        {
            var diagnosticsRoot = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "AnaliticoOperator",
                "diagnostics");
            Directory.CreateDirectory(diagnosticsRoot);

            var stamp = DateTimeOffset.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture);
            var zipPath = Path.Combine(diagnosticsRoot, $"operator-diagnostics_{stamp}.zip");
            if (File.Exists(zipPath))
            {
                File.Delete(zipPath);
            }

            var appState = BuildDiagnosticsState();
            var remotePayloads = await CollectRemoteDiagnosticsAsync().ConfigureAwait(false);

            using (var archive = ZipFile.Open(zipPath, ZipArchiveMode.Create))
            {
                AddTextEntry(archive, "app_state.json", JsonSerializer.Serialize(appState, PersistJsonOptions));

                foreach (var payload in remotePayloads)
                {
                    AddTextEntry(archive, $"server/{payload.Key}.json", payload.Value);
                }

                AddLogFiles(archive);
            }

            await Dispatcher.UIThread.InvokeAsync(() =>
            {
                StatusMessage = $"Diagnostico salvo: {zipPath}";
            });
        }
        catch (Exception exc) when (exc is not OperationCanceledException)
        {
            AppLogger.Error("Falha ao gerar diagnostico do app.", exc);
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Falha no diagnostico: {exc.Message}");
        }
        finally
        {
            await Dispatcher.UIThread.InvokeAsync(() => IsBusy = false);
        }
    }

    private void OpenLogsFolder()
    {
        try
        {
            Directory.CreateDirectory(AppLogger.LogDirectory);
            Process.Start(new ProcessStartInfo(AppLogger.LogDirectory)
            {
                UseShellExecute = true,
            });
            StatusMessage = $"Abrindo logs: {AppLogger.LogDirectory}";
        }
        catch (Exception exc)
        {
            AppLogger.Error("Falha ao abrir pasta de logs.", exc);
            StatusMessage = $"Falha ao abrir logs: {exc.Message}";
        }
    }

    private async Task ExportBackupAsync()
    {
        if (string.IsNullOrWhiteSpace(BackupPassword))
        {
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = "Erro: Digite uma senha para criptografar o backup.");
            return;
        }

        try
        {
            IsBusy = true;
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = "Gerando backup no servidor...");
            
            var backupBytes = await _apiClient.ExportBackupAsync(ServerUrl, BackupPassword, CancellationToken.None).ConfigureAwait(false);

            await Dispatcher.UIThread.InvokeAsync(async () =>
            {
                if (Avalonia.Application.Current?.ApplicationLifetime is Avalonia.Controls.ApplicationLifetimes.IClassicDesktopStyleApplicationLifetime desktop && desktop.MainWindow != null)
                {
                    var file = await desktop.MainWindow.StorageProvider.SaveFilePickerAsync(new Avalonia.Platform.Storage.FilePickerSaveOptions
                    {
                        Title = "Salvar Backup do Servidor",
                        DefaultExtension = "enc",
                        SuggestedFileName = $"vms_backup_{DateTime.Now:yyyyMMdd_HHmmss}.enc",
                        FileTypeChoices = new[]
                        {
                            new Avalonia.Platform.Storage.FilePickerFileType("Backup Criptografado (*.enc)") { Patterns = new[] { "*.enc" } }
                        }
                    });

                    if (file != null)
                    {
                        using var stream = await file.OpenWriteAsync();
                        await stream.WriteAsync(backupBytes, 0, backupBytes.Length);
                        StatusMessage = $"Backup exportado com sucesso: {file.Name}";
                    }
                    else
                    {
                        StatusMessage = "Exportação de backup cancelada.";
                    }
                }
                else
                {
                    StatusMessage = "Erro: Interface gráfica não disponível para salvar arquivo.";
                }
            });
        }
        catch (Exception exc)
        {
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Erro ao exportar backup: {exc.Message}");
        }
        finally
        {
            await Dispatcher.UIThread.InvokeAsync(() => IsBusy = false);
        }
    }

    private async Task ImportBackupAsync()
    {
        if (string.IsNullOrWhiteSpace(BackupPassword))
        {
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = "Erro: Digite a senha para descriptografar o backup.");
            return;
        }

        try
        {
            await Dispatcher.UIThread.InvokeAsync(async () =>
            {
                if (Avalonia.Application.Current?.ApplicationLifetime is Avalonia.Controls.ApplicationLifetimes.IClassicDesktopStyleApplicationLifetime desktop && desktop.MainWindow != null)
                {
                    var files = await desktop.MainWindow.StorageProvider.OpenFilePickerAsync(new Avalonia.Platform.Storage.FilePickerOpenOptions
                    {
                        Title = "Selecionar Backup para Restaurar",
                        AllowMultiple = false,
                        FileTypeFilter = new[]
                        {
                            new Avalonia.Platform.Storage.FilePickerFileType("Backup Criptografado (*.enc)") { Patterns = new[] { "*.enc" } }
                        }
                    });

                    if (files != null && files.Count > 0)
                    {
                        var file = files[0];
                        StatusMessage = "Lendo arquivo de backup...";
                        
                        byte[] backupBytes;
                        using (var stream = await file.OpenReadAsync())
                        {
                            using (var ms = new System.IO.MemoryStream())
                            {
                                await stream.CopyToAsync(ms);
                                backupBytes = ms.ToArray();
                            }
                        }

                        IsBusy = true;
                        StatusMessage = "Descriptografando e aplicando backup no servidor...";
                        
                        _ = Task.Run(async () =>
                        {
                            try
                            {
                                var result = await _apiClient.ImportBackupAsync(ServerUrl, backupBytes, BackupPassword, CancellationToken.None).ConfigureAwait(false);
                                await Dispatcher.UIThread.InvokeAsync(() =>
                                {
                                    StatusMessage = "Backup restaurado com sucesso! O servidor foi atualizado.";
                                    _ = RefreshOperationalDataAsync(isPolling: false);
                                });
                            }
                            catch (Exception exc)
                            {
                                await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Erro ao restaurar backup: {exc.Message}");
                            }
                            finally
                            {
                                await Dispatcher.UIThread.InvokeAsync(() => IsBusy = false);
                            }
                        });
                    }
                    else
                    {
                        StatusMessage = "Importação de backup cancelada.";
                    }
                }
                else
                {
                    StatusMessage = "Erro: Interface gráfica não disponível para abrir arquivo.";
                }
            });
        }
        catch (Exception exc)
        {
            await Dispatcher.UIThread.InvokeAsync(() => StatusMessage = $"Erro ao importar backup: {exc.Message}");
        }
    }

    private object BuildDiagnosticsState()
    {
        return new
        {
            generated_at = DateTimeOffset.Now,
            server_url = ServerUrl,
            app_version = AppBuildInfo.Version,
            server_version = _serverVersion,
            server_release_tag = _serverReleaseTag,
            server_commit = _serverCommit,
            operator_api_version = _operatorApiVersion,
            recommended_operator_client_version = _recommendedOperatorClientVersion,
            version_alignment = VersionAlignmentStatus,
            status = StatusMessage,
            grid_size = _gridSize,
            focus_mode = IsFocusMode,
            sequencing = IsSequencing,
            boxes_enabled = BoxesEnabled,
            hide_offline = HideOfflineEnabled,
            side_panel_expanded = SidePanelExpanded,
            side_panel_mode = SidePanelMode,
            app_settings_path = AppSettingsPath(),
            logs_dir = AppLogger.LogDirectory,
            selected_camera_id = SelectedCamera?.Camera.Id,
            counters = new
            {
                total = TotalCameraCount,
                visible = VisibleCameraCount,
                displayed = DisplayedCameraCount,
                running = RunningCameraCount,
                offline = OfflineCameraCount,
                open_events = OpenEventsCount,
            },
            cameras = Cameras.Select(camera => new
            {
                id = camera.Camera.Id,
                name = camera.Camera.Name,
                status = camera.Camera.Status,
                running = camera.Camera.IsRunning,
                visible = camera.IsVisible,
                registration_ok = camera.Camera.RegistrationOk,
                registration_reason = camera.Camera.RegistrationReason,
                rtsp = camera.MediaRtspUrl,
                last_frame_at = camera.Camera.LastFrameAt,
                last_metrics_at = camera.Camera.LastMetricsAt,
                track_status = camera.TrackStatusText,
                player = camera.StatusBadgeText,
            }).ToList(),
        };
    }

    private async Task<Dictionary<string, string>> CollectRemoteDiagnosticsAsync()
    {
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var endpoints = new Dictionary<string, string>
        {
            ["dashboard_metrics"] = "dashboard/metrics",
            ["dashboard_events"] = "dashboard/events",
            ["health_cameras"] = "health/cameras",
            ["monitor_tracks"] = $"monitor/tracks?camera_ids={string.Join(",", VisibleCameras.Select(camera => camera.Camera.Id))}&max_age_seconds=3",
        };
        var result = new Dictionary<string, string>();

        foreach (var endpoint in endpoints)
        {
            try
            {
                result[endpoint.Key] = await _apiClient.GetRawStringAsync(ServerUrl, endpoint.Value, cts.Token).ConfigureAwait(false);
            }
            catch (Exception exc) when (exc is not OperationCanceledException)
            {
                result[endpoint.Key] = JsonSerializer.Serialize(new { ok = false, error = exc.Message });
            }
        }

        return result;
    }

    private static void AddTextEntry(ZipArchive archive, string name, string content)
    {
        var entry = archive.CreateEntry(name, CompressionLevel.Fastest);
        using var stream = entry.Open();
        using var writer = new StreamWriter(stream, Encoding.UTF8);
        writer.Write(content);
    }

    private static void AddLogFiles(ZipArchive archive)
    {
        if (!Directory.Exists(AppLogger.LogDirectory))
        {
            return;
        }

        foreach (var logFile in Directory.GetFiles(AppLogger.LogDirectory, "*.log").Take(10))
        {
            var entryName = $"logs/{Path.GetFileName(logFile)}";
            archive.CreateEntryFromFile(logFile, entryName, CompressionLevel.Fastest);
        }
    }

    private void StopRefreshLoop()
    {
        _refreshCancellation?.Cancel();
        _refreshCancellation?.Dispose();
        _refreshCancellation = null;
        _trackCancellation?.Cancel();
        _trackCancellation?.Dispose();
        _trackCancellation = null;
    }

    private void RestartTrackLoop()
    {
        if (_trackCancellation is null)
        {
            return;
        }

        _trackCancellation.Cancel();
        _trackCancellation.Dispose();
        _trackCancellation = new CancellationTokenSource();
        _ = TrackLoopAsync(_trackCancellation.Token);
    }

    private void ApplyMinimumBoxConfidence()
    {
        var minimum = MinimumBoxConfidencePercent / 100.0;
        foreach (var camera in Cameras)
        {
            camera.MinimumBoxConfidence = minimum;
        }

        foreach (var slot in MosaicSlots)
        {
            slot.NotifyCameraOverlayChanged();
        }
    }

    private void CancelPlaybackStart()
    {
        _playbackStartCancellation?.Cancel();
        _playbackStartCancellation?.Dispose();
        _playbackStartCancellation = null;
    }

    private void CancelMosaicReconcile()
    {
        _mosaicReconcileCancellation?.Cancel();
        _mosaicReconcileCancellation?.Dispose();
        _mosaicReconcileCancellation = null;
    }

    private void RaiseCommandStates()
    {
        if (LoadCommand is RelayCommand load)
        {
            load.RaiseCanExecuteChanged();
        }

        if (RefreshCommand is RelayCommand refresh)
        {
            refresh.RaiseCanExecuteChanged();
        }

        if (StartSelectedIaCommand is RelayCommand start)
        {
            start.RaiseCanExecuteChanged();
        }

        if (ReconnectSelectedPlayerCommand is RelayCommand reconnect)
        {
            reconnect.RaiseCanExecuteChanged();
        }

        if (StopSelectedIaCommand is RelayCommand stop)
        {
            stop.RaiseCanExecuteChanged();
        }

        if (AutoFillCommand is RelayCommand autoFill)
        {
            autoFill.RaiseCanExecuteChanged();
        }

        if (SaveViewCommand is RelayCommand saveView)
        {
            saveView.RaiseCanExecuteChanged();
        }

        if (SaveDriveTokenCommand is RelayCommand saveDriveToken)
        {
            saveDriveToken.RaiseCanExecuteChanged();
        }

        if (UploadReviewedEventsToDriveCommand is RelayCommand uploadReviewedEvents)
        {
            uploadReviewedEvents.RaiseCanExecuteChanged();
        }

        if (ToggleSequenceCommand is RelayCommand sequence)
        {
            sequence.RaiseCanExecuteChanged();
        }

        if (ToggleFocusCommand is RelayCommand focus)
        {
            focus.RaiseCanExecuteChanged();
        }
    }

    public void Dispose()
    {
        StopPerformanceLoop();
        StopRefreshLoop();
        StopSequence();
        _alarmPopupAutoCloseCancellation?.Cancel();
        _alarmPopupAutoCloseCancellation?.Dispose();
        _alarmPopupAutoCloseCancellation = null;
        _loadCancellation?.Cancel();
        _loadCancellation?.Dispose();
        CancelPlaybackStart();
        CancelMosaicReconcile();
        ClearCameras(stopSlotPlayers: false);
        foreach (var slot in MosaicSlots.ToList())
        {
            slot.Dispose();
        }

        _apiClient.Dispose();
        _mosaicLayoutLock.Dispose();
    }


    private sealed class OperatorAppSettings
    {
        public string? ServerUrl { get; set; }

        public string? CurrentViewName { get; set; }

        public int GridSize { get; set; }

        public bool HideOffline { get; set; }

        public bool? BoxesEnabled { get; set; }

        public bool? SpotlightEnabled { get; set; }

        public bool? AlarmPopupEnabled { get; set; }

        public bool? PerformanceLogUploadEnabled { get; set; }

        public bool? AutoConnectOnStartup { get; set; }

        public bool? RestoreLastLayout { get; set; }

        public int? SequenceIntervalSeconds { get; set; }

        public bool? SequenceSavedViews { get; set; }

        public int? BoxRefreshIntervalMs { get; set; }

        public int? MinimumBoxConfidencePercent { get; set; }

        public bool? SidePanelExpanded { get; set; }

        public string? SidePanelMode { get; set; }

        public List<int?>? CameraIds { get; set; }

        public bool? RememberMe { get; set; }
        public string? Username { get; set; }
        public string? Password { get; set; }
    }
}

public class CameraGroupNode
{
    public string Name { get; set; } = "";
    public string Icon { get; set; } = "\uE8B7";
    public ObservableCollection<CameraGroupNode> SubNodes { get; } = new();
    public CameraTileViewModel? Camera { get; set; }
    public bool IsCamera => Camera is not null;
}
