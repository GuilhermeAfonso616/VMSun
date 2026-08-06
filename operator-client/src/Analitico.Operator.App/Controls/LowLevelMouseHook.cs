using System;
using System.Runtime.InteropServices;

namespace Analitico.Operator.App.Controls;

public sealed class LowLevelMouseHook : IDisposable
{
    private const int WhMouseLl = 14;
    private const int WmMouseMove = 0x0200;
    private const int WmLButtonDown = 0x0201;
    private const int WmLButtonUp = 0x0202;

    private readonly LowLevelMouseProc _proc;
    private IntPtr _hookId;
    private bool _disposed;

    public LowLevelMouseHook()
    {
        _proc = HookCallback;
    }

    public event EventHandler<LowLevelMouseEventArgs>? LeftButtonDown;

    public event EventHandler<LowLevelMouseEventArgs>? LeftButtonUp;

    public event EventHandler<LowLevelMouseEventArgs>? MouseMove;

    public bool IsStarted => _hookId != IntPtr.Zero;

    public void Start()
    {
        if (_disposed || _hookId != IntPtr.Zero)
        {
            return;
        }

        _hookId = SetWindowsHookEx(WhMouseLl, _proc, IntPtr.Zero, 0);
        if (_hookId == IntPtr.Zero)
        {
            throw new InvalidOperationException($"Falha ao instalar hook global de mouse. Win32={Marshal.GetLastWin32Error()}");
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        if (_hookId != IntPtr.Zero)
        {
            UnhookWindowsHookEx(_hookId);
            _hookId = IntPtr.Zero;
        }
    }

    private IntPtr HookCallback(int nCode, IntPtr wParam, IntPtr lParam)
    {
        if (nCode >= 0)
        {
            var data = Marshal.PtrToStructure<MouseHookStruct>(lParam);
            var args = new LowLevelMouseEventArgs(data.Point.X, data.Point.Y);
            switch (wParam.ToInt32())
            {
                case WmLButtonDown:
                    LeftButtonDown?.Invoke(this, args);
                    break;
                case WmLButtonUp:
                    LeftButtonUp?.Invoke(this, args);
                    break;
                case WmMouseMove:
                    MouseMove?.Invoke(this, args);
                    break;
            }
        }

        return CallNextHookEx(_hookId, nCode, wParam, lParam);
    }

    private delegate IntPtr LowLevelMouseProc(int nCode, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    private struct MouseHookStruct
    {
        public PointNative Point;
        public uint MouseData;
        public uint Flags;
        public uint Time;
        public IntPtr ExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PointNative
    {
        public int X;
        public int Y;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetWindowsHookEx(int idHook, LowLevelMouseProc lpfn, IntPtr hMod, uint dwThreadId);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnhookWindowsHookEx(IntPtr hhk);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);
}

public sealed class LowLevelMouseEventArgs : EventArgs
{
    public LowLevelMouseEventArgs(int screenX, int screenY)
    {
        ScreenX = screenX;
        ScreenY = screenY;
    }

    public int ScreenX { get; }

    public int ScreenY { get; }
}
