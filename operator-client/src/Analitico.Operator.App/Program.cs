using System;
using System.Threading.Tasks;
using Avalonia;
using LibVLCSharp.Shared;
using Analitico.Operator.App.Services;

namespace Analitico.Operator.App;

internal static class Program
{
    [STAThread]
    public static void Main(string[] args)
    {
        AppLogger.Initialize();
        AppDomain.CurrentDomain.UnhandledException += (_, eventArgs) =>
        {
            LogException(
                eventArgs.ExceptionObject as Exception ?? new Exception(Convert.ToString(eventArgs.ExceptionObject)),
                fatal: eventArgs.IsTerminating);
        };
        TaskScheduler.UnobservedTaskException += (_, eventArgs) =>
        {
            LogException(eventArgs.Exception);
            eventArgs.SetObserved();
        };
        AppDomain.CurrentDomain.ProcessExit += (_, _) => AppLogger.Shutdown("ProcessExit");
        AppLogger.Info("Handlers globais de excecao registrados.");
        AppLogger.Info("Inicializando Analitico Operator Client.");

        try
        {
            Core.Initialize();
            AppLogger.Info("LibVLC Core inicializado.");
            BuildAvaloniaApp().StartWithClassicDesktopLifetime(args);
            AppLogger.Shutdown("ClassicDesktopLifetime retornou");
        }
        catch (Exception exc)
        {
            LogException(exc, fatal: true);
            throw;
        }
    }

    public static AppBuilder BuildAvaloniaApp()
    {
        return AppBuilder.Configure<App>()
            .UsePlatformDetect()
            .WithInterFont()
            .LogToTrace();
    }

    internal static void LogException(Exception exception, bool fatal = false)
    {
        if (fatal)
        {
            AppLogger.Critical("Excecao fatal capturada pelo app.", exception);
            return;
        }

        AppLogger.Error("Excecao nao tratada capturada pelo app.", exception);
    }
}
