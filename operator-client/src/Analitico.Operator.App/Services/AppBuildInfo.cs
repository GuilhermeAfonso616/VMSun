using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;

namespace Analitico.Operator.App.Services;

internal static class AppBuildInfo
{
    public const string Version = "0.6.44";
    public const string ReleaseTag = "mosaic-manager";
    public const int OperatorApiVersion = 1;

    public static string ShortText => $"App v{Version} | build {BuildTimeText} | {ReleaseTag}";

    public static string DetailedText => $"{ShortText} | file {ExecutablePath}";

    private static string BuildTimeText
    {
        get
        {
            try
            {
                var path = ExecutablePath;
                if (!string.IsNullOrWhiteSpace(path) && File.Exists(path))
                {
                    return File.GetLastWriteTime(path).ToString("dd/MM HH:mm");
                }
            }
            catch
            {
                // Version display must never block startup.
            }

            return DateTime.Now.ToString("dd/MM HH:mm");
        }
    }

    private static string ExecutablePath
    {
        get
        {
            try
            {
                return Process.GetCurrentProcess().MainModule?.FileName
                    ?? Assembly.GetExecutingAssembly().Location;
            }
            catch
            {
                return Assembly.GetExecutingAssembly().Location;
            }
        }
    }
}
