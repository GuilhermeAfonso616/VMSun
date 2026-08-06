using System;
using System.IO;

namespace Analitico.Operator.DragTest;

internal static class DragTestLog
{
    private static readonly object Sync = new();
    private static string? _logPath;

    public static string LogPath
    {
        get
        {
            if (_logPath is not null)
            {
                return _logPath;
            }

            var dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "AnaliticoOperator",
                "logs");
            Directory.CreateDirectory(dir);
            _logPath = Path.Combine(dir, "operator-dragtest.log");
            return _logPath;
        }
    }

    public static void Info(string message)
    {
        Write("INFO", message);
    }

    public static void Error(string message, Exception? exception = null)
    {
        Write("ERROR", exception is null ? message : $"{message}{Environment.NewLine}{exception}");
    }

    private static void Write(string level, string message)
    {
        lock (Sync)
        {
            File.AppendAllText(
                LogPath,
                $"[{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss.fff zzz}] [{level}] {message}{Environment.NewLine}");
        }
    }
}
