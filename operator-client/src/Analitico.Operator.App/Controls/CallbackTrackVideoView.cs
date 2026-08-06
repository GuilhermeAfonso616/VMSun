using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using Avalonia.Threading;
using LibVLCSharp.Shared;
using Analitico.Operator.App.Services;
using Analitico.Operator.App.ViewModels;

namespace Analitico.Operator.App.Controls;

public sealed class CallbackTrackVideoView : Control, IDisposable
{
    private const int MaxRenderFps = 20;
    private static readonly long MinimumFrameIntervalTicks = Stopwatch.Frequency / MaxRenderFps;

    public static readonly StyledProperty<string?> SourceUrlProperty =
        AvaloniaProperty.Register<CallbackTrackVideoView, string?>(nameof(SourceUrl));

    public static readonly StyledProperty<bool> IsActiveProperty =
        AvaloniaProperty.Register<CallbackTrackVideoView, bool>(nameof(IsActive));

    public static readonly StyledProperty<int> FrameWidthProperty =
        AvaloniaProperty.Register<CallbackTrackVideoView, int>(nameof(FrameWidth), 1152);

    public static readonly StyledProperty<int> FrameHeightProperty =
        AvaloniaProperty.Register<CallbackTrackVideoView, int>(nameof(FrameHeight), 1920);

    public static readonly StyledProperty<IEnumerable<TrackBoxViewModel>?> BoxesProperty =
        AvaloniaProperty.Register<CallbackTrackVideoView, IEnumerable<TrackBoxViewModel>?>(nameof(Boxes));

    public static readonly StyledProperty<int> RevisionProperty =
        AvaloniaProperty.Register<CallbackTrackVideoView, int>(nameof(Revision));

    public static readonly StyledProperty<Stretch> StretchProperty =
        AvaloniaProperty.Register<CallbackTrackVideoView, Stretch>(nameof(Stretch), Stretch.Uniform);

    private static readonly Lazy<LibVLC> SharedLibVlc = new(() => new LibVLC(
        "--no-audio",
        "--rtsp-tcp",
        "--network-caching=90",
        "--clock-jitter=0",
        "--clock-synchro=0"));

    private readonly object _sync = new();
    private readonly MediaPlayer.LibVLCVideoLockCb _lockCallback;
    private readonly MediaPlayer.LibVLCVideoUnlockCb _unlockCallback;
    private readonly MediaPlayer.LibVLCVideoDisplayCb _displayCallback;
    private readonly DispatcherTimer _reconcileTimer;
    private IntPtr _buffer;
    private int _bufferSize;
    private uint _pitch;
    private MediaPlayer? _player;
    private Media? _media;
    private WriteableBitmap? _bitmap;
    private byte[]? _frameBuffer;
    private TrackBoxSnapshot[] _boxSnapshot = Array.Empty<TrackBoxSnapshot>();
    private bool _frameQueued;
    private bool _isAttached;
    private bool _disposed;
    private string? _playingUrl;
    private int _playingWidth;
    private int _playingHeight;
    private long _receivedFrames;
    private long _renderedFrames;
    private long _lastAcceptedFrameTimestamp;

    static CallbackTrackVideoView()
    {
        AffectsRender<CallbackTrackVideoView>(BoxesProperty, RevisionProperty, StretchProperty);
        Task.Run(() =>
        {
            try
            {
                _ = SharedLibVlc.Value;
            }
            catch {}
        });
    }

    public CallbackTrackVideoView()
    {
        ClipToBounds = true;
        IsHitTestVisible = false;
        _lockCallback = LockVideo;
        _unlockCallback = UnlockVideo;
        _displayCallback = DisplayVideo;
        _reconcileTimer = new DispatcherTimer(DispatcherPriority.Background)
        {
            Interval = TimeSpan.FromMilliseconds(300),
        };
        _reconcileTimer.Tick += (_, _) =>
        {
            _reconcileTimer.Stop();
            ReconcilePlaybackNow();
        };
        AttachedToVisualTree += (_, _) =>
        {
            _isAttached = true;
            SchedulePlaybackReconcile();
        };
        DetachedFromVisualTree += (_, _) =>
        {
            _isAttached = false;
            _reconcileTimer.Stop();
            StopPlayback(clearFrame: true);
        };
    }

    public string? SourceUrl
    {
        get => GetValue(SourceUrlProperty);
        set => SetValue(SourceUrlProperty, value);
    }

    public bool IsActive
    {
        get => GetValue(IsActiveProperty);
        set => SetValue(IsActiveProperty, value);
    }

    public int FrameWidth
    {
        get => GetValue(FrameWidthProperty);
        set => SetValue(FrameWidthProperty, value);
    }

