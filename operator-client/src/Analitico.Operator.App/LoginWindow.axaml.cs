using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Threading;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Interactivity;
using Avalonia.Media.Imaging;
using Avalonia.Platform;
using Analitico.Operator.App.Services;

namespace Analitico.Operator.App;

public partial class LoginWindow : Window
{
    private readonly AnaliticoApiClient _apiClient = new();
    private readonly string _settingsPath;
    private readonly bool _suppressAutoLogin;
    private bool _autoLoginAttempted;

    public LoginWindow()
        : this(suppressAutoLogin: false)
    {
    }

    public LoginWindow(bool suppressAutoLogin)
    {
        InitializeComponent();

        _suppressAutoLogin = suppressAutoLogin;
        _settingsPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "AnaliticoOperator",
            "operator-settings.json");

        Opened += OnWindowOpened;
    }

    private void OnWindowOpened(object? sender, EventArgs e)
    {
        var rememberMe = false;
        try
        {
            if (File.Exists(_settingsPath))
            {
                var json = File.ReadAllText(_settingsPath);
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;

                if (root.TryGetProperty("ServerUrl", out var urlProp))
                {
                    ServerUrlTextBox.Text = urlProp.GetString();
                }

                if (root.TryGetProperty("Username", out var userProp))
                {
                    UsernameTextBox.Text = userProp.GetString();
                }

                if (root.TryGetProperty("Password", out var passProp))
                {
                    PasswordTextBox.Text = passProp.GetString();
                }

                if (root.TryGetProperty("RememberMe", out var remProp) && remProp.ValueKind == JsonValueKind.True)
                {
                    RememberMeCheckBox.IsChecked = true;
                    rememberMe = true;
                }
            }
        }
        catch (Exception ex)
        {
            AppLogger.Error("Erro ao carregar configuracoes de login", ex);
        }

        // Prefill default server if empty
        if (string.IsNullOrWhiteSpace(ServerUrlTextBox.Text))
        {
            ServerUrlTextBox.Text = "http://192.168.2.62:8000";
        }

        TryAutoLogin(rememberMe);
    }

    private async void TryAutoLogin(bool rememberMe)
    {
        if (_suppressAutoLogin || _autoLoginAttempted || !rememberMe)
        {
            return;
        }

        if (string.IsNullOrWhiteSpace(ServerUrlTextBox.Text)
            || string.IsNullOrWhiteSpace(UsernameTextBox.Text)
            || string.IsNullOrWhiteSpace(PasswordTextBox.Text))
        {
            return;
        }

        _autoLoginAttempted = true;
        AppLogger.Info("Sessao lembrada encontrada; iniciando login automatico.");
        await AttemptLoginAsync(isAutoLogin: true);
    }

    private async void OnLoginClick(object sender, RoutedEventArgs e)
    {
        await AttemptLoginAsync(isAutoLogin: false);
    }

    private async System.Threading.Tasks.Task AttemptLoginAsync(bool isAutoLogin)
    {
        var serverUrl = ServerUrlTextBox.Text?.Trim();
        var username = UsernameTextBox.Text?.Trim();
        var password = PasswordTextBox.Text;

        if (string.IsNullOrWhiteSpace(serverUrl))
        {
            ShowError("O endereço do servidor é obrigatório.");
            return;
        }
        if (string.IsNullOrWhiteSpace(username))
        {
            ShowError("O usuário é obrigatório.");
            return;
        }
        if (string.IsNullOrWhiteSpace(password))
        {
            ShowError("A senha é obrigatória.");
            return;
        }

        // Hide error and show loader
        ErrorBorder.IsVisible = false;
        LoginButton.IsEnabled = false;
        LoginProgressBar.IsVisible = true;

        var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        var (success, token, name, role, error) = await _apiClient.LoginAsync(serverUrl, username, password, cts.Token);

        if (success && !string.IsNullOrEmpty(token))
        {
            Session.Token = token;
            Session.ServerUrl = serverUrl;
            Session.Username = username;
            Session.UserName = name;
            Session.UserRole = role;

            SaveLoginSettings(serverUrl, username, password, RememberMeCheckBox.IsChecked == true);

            AppLogger.Info($"Login bem sucedido para o usuario '{username}' ({role})");

            // Open MainWindow and switch lifetime window reference
            var mainWin = new MainWindow();
            if (Application.Current?.ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
            {
                desktop.MainWindow = mainWin;
            }
            mainWin.Show();
            Close();
        }
        else
        {
            LoginButton.IsEnabled = true;
            LoginProgressBar.IsVisible = false;
            var message = error ?? "Falha na conexão com o servidor.";
            if (isAutoLogin)
            {
                AppLogger.Error($"Login automatico falhou: {message}");
                message = $"Reconexão automática falhou: {message}";
            }
            ShowError(message);
        }
    }

    private void ShowError(string msg)
    {
        ErrorTextBlock.Text = msg;
        ErrorBorder.IsVisible = true;
    }

    private void SaveLoginSettings(string serverUrl, string username, string password, bool rememberMe)
    {
        try
        {
            Dictionary<string, object> settingsDict;
            if (File.Exists(_settingsPath))
            {
                var json = File.ReadAllText(_settingsPath);
                settingsDict = JsonSerializer.Deserialize<Dictionary<string, object>>(json) ?? new();
            }
            else
            {
                settingsDict = new();
                var dir = Path.GetDirectoryName(_settingsPath);
                if (!string.IsNullOrEmpty(dir))
                {
                    Directory.CreateDirectory(dir);
                }
            }

            settingsDict["ServerUrl"] = serverUrl;
            settingsDict["RememberMe"] = rememberMe;
            
            if (rememberMe)
            {
                settingsDict["Username"] = username;
                settingsDict["Password"] = password;
            }
            else
            {
                settingsDict.Remove("Username");
                settingsDict.Remove("Password");
            }

            File.WriteAllText(_settingsPath, JsonSerializer.Serialize(settingsDict, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch (Exception ex)
        {
            AppLogger.Error("Erro ao salvar configuracoes de login", ex);
        }
    }

    private WindowIcon? CreateWindowIcon()
    {
        try
        {
            using var stream = AssetLoader.Open(new Uri("avares://Analitico.Operator.App/Assets/sunorus-logo.png"));
            return new WindowIcon(stream);
        }
        catch
        {
            return null;
        }
    }
}
