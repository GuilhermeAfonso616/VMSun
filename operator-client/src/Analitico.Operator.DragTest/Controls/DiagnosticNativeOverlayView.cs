using System;
using System.Runtime.InteropServices;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Platform;
using Avalonia.Threading;

namespace Analitico.Operator.DragTest.Controls;

public sealed class DiagnosticNativeOverlayView : NativeControlHost
{
    public static readonly StyledProperty<int> RevisionProperty =
        AvaloniaProperty.Register<DiagnosticNativeOverlayView, int>(nameof(Revision));

    private const int WsChild = 0x40000000;
    private const int WsVisible = 0x10000000;
    private const int WsExTransparent = 0x00000020;
    private const int GwlpWndProc = -4;
    private const int WmPaint = 0x000F;
    private const int WmEraseBkgnd = 0x0014;
    private const int WmNcHitTest = 0x0084;
    private const int HtTransparent = -1;
    private const int SwpNoMove = 0x0002;
    private const int SwpNoSize = 0x0001;
    private const int SwpNoActivate = 0x0010;
    private const int Transparent = 1;
    private const int NullBrush = 5;
    private const int DtLeft = 0x0000;
    private const int DtVCenter = 0x0004;
    private const int DtSingleLine = 0x0020;
    private const int DtNoPrefix = 0x0800;

    private static readonly IntPtr HwndTop = IntPtr.Zero;

    private IntPtr _hwnd;
    private IntPtr _oldWndProc;
    private WndProcDelegate? _wndProc;

    public DiagnosticNativeOverlayView()
    {
        IsHitTestVisible = false;
    }

    public int Revision
    {
        get => GetValue(RevisionProperty);
        set => SetValue(RevisionProperty, value);
    }

    protected override IPlatformHandle CreateNativeControlCore(IPlatformHandle parent)
    {
        _hwnd = CreateWindowEx(
            WsExTransparent,
            "STATIC",
            "",
            WsChild | WsVisible,
            0,
            0,
            1,
            1,
            parent.Handle,
            IntPtr.Zero,
            IntPtr.Zero,
            IntPtr.Zero);

        _wndProc = WndProc;
        _oldWndProc = SetWindowLongPtr(_hwnd, GwlpWndProc, Marshal.GetFunctionPointerForDelegate(_wndProc));
        SetWindowPos(_hwnd, HwndTop, 0, 0, 0, 0, SwpNoMove | SwpNoSize | SwpNoActivate);
        return new PlatformHandle(_hwnd, "HWND");
    }