    public int FrameHeight
    {
        get => GetValue(FrameHeightProperty);
        set => SetValue(FrameHeightProperty, value);
    }

    public IEnumerable<TrackBoxViewModel>? Boxes
    {
        get => GetValue(BoxesProperty);
        set => SetValue(BoxesProperty, value);
    }

    public int Revision
    {
        get => GetValue(RevisionProperty);
        set => SetValue(RevisionProperty, value);
    }

    public Stretch Stretch
    {
        get => GetValue(StretchProperty);
        set => SetValue(StretchProperty, value);
    }

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (change.Property == SourceUrlProperty
            || change.Property == IsActiveProperty
            || change.Property == FrameWidthProperty
            || change.Property == FrameHeightProperty)
        {
            SchedulePlaybackReconcile();
        }

        if (change.Property == BoxesProperty || change.Property == RevisionProperty)
        {
            RefreshBoxSnapshot();
        }
    }

    public override void Render(DrawingContext context)
    {
        base.Render(context);
        context.FillRectangle(Brushes.Black, new Rect(Bounds.Size));

        var frameWidth = Math.Max(1, _playingWidth);
        var frameHeight = Math.Max(1, _playingHeight);
        var target = Stretch == Stretch.UniformToFill
            ? GetCoverRect(Bounds.Width, Bounds.Height, frameWidth, frameHeight)
            : GetContainRect(Bounds.Width, Bounds.Height, frameWidth, frameHeight);
        if (_bitmap is not null)
        {
            var source = new Rect(0, 0, _bitmap.PixelSize.Width, _bitmap.PixelSize.Height);
            context.DrawImage(_bitmap, source, target);
        }

        DrawBoxes(context, target, frameWidth, frameHeight);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        _reconcileTimer.Stop();
        StopPlayback(clearFrame: true);
    }

    private void SchedulePlaybackReconcile()
    {
        if (_disposed || !_isAttached)
        {
            return;
        }

        var url = SourceUrl?.Trim();
        if (!IsActive || string.IsNullOrWhiteSpace(url))
        {
            _reconcileTimer.Stop();
            StopPlayback(clearFrame: true);
            return;
        }

        _reconcileTimer.Stop();
        _reconcileTimer.Start();
    }

    private void ReconcilePlaybackNow()
    {
        if (_disposed || !_isAttached)
        {
            return;
        }

        var url = SourceUrl?.Trim();
        var active = IsActive && !string.IsNullOrWhiteSpace(url);
        var width = Math.Max(1, FrameWidth);
        var height = Math.Max(1, FrameHeight);
        if (!active)
        {
            StopPlayback(clearFrame: true);
            return;
        }

        if (_player is not null
            && string.Equals(_playingUrl, url, StringComparison.OrdinalIgnoreCase)
            && _playingWidth == width
            && _playingHeight == height)
        {
            return;
        }

        StartPlayback(url!, width, height);
    }

    private void StartPlayback(string url, int width, int height)
    {
        StopPlayback(clearFrame: false);

        _playingUrl = url;
        _playingWidth = width;
        _playingHeight = height;
        _pitch = checked((uint)(width * 4));
        _bufferSize = checked(width * height * 4);
        _buffer = Marshal.AllocHGlobal(_bufferSize);
        ZeroNativeBuffer(_buffer, _bufferSize);
        _frameBuffer = new byte[_bufferSize];
        _receivedFrames = 0;
        _renderedFrames = 0;
        _lastAcceptedFrameTimestamp = 0;
        _frameQueued = false;

        _player = new MediaPlayer(SharedLibVlc.Value);
        _player.EncounteredError += HandlePlaybackError;
        _player.Playing += HandlePlaying;
        _player.SetVideoFormat("RV32", (uint)width, (uint)height, _pitch);
        _player.SetVideoCallbacks(_lockCallback, _unlockCallback, _displayCallback);

        _media = new Media(SharedLibVlc.Value, url, FromType.FromLocation);
        if (url.StartsWith("rtsp", StringComparison.OrdinalIgnoreCase))
        {
            _media.AddOption(":rtsp-tcp");
        }

        _media.AddOption(":avcodec-hw=none");
        _media.AddOption(":network-caching=80");
        _media.AddOption(":live-caching=80");
        _media.AddOption(":drop-late-frames");
        _media.AddOption(":skip-frames");
        _media.AddOption(":clock-jitter=0");
        _media.AddOption(":clock-synchro=0");

        var ok = _player.Play(_media);
        AppLogger.Info($"CallbackTrackVideoView start: ok={ok}; url={url}; frame={width}x{height}");
        InvalidateVisual();
    }

    private void StopPlayback(bool clearFrame)
    {
        IntPtr bufferToFree = IntPtr.Zero;
        if (_player is not null)
        {
            _player.EncounteredError -= HandlePlaybackError;
            _player.Playing -= HandlePlaying;
            var playerToStop = _player;
            _player = null;

            bufferToFree = _buffer;
            _buffer = IntPtr.Zero;

            Task.Run(() =>
            {
                try
                {
                    playerToStop.Stop();
                    playerToStop.Dispose();
                }
                catch {}
                finally
                {
                    if (bufferToFree != IntPtr.Zero)
                    {
                        Marshal.FreeHGlobal(bufferToFree);
                    }
                }
            });
        }
        else
        {
            if (_buffer != IntPtr.Zero)
            {
                Marshal.FreeHGlobal(_buffer);
                _buffer = IntPtr.Zero;
            }
        }

        _media?.Dispose();
        _media = null;
        _playingUrl = null;
        lock (_sync)
        {
            _frameBuffer = null;
            _frameQueued = false;
        }

        if (clearFrame)
        {
            _bitmap?.Dispose();
            _bitmap = null;
        }

        InvalidateVisual();
    }

    private IntPtr LockVideo(IntPtr opaque, IntPtr planes)
    {
        if (_buffer == IntPtr.Zero)
        {
            return IntPtr.Zero;
        }

        Marshal.WriteIntPtr(planes, _buffer);
        return IntPtr.Zero;
    }

    private void UnlockVideo(IntPtr opaque, IntPtr picture, IntPtr planes)
    {
        if (_disposed || _buffer == IntPtr.Zero || _bufferSize <= 0)
        {
            return;
        }

        var received = Interlocked.Increment(ref _receivedFrames);
        var now = Stopwatch.GetTimestamp();
        lock (_sync)
        {
            if (_frameQueued
                || _frameBuffer is null
                || (_lastAcceptedFrameTimestamp > 0
                    && now - _lastAcceptedFrameTimestamp < MinimumFrameIntervalTicks))
            {
                return;
            }

            Marshal.Copy(_buffer, _frameBuffer, 0, _frameBuffer.Length);
            _lastAcceptedFrameTimestamp = now;
            _frameQueued = true;
        }

        Dispatcher.UIThread.Post(ApplyPendingFrame, DispatcherPriority.Render);
        if (received == 1)
        {
            AppLogger.Info($"CallbackTrackVideoView primeiro frame: url={_playingUrl ?? "-"}; frame={_playingWidth}x{_playingHeight}");
        }
    }

    private void DisplayVideo(IntPtr opaque, IntPtr picture)
    {
        // O frame ja foi copiado no UnlockVideo. Copiar aqui pode pegar o mesmo
        // buffer nativo enquanto o LibVLC prepara o proximo frame, causando ghost/flicker.
    }

    private void ApplyPendingFrame()
    {
        if (_disposed || _bufferSize <= 0)
        {
            return;
        }

        if (_bitmap is null
            || _bitmap.PixelSize.Width != _playingWidth
            || _bitmap.PixelSize.Height != _playingHeight)
        {
            _bitmap?.Dispose();
            _bitmap = new WriteableBitmap(
                new PixelSize(_playingWidth, _playingHeight),
                new Vector(96, 96),
                PixelFormat.Bgra8888,
                AlphaFormat.Opaque);
        }

        lock (_sync)
        {
            if (!_frameQueued || _frameBuffer is null)
            {
                return;
            }

            using var locked = _bitmap.Lock();
            if (locked.RowBytes == _pitch)
            {
                Marshal.Copy(_frameBuffer, 0, locked.Address, _frameBuffer.Length);
            }
            else
            {
                for (var row = 0; row < _playingHeight; row++)
                {
                    Marshal.Copy(
                        _frameBuffer,
                        checked((int)(row * _pitch)),
                        locked.Address + (row * locked.RowBytes),
                        checked((int)_pitch));
                }
            }

            _frameQueued = false;
        }

        var rendered = Interlocked.Increment(ref _renderedFrames);
        if (rendered % 90 == 0)
        {
            AppLogger.Info($"CallbackTrackVideoView frames: recebidos={_receivedFrames}; desenhados={rendered}; limite={MaxRenderFps}fps; url={_playingUrl ?? "-"}");
        }

        InvalidateVisual();
    }

    private static void ZeroNativeBuffer(IntPtr buffer, int size)
    {
        if (buffer == IntPtr.Zero || size <= 0)
        {
            return;
        }

        var zeros = new byte[Math.Min(size, 1024 * 1024)];
        var offset = 0;
        while (offset < size)
        {
            var count = Math.Min(zeros.Length, size - offset);
            Marshal.Copy(zeros, 0, buffer + offset, count);
            offset += count;
        }
    }

    private void RefreshBoxSnapshot()
    {
        _boxSnapshot = Boxes?
            .Select(box =>
            {
                var bbox = box.Source.Bbox;
                return bbox.Count == 4
                    ? new TrackBoxSnapshot(
                        bbox[0],
                        bbox[1],
                        Math.Max(1, bbox[2] - bbox[0]),
                        Math.Max(1, bbox[3] - bbox[1]),
                        box.Label,
                        box.StrokeBrush,
                        box.FillBrush,
                        box.LabelBrush)
                    : new TrackBoxSnapshot(
                        box.Left,
                        box.Top,
                        box.Width,
                        box.Height,
                        box.Label,
                        box.StrokeBrush,
                        box.FillBrush,
                        box.LabelBrush);
            })
            .ToArray()
            ?? Array.Empty<TrackBoxSnapshot>();
    }

    private void DrawBoxes(DrawingContext context, Rect target, double frameWidth, double frameHeight)
    {
        if (_boxSnapshot.Length == 0 || frameWidth <= 0 || frameHeight <= 0)
        {
            return;
        }

        var scaleX = target.Width / frameWidth;
        var scaleY = target.Height / frameHeight;
        var typeface = new Typeface("Verdana", FontStyle.Normal, FontWeight.Bold);
        foreach (var box in _boxSnapshot)
        {
            var left = target.X + (Math.Clamp(box.Left, 0, frameWidth) * scaleX);
            var top = target.Y + (Math.Clamp(box.Top, 0, frameHeight) * scaleY);
            var right = target.X + (Math.Clamp(box.Left + box.Width, 0, frameWidth) * scaleX);
            var bottom = target.Y + (Math.Clamp(box.Top + box.Height, 0, frameHeight) * scaleY);
            var rect = new Rect(left, top, Math.Max(1, right - left), Math.Max(1, bottom - top));
            context.DrawRectangle(box.FillBrush, new Pen(box.StrokeBrush, 2), rect);

            var text = new FormattedText(
                box.Label,
                CultureInfo.CurrentUICulture,
                FlowDirection.LeftToRight,
                typeface,
                10,
                Brushes.White);
            var labelWidth = Math.Max(18, (box.Label.Length * 6.5) + 8);
            var labelRect = new Rect(rect.Left, rect.Top, Math.Min(labelWidth, Math.Max(1, rect.Width)), 14);
            context.DrawRectangle(box.LabelBrush, null, labelRect);
            context.DrawText(text, new Point(labelRect.Left + 4, labelRect.Top + 1));
        }
    }

    private void HandlePlaybackError(object? sender, EventArgs args)
    {
        AppLogger.Warn($"CallbackTrackVideoView erro LibVLC: url={_playingUrl ?? "-"}; frame={_playingWidth}x{_playingHeight}");
    }

    private void HandlePlaying(object? sender, EventArgs args)
    {
        AppLogger.Info($"CallbackTrackVideoView playing: url={_playingUrl ?? "-"}; frame={_playingWidth}x{_playingHeight}");
    }

    private static Rect GetContainRect(double viewportWidth, double viewportHeight, double sourceWidth, double sourceHeight)
    {
        if (viewportWidth <= 0 || viewportHeight <= 0 || sourceWidth <= 0 || sourceHeight <= 0)
        {
            return new Rect(0, 0, Math.Max(1, viewportWidth), Math.Max(1, viewportHeight));
        }

        var scale = Math.Min(viewportWidth / sourceWidth, viewportHeight / sourceHeight);
        var width = sourceWidth * scale;
        var height = sourceHeight * scale;
        return new Rect((viewportWidth - width) / 2, (viewportHeight - height) / 2, width, height);
    }

    private static Rect GetCoverRect(double viewportWidth, double viewportHeight, double sourceWidth, double sourceHeight)
    {
        if (viewportWidth <= 0 || viewportHeight <= 0 || sourceWidth <= 0 || sourceHeight <= 0)
        {
            return new Rect(0, 0, Math.Max(1, viewportWidth), Math.Max(1, viewportHeight));
        }

        var scale = Math.Max(viewportWidth / sourceWidth, viewportHeight / sourceHeight);
        var width = sourceWidth * scale;
        var height = sourceHeight * scale;
        return new Rect((viewportWidth - width) / 2, (viewportHeight - height) / 2, width, height);
    }

    private readonly record struct TrackBoxSnapshot(
        double Left,
        double Top,
        double Width,
        double Height,
        string Label,
        IBrush StrokeBrush,
        IBrush FillBrush,
        IBrush LabelBrush);
}
