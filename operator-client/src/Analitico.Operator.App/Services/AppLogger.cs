using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;

namespace Analitico.Operator.App.Services;

internal static class AppLogger
{
    private static readonly object Sync = new();
    private static readonly string SessionId = DateTimeOffset.Now.ToString("yyyyMMdd-HHmmss-fff");
    private static int _initialized;

    public static string LogDirectory
    {
        get
        {
            var directory = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "AnaliticoOperator",
                "logs");

            Directory.CreateDirectory(directory);
            return directory;
        }
    }

    public static string LogPath => Path.Combine(LogDirectory, "operator-client.log");

    public static string CrashLogPath => Path.Combine(LogDirectory, "operator-crash.log");

    public static string SessionLogPath => Path.Combine(LogDirectory, $"session-{SessionId}.log");

    public static string TraceLogPath => Path.Combine(LogDirectory, "operator-trace.log");

    public static void Initialize()
    {
        if (Interlocked.Exchange(ref _initialized, 1) == 1)
        {
            return;
        }

        try
        {
            Trace.AutoFlush = true;
            Trace.Listeners.Add(new TextWriterTraceListener(TraceLogPath, "AnaliticoOperatorFileTrace"));
        }
        catch
        {
            // Logging cannot block app startup.
        }

        Info("============================================================");
        Info($"Sessao iniciada: {SessionId}");
        Info($"Versao: {Assembly.GetExecutingAssembly().GetName().Version}");
        Info($"Build do app: {AppBuildInfo.DetailedText}");
        Info($"Processo: {Environment.ProcessId}");
        Info($"SO: {Environment.OSVersion}");
        Info($".NET: {Environment.Version}");
        Info($"64 bits: processo={Environment.Is64BitProcess}; SO={Environment.Is64BitOperatingSystem}");
        Info($"Diretorio base: {AppContext.BaseDirectory}");
        Info($"Diretorio atual: {Environment.CurrentDirectory}");
        Info($"Logs: {LogDirectory}");
    }

    public static void Info(string message)
    {
        Write("INFO", message);
    }

    public static void Warn(string message)
    {
        Write("WARN", message);
    }

    public static void Error(string message, Exception? exception = null)
    {
        if (exception is null)
        {
            Write("ERROR", message);
            return;
        }

        Write("ERROR", $"{message}{Environment.NewLine}{exception}");
    }

    public static void Critical(string message, Exception? exception = null)
    {
        var body = exception is null ? message : $"{message}{Environment.NewLine}{exception}";
        Write("CRITICAL", body);
        WriteCrash(body);
    }

    public static void Shutdown(string reason)
    {
        Info($"Sessao encerrando: {reason}");
    }

    private static void Write(string level, string message)
    {
        try
        {
            var line =
                $"[{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss.fff zzz}] " +
                $"[{level}] " +
                $"[T{Environment.CurrentManagedThreadId}] " +
                $"{message}{Environment.NewLine}";

            lock (Sync)
            {
                File.AppendAllText(LogPath, line, Encoding.UTF8);
                File.AppendAllText(SessionLogPath, line, Encoding.UTF8);
                TrimIfNeededUnsafe();
            }
        }
        catch
        {
            // O log nunca pode derrubar o app do operador.
        }
    }

    private static void TrimIfNeededUnsafe()
    {
        TrimFileUnsafe(LogPath, "operator-client.old.log", 8 * 1024 * 1024);
        TrimFileUnsafe(TraceLogPath, "operator-trace.old.log", 8 * 1024 * 1024);
        TrimFileUnsafe(CrashLogPath, "operator-crash.old.log", 8 * 1024 * 1024);
    }

    private static void WriteCrash(string message)
    {
        try
        {
            var line =
                $"[{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss.fff zzz}] " +
                $"[SESSION {SessionId}] " +
                $"[PID {Environment.ProcessId}] " +
                $"{message}{Environment.NewLine}{Environment.NewLine}";

            lock (Sync)
            {
                File.AppendAllText(CrashLogPath, line, Encoding.UTF8);
                TrimIfNeededUnsafe();
            }
        }
        catch
        {
            // Logging cannot crash the app.
        }
    }

    private static void TrimFileUnsafe(string path, string rotatedName, long maxBytes)
    {
        var file = new FileInfo(path);

        if (!file.Exists || file.Length < maxBytes)
        {
            return;
        }

        var rotatedPath = Path.Combine(LogDirectory, rotatedName);

        if (File.Exists(rotatedPath))
        {
            File.Delete(rotatedPath);
        }

        File.Move(path, rotatedPath);
    }
}
