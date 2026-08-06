using System;
using System.Runtime.InteropServices;

/// <summary>
/// Caixas de dialogo via user32 em vez de WinForms: o instalador precisa de tres
/// mensagens simples, e carregar WinForms so por isso impedia o trimming e
/// custava dezenas de MB no executavel final.
/// </summary>
internal static class Dialogs
{
    private const uint MB_OK = 0x0000;
    private const uint MB_YESNO = 0x0004;
    private const uint MB_ICONERROR = 0x0010;
    private const uint MB_ICONQUESTION = 0x0020;
    private const uint MB_ICONINFORMATION = 0x0040;
    private const uint MB_SETFOREGROUND = 0x00010000;
    private const int IDYES = 6;

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);

    public static bool Confirmar(string texto, string titulo) =>
        MessageBoxW(IntPtr.Zero, texto, titulo, MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND) == IDYES;

    public static void Informar(string texto, string titulo) =>
        MessageBoxW(IntPtr.Zero, texto, titulo, MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND);

    public static void Erro(string texto, string titulo) =>
        MessageBoxW(IntPtr.Zero, texto, titulo, MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
}
