using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
using Avalonia.Threading;
using Analitico.Operator.App.Services;

namespace Analitico.Operator.App;

public partial class App : Application
{
    public override void Initialize()
    {
        AppLogger.Info("Avalonia Initialize iniciado.");
        AvaloniaXamlLoader.Load(this);
        AppLogger.Info("Avalonia XAML carregado.");
    }

    public override void OnFrameworkInitializationCompleted()
    {
        Dispatcher.UIThread.UnhandledException += (_, eventArgs) =>
        {
            AppLogger.Error("Excecao no Dispatcher/UIThread.", eventArgs.Exception);
            Program.LogException(eventArgs.Exception);
            eventArgs.Handled = true;
        };

        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            desktop.Startup += (_, _) => AppLogger.Info("Desktop lifetime startup.");
            desktop.Exit += (_, args) => AppLogger.Shutdown($"Desktop lifetime exit. Codigo={args.ApplicationExitCode}");
            desktop.MainWindow = new LoginWindow();
            AppLogger.Info("LoginWindow criada.");
        }

        base.OnFrameworkInitializationCompleted();
    }
}
