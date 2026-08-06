using System;
using System.Runtime.InteropServices;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using Avalonia.Threading;
using LibVLCSharp.Shared;

namespace Analitico.Operator.DragTest.Controls;

public sealed class CallbackVideoOverlayView : Control, IDisposable
{
    private const uint DefaultWidth = 1152;
    private const uint DefaultHeight = 1920;

    private readonly LibVLC _libVlc = new("--no-audio", "--rtsp-tcp", "--network-caching=120", "--clock-jitter=0", "--clock-synchro=0");
    private readonly object _sync = new();
    private readonly int _bufferSize = checked((int)(DefaultWidth * DefaultHeight * 4));
    private readonly uint _pitch = DefaultWidth * 4;
    private readonly MediaPlayer.LibVLCVideoLockCb _lockCallback;
    private readonly MediaPlayer.LibVLCVideoUnlockCb _unlockCallback;
    private readonly MediaPlayer.LibVLCVideoDisplayCb _displayCallback;
    private IntPtr _buffer;
    private MediaPlayer? _player;
    private Media? _media;
    private WriteableBitmap? _bitmap;
    private byte[]? _pendingFrame;
    private bool _frameQueued;
    private bool _disposed;
    private long _receivedFrames;
    private long _renderedFrames;
    private DateTime _lastStatusAt = DateTime.MinValue;

    public CallbackVideoOverlayView()
    {
        ClipToBounds = true;
        IsHitTestVisible = false;
        _buffer = Marshal.AllocHGlobal(_bufferSize);
        _lockCallback = LockVideo;
        _unlockCallback = UnlockVideo;
        _displayCallback = DisplayVideo;
    }

    public event EventHandler<string>? StatusChanged;

    public void Start(string url)
    {
        Stop();
        _bitmap?.Dispose();
        _bitmap = null;
        _pendingFrame = null;
        _frameQueued = false;
        ZeroNativeBuffer(_buffer, _bufferSize);
        _receivedFrames = 0;
        _renderedFrames = 0;
        _lastStatusAt = DateTime.MinValue;
        RaiseStatus("callback: iniciado, aguardando primeiro frame");
        InvalidateVisual();
        _player = new MediaPlayer(_libVlc);
        _player.Playing += (_, _) => RaiseStatus("callback: player em Playing");
        _player.EncounteredError += (_, _) => RaiseStatus("callback: erro no player LibVLC");
        _player.SetVideoFormat("RV32", DefaultWidth, DefaultHeight, _pitch);
        _player.SetVideoCallbacks(_lockCallback, _unlockCallback, _displayCallback);
        _media = new Media(_libVlc, url, FromType.FromLocation);
        _media.AddOption(":rtsp-tcp");
        _media.AddOption(":avcodec-hw=none");
        _media.AddOption(":network-caching=80");
        _media.AddOption(":live-caching=80");
        _media.AddOption(":drop-late-frames");
        _media.AddOption(":skip-frames");
        _player.Play(_media);
    }

    public void Stop()
    {
        _player?.Stop();
        _media?.Dispose();
        _media = null;
        _player?.Dispose();
        _player = null;
        RaiseStatus("callback: parado");
    }