    protected override void DestroyNativeControlCore(IPlatformHandle control)
    {
        if (_hwnd != IntPtr.Zero)
        {
            if (_oldWndProc != IntPtr.Zero)
            {
                SetWindowLongPtr(_hwnd, GwlpWndProc, _oldWndProc);
            }

            DestroyWindow(_hwnd);
        }

        _hwnd = IntPtr.Zero;
        _oldWndProc = IntPtr.Zero;
        _wndProc = null;
        base.DestroyNativeControlCore(control);
    }

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if ((change.Property == RevisionProperty || change.Property == IsVisibleProperty) && _hwnd != IntPtr.Zero)
        {
            Dispatcher.UIThread.Post(() =>
            {
                SetWindowPos(_hwnd, HwndTop, 0, 0, 0, 0, SwpNoMove | SwpNoSize | SwpNoActivate);
                InvalidateRect(_hwnd, IntPtr.Zero, true);
            }, DispatcherPriority.Render);
        }
    }

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam)
    {
        if (msg == WmEraseBkgnd)
        {
            return new IntPtr(1);
        }

        if (msg == WmNcHitTest)
        {
            return new IntPtr(HtTransparent);
        }

        if (msg == WmPaint)
        {
            Paint(hwnd);
            return IntPtr.Zero;
        }

        return _oldWndProc != IntPtr.Zero
            ? CallWindowProc(_oldWndProc, hwnd, msg, wParam, lParam)
            : DefWindowProc(hwnd, msg, wParam, lParam);
    }

    private void Paint(IntPtr hwnd)
    {
        var ps = new PaintStruct();
        var hdc = BeginPaint(hwnd, ref ps);
        try
        {
            GetClientRect(hwnd, out var bounds);
            SetBkMode(hdc, Transparent);

            var pen = CreatePen(0, 3, ColorRef(37, 99, 235));
            var labelBrush = CreateSolidBrush(ColorRef(37, 99, 235));
            var oldPen = SelectObject(hdc, pen);
            var oldBrush = SelectObject(hdc, GetStockObject(NullBrush));
            SetTextColor(hdc, ColorRef(255, 255, 255));

            DrawBox(hdc, bounds, 0.22, 0.22, 0.32, 0.58, "diagnostic A");
            DrawBox(hdc, bounds, 0.40, 0.35, 0.35, 0.50, "diagnostic B");

            var note = new NativeRect
            {
                Left = 8,
                Top = Math.Max(8, bounds.Bottom - 28),
                Right = Math.Max(140, bounds.Right - 8),
                Bottom = Math.Max(30, bounds.Bottom - 8),
            };
            FillRect(hdc, ref note, labelBrush);
            DrawText(hdc, "overlay nativo transparente", -1, ref note, DtLeft | DtVCenter | DtSingleLine | DtNoPrefix);

            SelectObject(hdc, oldBrush);
            SelectObject(hdc, oldPen);
            DeleteObject(labelBrush);
            DeleteObject(pen);
        }
        finally
        {
            EndPaint(hwnd, ref ps);
        }
    }

    private static void DrawBox(IntPtr hdc, NativeRect bounds, double x, double y, double width, double height, string label)
    {
        var left = bounds.Left + (int)Math.Round((bounds.Right - bounds.Left) * x);
        var top = bounds.Top + (int)Math.Round((bounds.Bottom - bounds.Top) * y);
        var right = left + (int)Math.Round((bounds.Right - bounds.Left) * width);
        var bottom = top + (int)Math.Round((bounds.Bottom - bounds.Top) * height);
        Rectangle(hdc, left, top, right, bottom);

        var labelBrush = CreateSolidBrush(ColorRef(37, 99, 235));
        var labelRect = new NativeRect
        {
            Left = left,
            Top = top,
            Right = Math.Min(right, left + Math.Max(70, label.Length * 8)),
            Bottom = top + 18,
        };
        FillRect(hdc, ref labelRect, labelBrush);
        labelRect.Left += 4;
        DrawText(hdc, label, -1, ref labelRect, DtLeft | DtVCenter | DtSingleLine | DtNoPrefix);
        DeleteObject(labelBrush);
    }

    private static int ColorRef(byte r, byte g, byte b)
    {
        return r | (g << 8) | (b << 16);
    }

    private delegate IntPtr WndProcDelegate(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    private struct PaintStruct
    {
        public IntPtr Hdc;
        public bool Erase;
        public NativeRect Paint;
        public bool Restore;
        public bool IncUpdate;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 32)]
        public byte[] Reserved;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateWindowEx(int exStyle, string className, string windowName, int style, int x, int y, int width, int height, IntPtr parent, IntPtr menu, IntPtr instance, IntPtr param);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool DestroyWindow(IntPtr hwnd);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetWindowPos(IntPtr hwnd, IntPtr hwndInsertAfter, int x, int y, int cx, int cy, int flags);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool InvalidateRect(IntPtr hwnd, IntPtr rect, bool erase);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool GetClientRect(IntPtr hwnd, out NativeRect rect);

    [DllImport("user32.dll")]
    private static extern IntPtr BeginPaint(IntPtr hwnd, ref PaintStruct paint);

    [DllImport("user32.dll")]
    private static extern bool EndPaint(IntPtr hwnd, ref PaintStruct paint);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtr64(IntPtr hwnd, int index, IntPtr newProc);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern int SetWindowLong32(IntPtr hwnd, int index, int newProc);

    private static IntPtr SetWindowLongPtr(IntPtr hwnd, int index, IntPtr newProc)
    {
        return IntPtr.Size == 8
            ? SetWindowLongPtr64(hwnd, index, newProc)
            : new IntPtr(SetWindowLong32(hwnd, index, newProc.ToInt32()));
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr CallWindowProc(IntPtr oldProc, IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr DefWindowProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam);

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern IntPtr CreatePen(int style, int width, int color);

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern IntPtr CreateSolidBrush(int color);

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern bool DeleteObject(IntPtr obj);

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern IntPtr SelectObject(IntPtr hdc, IntPtr obj);

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern IntPtr GetStockObject(int index);

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern int SetBkMode(IntPtr hdc, int mode);

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern int SetTextColor(IntPtr hdc, int color);

    [DllImport("gdi32.dll", SetLastError = true)]
    private static extern bool Rectangle(IntPtr hdc, int left, int top, int right, int bottom);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern int FillRect(IntPtr hdc, ref NativeRect rect, IntPtr brush);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern int DrawText(IntPtr hdc, string text, int count, ref NativeRect rect, int format);
}
