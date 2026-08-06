using System;
using Avalonia;
using LibVLCSharp.Shared;

namespace Analitico.Operator.DragTest;

internal static class Program
{
    [STAThread]
    public static void Main(string[] args)
    {
        DragTestLog.Info("Inicializando Analitico Operator DragTest.");
        AppDomain.CurrentDomain.UnhandledException += (_, eventArgs) =>
        {
            DragTestLog.Error(
                $"Excecao fatal. terminating={eventArgs.IsTerminating}",
                eventArgs.ExceptionObject as Exception ?? new Exception(Convert.ToString(eventArgs.ExceptionObject)));
        };
        AppDomain.CurrentDomain.ProcessExit += (_, _) => DragTestLog.Info("ProcessExit DragTest.");

        try
        {
            Core.Initialize();
            DragTestLog.Info("LibVLC Core inicializado no DragTest.");
            BuildAvaloniaApp().StartWithClassicDesktopLifetime(args);
            DragTestLog.Info("DragTest lifetime retornou.");
        }
        catch (Exception exc)
        {
            DragTestLog.Error("Falha fatal ao iniciar DragTest.", exc);
            throw;
        }
    }

    private static AppBuilder BuildAvaloniaApp()
    {
        return AppBuilder.Configure<App>()
            .UsePlatformDetect()
            .WithInterFont()
            .LogToTrace();
    }
}
