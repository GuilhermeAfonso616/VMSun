using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Avalonia.Platform;
using Avalonia.Threading;
using LibVLCSharp.Avalonia;

namespace Analitico.Operator.DragTest.Controls;

public sealed class NativeDragVideoView : VideoView
{
    private const int GwlpWndProc = -4;
    private const int WmLButtonDown = 0x0201;
    private const int WmLButtonUp = 0x0202;
    private const int WmMouseMove = 0x0200;
    private const int NativeDragThreshold = 5;

    private readonly Dictionary<IntPtr, IntPtr> _subclassedWindows = new();
    private IntPtr _rootHwnd;
    private WndProcDelegate? _wndProc;
    private DispatcherTimer? _childHookTimer;
    private bool _leftDown;
    private bool _dragging;
    private PointNative _downClient;

    public event EventHandler<NativeVideoDragEventArgs>? NativeDragStarted;

    public event EventHandler<NativeVideoDragEventArgs>? NativeDragCompleted;

    protected override IPlatformHandle CreateNativeControlCore(IPlatformHandle parent)
    {
        var handle = base.CreateNativeControlCore(parent);
        AttachNativeDrag(handle.Handle);
        return handle;
    }

    protected override void DestroyNativeControlCore(IPlatformHandle control)
    {
        DetachNativeDrag();
        base.DestroyNativeControlCore(control);
    }

    private void AttachNativeDrag(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero || _rootHwnd == hwnd)
        {
            return;
        }

        DetachNativeDrag();
        _rootHwnd = hwnd;
        _wndProc = WndProc;
        SubclassWindow(hwnd);
        HookChildWindows();

        _childHookTimer = new DispatcherTimer
        {
            Interval = TimeSpan.FromMilliseconds(350),
        };
        _childHookTimer.Tick += (_, _) => HookChildWindows();
        _childHookTimer.Start();
    }

    private void DetachNativeDrag()
    {
        _childHookTimer?.Stop();
        _childHookTimer = null;

        foreach (var entry in new List<KeyValuePair<IntPtr, IntPtr>>(_subclassedWindows))
        {
            if (IsWindow(entry.Key))
            {
                SetWindowLongPtr(entry.Key, GwlpWndProc, entry.Value);
            }
        }

        _subclassedWindows.Clear();
        _rootHwnd = IntPtr.Zero;
        _wndProc = null;
        _leftDown = false;
        _dragging = false;
    }

    private void HookChildWindows()
    {
        if (_rootHwnd == IntPtr.Zero || _wndProc is null)
        {
            return;
        }

        SubclassWindow(_rootHwnd);
        EnumChildWindows(
            _rootHwnd,
            (childHwnd, _) =>
            {
                SubclassWindow(childHwnd);
                return true;
            },
            IntPtr.Zero);
    }

    private void SubclassWindow(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero || _wndProc is null || _subclassedWindows.ContainsKey(hwnd))
        {
            return;
        }

        var oldWndProc = SetWindowLongPtr(hwnd, GwlpWndProc, Marshal.GetFunctionPointerForDelegate(_wndProc));
        if (oldWndProc != IntPtr.Zero)
        {
            _subclassedWindows[hwnd] = oldWndProc;
        }
    }

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam)
    {
        switch (msg)
        {
            case WmLButtonDown:
                _leftDown = true;
                _dragging = false;
                _downClient = LParamToPoint(lParam);
                SetCapture(hwnd);
                break;

            case WmMouseMove:
                if (_leftDown && !_dragging)
                {
                    var current = LParamToPoint(lParam);
                    if (Math.Abs(current.X - _downClient.X) >= NativeDragThreshold
                        || Math.Abs(current.Y - _downClient.Y) >= NativeDragThreshold)
                    {
                        _dragging = true;
                        RaiseNativeDrag(NativeDragStarted, hwnd, current);
                    }
                }

                break;

            case WmLButtonUp:
                if (_leftDown)
                {
                    var current = LParamToPoint(lParam);
                    var shouldComplete = _dragging;
                    _leftDown = false;
                    _dragging = false;
                    ReleaseCapture();
                    if (shouldComplete)
                    {
                        RaiseNativeDrag(NativeDragCompleted, hwnd, current);
                    }
                }

                break;
        }

        return _subclassedWindows.TryGetValue(hwnd, out var oldWndProc)
            ? CallWindowProc(oldWndProc, hwnd, msg, wParam, lParam)
            : DefWindowProc(hwnd, msg, wParam, lParam);
    }

    private void RaiseNativeDrag(EventHandler<NativeVideoDragEventArgs>? handler, IntPtr hwnd, PointNative clientPoint)
    {
        if (handler is null)
        {
            return;
        }

        var screenPoint = clientPoint;
        ClientToScreen(hwnd, ref screenPoint);
        Dispatcher.UIThread.Post(() => handler(this, new NativeVideoDragEventArgs(screenPoint.X, screenPoint.Y)));
    }

    private static PointNative LParamToPoint(IntPtr lParam)
    {
        var value = lParam.ToInt64();
        return new PointNative((short)(value & 0xffff), (short)((value >> 16) & 0xffff));
    }

    private static IntPtr SetWindowLongPtr(IntPtr hwnd, int index, IntPtr newProc)
    {
        return IntPtr.Size == 8
            ? SetWindowLongPtr64(hwnd, index, newProc)
            : new IntPtr(SetWindowLong32(hwnd, index, newProc.ToInt32()));
    }

    private delegate IntPtr WndProcDelegate(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam);

    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    private struct PointNative
    {
        public PointNative(int x, int y)
        {
            X = x;
            Y = y;
        }

        public int X;
        public int Y;
    }

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtr64(IntPtr hwnd, int index, IntPtr newProc);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern int SetWindowLong32(IntPtr hwnd, int index, int newProc);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr CallWindowProc(IntPtr oldProc, IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr DefWindowProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetCapture(IntPtr hwnd);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool ClientToScreen(IntPtr hwnd, ref PointNative point);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool EnumChildWindows(IntPtr hwndParent, EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool IsWindow(IntPtr hwnd);
}

public sealed class NativeVideoDragEventArgs : EventArgs
{
    public NativeVideoDragEventArgs(int screenX, int screenY)
    {
        ScreenX = screenX;
        ScreenY = screenY;
    }

    public int ScreenX { get; }

    public int ScreenY { get; }
}