    public override void Render(DrawingContext context)
    {
        base.Render(context);

        if (_bitmap is not null)
        {
            var source = new Rect(0, 0, _bitmap.PixelSize.Width, _bitmap.PixelSize.Height);
            var target = GetContainRect(Bounds.Width, Bounds.Height, DefaultWidth, DefaultHeight);
            context.DrawImage(_bitmap, source, target);
            DrawDiagnosticBoxes(context, target);
        }
        else
        {
            context.FillRectangle(Brushes.Black, new Rect(Bounds.Size));
            DrawCenteredText(context, "CALLBACK AGUARDANDO FRAME", "se ficar aqui, o VLC nao entregou frame ao canvas");
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        Stop();
        _bitmap?.Dispose();
        if (_buffer != IntPtr.Zero)
        {
            Marshal.FreeHGlobal(_buffer);
            _buffer = IntPtr.Zero;
        }

        _libVlc.Dispose();
    }

    private IntPtr LockVideo(IntPtr opaque, IntPtr planes)
    {
        Marshal.WriteIntPtr(planes, _buffer);
        return IntPtr.Zero;
    }

    private void UnlockVideo(IntPtr opaque, IntPtr picture, IntPtr planes)
    {
        if (_disposed || _buffer == IntPtr.Zero)
        {
            return;
        }

        var frame = new byte[_bufferSize];
        Marshal.Copy(_buffer, frame, 0, frame.Length);
        var received = Interlocked.Increment(ref _receivedFrames);
        lock (_sync)
        {
            _pendingFrame = frame;
            if (_frameQueued)
            {
                return;
            }

            _frameQueued = true;
        }

        Dispatcher.UIThread.Post(ApplyPendingFrame, DispatcherPriority.Render);
        if (received == 1)
        {
            RaiseStatus("callback: primeiro frame recebido");
        }
    }

    private void DisplayVideo(IntPtr opaque, IntPtr picture)
    {
        // O snapshot do frame e feito no UnlockVideo para evitar copiar o buffer
        // enquanto o LibVLC escreve o proximo frame.
    }

    private void ApplyPendingFrame()
    {
        byte[]? frame;
        lock (_sync)
        {
            frame = _pendingFrame;
            _pendingFrame = null;
            _frameQueued = false;
        }

        if (frame is null || _disposed)
        {
            return;
        }

        _bitmap ??= new WriteableBitmap(
            new PixelSize((int)DefaultWidth, (int)DefaultHeight),
            new Vector(96, 96),
            PixelFormat.Bgra8888,
            AlphaFormat.Opaque);

        using (var locked = _bitmap.Lock())
        {
            if (locked.RowBytes == _pitch)
            {
                Marshal.Copy(frame, 0, locked.Address, frame.Length);
            }
            else
            {
                for (var row = 0; row < DefaultHeight; row++)
                {
                    Marshal.Copy(
                        frame,
                        checked((int)(row * _pitch)),
                        locked.Address + (row * locked.RowBytes),
                        checked((int)_pitch));
                }
            }
        }

        var rendered = Interlocked.Increment(ref _renderedFrames);
        if (rendered == 1 || rendered % 30 == 0)
        {
            RaiseStatus($"callback: frames recebidos={_receivedFrames}; desenhados={rendered}");
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

    private void RaiseStatus(string message)
    {
        var now = DateTime.UtcNow;
        if (now - _lastStatusAt < TimeSpan.FromMilliseconds(250)
            && !message.Contains("primeiro", StringComparison.OrdinalIgnoreCase)
            && !message.Contains("parado", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        _lastStatusAt = now;
        Dispatcher.UIThread.Post(() => StatusChanged?.Invoke(this, message));
    }

    private void DrawCenteredText(DrawingContext context, string title, string subtitle)
    {
        var titleText = new FormattedText(
            title,
            System.Globalization.CultureInfo.CurrentUICulture,
            FlowDirection.LeftToRight,
            new Typeface("Verdana", FontStyle.Normal, FontWeight.Bold),
            22,
            Brushes.White);
        var subtitleText = new FormattedText(
            subtitle,
            System.Globalization.CultureInfo.CurrentUICulture,
            FlowDirection.LeftToRight,
            new Typeface("Verdana", FontStyle.Normal, FontWeight.Bold),
            13,
            new SolidColorBrush(Color.FromRgb(147, 197, 253)));
        var centerX = Bounds.Width / 2;
        var centerY = Bounds.Height / 2;
        context.DrawText(titleText, new Point(centerX - (titleText.Width / 2), centerY - 24));
        context.DrawText(subtitleText, new Point(centerX - (subtitleText.Width / 2), centerY + 8));
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

    private static void DrawDiagnosticBoxes(DrawingContext context, Rect target)
    {
        var stroke = new SolidColorBrush(Color.FromRgb(37, 99, 235));
        var fill = new SolidColorBrush(Color.FromArgb(34, 37, 99, 235));
        var labelBrush = new SolidColorBrush(Color.FromRgb(37, 99, 235));
        DrawBox(context, target, stroke, fill, labelBrush, 0.08, 0.35, 0.76, 0.62, "callback A");
        DrawBox(context, target, stroke, fill, labelBrush, 0.48, 0.18, 0.48, 0.72, "callback B");
    }

    private static void DrawBox(
        DrawingContext context,
        Rect target,
        IBrush stroke,
        IBrush fill,
        IBrush labelBrush,
        double x,
        double y,
        double width,
        double height,
        string label)
    {
        var rect = new Rect(
            target.X + (target.Width * x),
            target.Y + (target.Height * y),
            target.Width * width,
            target.Height * height);
        context.DrawRectangle(fill, new Pen(stroke, 2), rect);
        var labelRect = new Rect(rect.X, rect.Y, Math.Min(rect.Width, 86), 18);
        context.DrawRectangle(labelBrush, null, labelRect);
        var text = new FormattedText(
            label,
            System.Globalization.CultureInfo.CurrentUICulture,
            FlowDirection.LeftToRight,
            new Typeface("Verdana", FontStyle.Normal, FontWeight.Bold),
            10,
            Brushes.White);
        context.DrawText(text, new Point(labelRect.X + 4, labelRect.Y + 2));
    }
}
