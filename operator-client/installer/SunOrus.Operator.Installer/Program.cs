using System.Diagnostics;
using System.Drawing;
using System.IO.Compression;
using System.Reflection;
using System.Security.Principal;
using Microsoft.Win32;
using WinForms = System.Windows.Forms;

internal static class Program
{
    private const string AppName = "SunOrus Operator";
    private const string Publisher = "SunOrus";
    private const string MainExeName = "Analitico.Operator.App.exe";
    private const string RegistryKeyName = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\SunOrus Operator";

    [STAThread]
    private static int Main(string[] args)
    {
        var options = InstallerOptions.Parse(args);

        if (options.Silent || options.Uninstall)
        {
            return RunHeadless(options);
        }

        WinForms.Application.EnableVisualStyles();
        WinForms.Application.SetCompatibleTextRenderingDefault(false);
        using (var languageDialog = new LanguageDialog())
        {
            if (languageDialog.ShowDialog() != WinForms.DialogResult.OK)
            {
                return 1;
            }
        }

        WinForms.Application.Run(new InstallerWizardForm(options));
        return Environment.ExitCode;
    }

    private static int RunHeadless(InstallerOptions options)
    {
        try
        {
            if (options.Uninstall)
            {
                Uninstall(options, null);
            }
            else
            {
                Install(options, null);
            }

            return 0;
        }
        catch (Exception exc)
        {
            if (!options.Silent)
            {
                WinForms.MessageBox.Show(
                    exc.Message,
                    "Falha no instalador",
                    WinForms.MessageBoxButtons.OK,
                    WinForms.MessageBoxIcon.Error);
            }

            return 1;
        }
    }

    private static void Install(InstallerOptions options, Action<int, string>? progress)
    {
        var installDirectory = ResolveInstallDirectory(options);
        var mainExe = Path.Combine(installDirectory, MainExeName);

        try
        {
            var processes = Process.GetProcessesByName("Analitico.Operator.App");
            if (processes.Length > 0)
            {
                if (options.Silent)
                {
                    progress?.Invoke(2, "Fechando aplicativo Operator aberto...");
                    foreach (var p in processes)
                    {
                        p.Kill();
                        p.WaitForExit(3000);
                    }
                }
                else
                {
                    var dr = WinForms.MessageBox.Show(
                        "O SunOrus Operator está em execução e precisa ser fechado para continuar. Deseja fechar o aplicativo automaticamente agora?",
                        "Aplicativo em Execução",
                        WinForms.MessageBoxButtons.YesNo,
                        WinForms.MessageBoxIcon.Warning);

                    if (dr == WinForms.DialogResult.Yes)
                    {
                        progress?.Invoke(2, "Fechando aplicativo Operator...");
                        foreach (var p in processes)
                        {
                            try
                            {
                                p.Kill();
                                p.WaitForExit(3000);
                            }
                            catch { }
                        }
                    }
                    else
                    {
                        throw new OperationCanceledException("Instalação cancelada pelo usuário pois o aplicativo está em execução.");
                    }
                }
            }
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception ex)
        {
            progress?.Invoke(2, $"Aviso ao verificar processo: {ex.Message}");
        }

        if (File.Exists(mainExe) && !options.Silent)
        {
            var dr = WinForms.MessageBox.Show(
                "Uma versão anterior do SunOrus Operator foi encontrada nesta pasta.\nDeseja sobrescrevê-la para atualizar para a nova versão?",
                "Atualizar Versão Anterior",
                WinForms.MessageBoxButtons.YesNo,
                WinForms.MessageBoxIcon.Question);

            if (dr != WinForms.DialogResult.Yes)
            {
                throw new OperationCanceledException("Instalação cancelada pelo usuário.");
            }
        }

        var tempPayload = Path.Combine(Path.GetTempPath(), $"sunorus-operator-payload-{Guid.NewGuid():N}.zip");

        progress?.Invoke(5, "Preparando pasta de instalacao...");
        Directory.CreateDirectory(installDirectory);

        progress?.Invoke(12, "Lendo pacote do aplicativo...");
        ExtractPayloadResource(tempPayload);

        try
        {
            progress?.Invoke(22, "Copiando arquivos do SunOrus Operator...");
            ExtractPayload(tempPayload, installDirectory, progress);
        }
        finally
        {
            TryDeleteFile(tempPayload);
        }

        mainExe = Path.Combine(installDirectory, MainExeName);
        if (!File.Exists(mainExe))
        {
            throw new FileNotFoundException("Executavel principal nao encontrado no payload.", mainExe);
        }

        progress?.Invoke(86, "Configurando atalhos e desinstalador...");
        var installedInstaller = CopyInstallerToInstallDirectory(installDirectory);

        WriteInstallInfo(installDirectory, options, installedInstaller);
        WriteUninstaller(installDirectory, installedInstaller);
        CreateShortcuts(installDirectory, mainExe, options);
        WriteUninstallRegistry(installDirectory, mainExe);

        progress?.Invoke(100, "Instalacao concluida.");

        if (options.Launch)
        {
            Process.Start(new ProcessStartInfo(mainExe)
            {
                WorkingDirectory = installDirectory,
                UseShellExecute = true,
            });
        }
    }

    private static void Uninstall(InstallerOptions options, Action<int, string>? progress)
    {
        var installDirectory = ResolveInstalledDirectory(options);
        progress?.Invoke(20, "Removendo atalhos...");

        DeleteShortcuts();
        Registry.CurrentUser.DeleteSubKeyTree(RegistryKeyName, throwOnMissingSubKey: false);

        progress?.Invoke(65, "Removendo arquivos instalados...");
        if (Directory.Exists(installDirectory))
        {
            var currentExe = Environment.ProcessPath ?? "";
            var currentDir = Path.GetDirectoryName(currentExe) ?? "";
            if (Path.GetFullPath(currentDir).TrimEnd(Path.DirectorySeparatorChar).Equals(
                    Path.GetFullPath(installDirectory).TrimEnd(Path.DirectorySeparatorChar),
                    StringComparison.OrdinalIgnoreCase))
            {
                ScheduleSelfDelete(installDirectory);
            }
            else
            {
                Directory.Delete(installDirectory, recursive: true);
            }
        }

        progress?.Invoke(100, "Remocao concluida.");
    }

    private static string ResolveInstallDirectory(InstallerOptions options)
    {
        if (!string.IsNullOrWhiteSpace(options.InstallDirectory))
        {
            return Path.GetFullPath(options.InstallDirectory);
        }

        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RegistryKeyName);
            var registryPath = key?.GetValue("InstallLocation") as string;
            if (!string.IsNullOrWhiteSpace(registryPath) && Directory.Exists(registryPath))
            {
                return registryPath;
            }
        }
        catch { }

        var baseDirectory = IsAdministrator()
            ? Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles)
            : Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs");

        return Path.Combine(baseDirectory, "SunOrus", "Operator");
    }

    private static string ResolveInstalledDirectory(InstallerOptions options)
    {
        if (!string.IsNullOrWhiteSpace(options.InstallDirectory))
        {
            return Path.GetFullPath(options.InstallDirectory);
        }

        using var key = Registry.CurrentUser.OpenSubKey(RegistryKeyName);
        var registryPath = key?.GetValue("InstallLocation") as string;
        return !string.IsNullOrWhiteSpace(registryPath)
            ? registryPath
            : ResolveInstallDirectory(options);
    }

    private static void ExtractPayloadResource(string destinationZip)
    {
        var assembly = Assembly.GetExecutingAssembly();
        var resourceName = assembly.GetManifestResourceNames()
            .FirstOrDefault(name => name.EndsWith("AnaliticoOperatorPayload.zip", StringComparison.OrdinalIgnoreCase));

        if (resourceName is null)
        {
            throw new InvalidOperationException("Payload do aplicativo nao foi embutido no instalador.");
        }

        using var resource = assembly.GetManifestResourceStream(resourceName)
            ?? throw new InvalidOperationException("Nao foi possivel abrir o payload embutido.");
        using var file = File.Create(destinationZip);
        resource.CopyTo(file);
    }

    private static void ExtractPayload(string payloadZip, string installDirectory, Action<int, string>? progress)
    {
        using var archive = ZipFile.OpenRead(payloadZip);
        var entries = archive.Entries.Where(entry => !string.IsNullOrEmpty(entry.Name)).ToList();
        var total = Math.Max(1, entries.Count);
        var index = 0;

        foreach (var entry in archive.Entries)
        {
            var destination = Path.GetFullPath(Path.Combine(installDirectory, entry.FullName));
            if (!destination.StartsWith(Path.GetFullPath(installDirectory), StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException($"Entrada invalida no payload: {entry.FullName}");
            }

            if (string.IsNullOrEmpty(entry.Name))
            {
                Directory.CreateDirectory(destination);
                continue;
            }

            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            if (File.Exists(destination))
            {
                try
                {
                    File.SetAttributes(destination, FileAttributes.Normal);
                    File.Delete(destination);
                }
                catch { }
            }
            entry.ExtractToFile(destination, overwrite: true);
            index++;

            if (index % 12 == 0 || index == total)
            {
                var value = 22 + (int)Math.Round(index / (double)total * 60);
                progress?.Invoke(Math.Min(82, value), $"Copiando arquivos... {index}/{total}");
            }
        }
    }

    private static string CopyInstallerToInstallDirectory(string installDirectory)
    {
        var target = Path.Combine(installDirectory, "SunOrus.Operator.Setup.exe");
        var source = Environment.ProcessPath;
        if (string.IsNullOrWhiteSpace(source) || !File.Exists(source))
        {
            return target;
        }

        if (!Path.GetFullPath(source).Equals(Path.GetFullPath(target), StringComparison.OrdinalIgnoreCase))
        {
            File.Copy(source, target, overwrite: true);
        }

        return target;
    }

    private static void WriteInstallInfo(string installDirectory, InstallerOptions options, string installedInstaller)
    {
        var text = $$"""
        {
          "app": "{{AppName}}",
          "installed_at": "{{DateTimeOffset.Now:O}}",
          "install_directory": "{{EscapeJson(installDirectory)}}",
          "installer": "{{EscapeJson(installedInstaller)}}",
          "scope": "{{(IsAdministrator() ? "machine-preferred" : "user")}}",
          "desktop_shortcut": {{(!options.NoDesktopShortcut).ToString().ToLowerInvariant()}}
        }
        """;

        File.WriteAllText(Path.Combine(installDirectory, "install-info.json"), text);
    }

    private static void WriteUninstaller(string installDirectory, string installedInstaller)
    {
        var installerPath = Path.Combine(installDirectory, "SunOrus.Operator.Uninstall.cmd");
        var uninstallCommand = $$"""
        @echo off
        setlocal
        "{{installedInstaller}}" --uninstall --dir="{{installDirectory}}"
        endlocal
        """;
        File.WriteAllText(installerPath, uninstallCommand);
    }

    private static void CreateShortcuts(string installDirectory, string mainExe, InstallerOptions options)
    {
        var startMenuDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.StartMenu),
            "Programs",
            "SunOrus");
        Directory.CreateDirectory(startMenuDir);

        CreateShortcut(
            Path.Combine(startMenuDir, $"{AppName}.lnk"),
            mainExe,
            installDirectory,
            $"{AppName} - VMS + Analytics");

        if (!options.NoDesktopShortcut)
        {
            CreateShortcut(
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), $"{AppName}.lnk"),
                mainExe,
                installDirectory,
                $"{AppName} - VMS + Analytics");
        }
    }

    private static void CreateShortcut(string shortcutPath, string targetPath, string workingDirectory, string description)
    {
        var shellType = Type.GetTypeFromProgID("WScript.Shell")
            ?? throw new InvalidOperationException("WScript.Shell indisponivel para criar atalhos.");
        dynamic shell = Activator.CreateInstance(shellType)
            ?? throw new InvalidOperationException("Nao foi possivel criar WScript.Shell.");
        dynamic shortcut = shell.CreateShortcut(shortcutPath);
        shortcut.TargetPath = targetPath;
        shortcut.WorkingDirectory = workingDirectory;
        shortcut.Description = description;
        shortcut.IconLocation = targetPath;
        shortcut.Save();
    }

    private static void DeleteShortcuts()
    {
        TryDeleteFile(Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.StartMenu),
            "Programs",
            "SunOrus",
            $"{AppName}.lnk"));
        TryDeleteFile(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), $"{AppName}.lnk"));
    }

    private static void WriteUninstallRegistry(string installDirectory, string mainExe)
    {
        using var key = Registry.CurrentUser.CreateSubKey(RegistryKeyName);
        key.SetValue("DisplayName", AppName);
        key.SetValue("Publisher", Publisher);
        key.SetValue("DisplayVersion", Assembly.GetExecutingAssembly().GetName().Version?.ToString() ?? "");
        key.SetValue("InstallLocation", installDirectory);
        key.SetValue("DisplayIcon", mainExe);
        key.SetValue("UninstallString", $"\"{Path.Combine(installDirectory, "SunOrus.Operator.Uninstall.cmd")}\"");
        key.SetValue("QuietUninstallString", $"\"{Path.Combine(installDirectory, "SunOrus.Operator.Uninstall.cmd")}\"");
        key.SetValue("NoModify", 1, RegistryValueKind.DWord);
        key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
        key.SetValue("EstimatedSize", EstimateInstalledSizeKb(installDirectory), RegistryValueKind.DWord);
    }

    private static int EstimateInstalledSizeKb(string installDirectory)
    {
        long bytes = Directory.EnumerateFiles(installDirectory, "*", SearchOption.AllDirectories)
            .Sum(path => new FileInfo(path).Length);
        return (int)Math.Max(1, bytes / 1024);
    }

    private static void ScheduleSelfDelete(string installDirectory)
    {
        var script = Path.Combine(Path.GetTempPath(), $"sunorus-uninstall-{Guid.NewGuid():N}.cmd");
        File.WriteAllText(script, $$"""
        @echo off
        timeout /t 2 /nobreak >nul
        rmdir /s /q "{{installDirectory}}"
        del "%~f0"
        """);

        Process.Start(new ProcessStartInfo("cmd.exe", $"/c \"{script}\"")
        {
            CreateNoWindow = true,
            UseShellExecute = false,
        });
    }

    private static bool IsAdministrator()
    {
        using var identity = WindowsIdentity.GetCurrent();
        return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
    }

    private static void TryDeleteFile(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch
        {
            // Best effort cleanup.
        }
    }

    private static string EscapeJson(string value)
    {
        return value.Replace("\\", "\\\\", StringComparison.Ordinal).Replace("\"", "\\\"", StringComparison.Ordinal);
    }

    private enum WizardPage
    {
        License,
        Destination,
        Options,
        Installing,
        Finish,
    }

    private sealed class LanguageDialog : WinForms.Form
    {
        public LanguageDialog()
        {
            Text = "Selecione o Idioma do Instalador";
            StartPosition = WinForms.FormStartPosition.CenterScreen;
            FormBorderStyle = WinForms.FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(390, 150);
            Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            Icon = Icon.ExtractAssociatedIcon(Environment.ProcessPath ?? WinForms.Application.ExecutablePath);

            Controls.Add(new WinForms.PictureBox
            {
                Image = SystemIcons.Application.ToBitmap(),
                Location = new Point(22, 30),
                Size = new Size(40, 40),
                SizeMode = WinForms.PictureBoxSizeMode.CenterImage,
            });

            Controls.Add(new WinForms.Label
            {
                Text = "Selecione o idioma para usar durante a instalacao:",
                Location = new Point(78, 28),
                Size = new Size(285, 40),
            });

            var combo = new WinForms.ComboBox
            {
                DropDownStyle = WinForms.ComboBoxStyle.DropDownList,
                Location = new Point(78, 78),
                Size = new Size(265, 24),
            };
            combo.Items.Add("Portugues Brasileiro");
            combo.SelectedIndex = 0;
            Controls.Add(combo);

            var okButton = new WinForms.Button
            {
                Text = "OK",
                Location = new Point(166, 114),
                Size = new Size(82, 27),
                DialogResult = WinForms.DialogResult.OK,
            };
            Controls.Add(okButton);

            var cancelButton = new WinForms.Button
            {
                Text = "Cancelar",
                Location = new Point(258, 114),
                Size = new Size(82, 27),
                DialogResult = WinForms.DialogResult.Cancel,
            };
            Controls.Add(cancelButton);

            AcceptButton = okButton;
            CancelButton = cancelButton;
        }
    }

    private sealed class InstallerWizardForm : WinForms.Form
    {
        private readonly InstallerOptions _initialOptions;
        private readonly WinForms.TextBox _installPathBox = new();
        private readonly WinForms.CheckBox _desktopShortcutBox = new();
        private readonly WinForms.CheckBox _launchBox = new();
        private readonly WinForms.ProgressBar _progressBar = new();
        private readonly WinForms.Label _statusLabel = new();
        private readonly WinForms.Button _backButton = new();
        private readonly WinForms.Button _nextButton = new();
        private readonly WinForms.Button _cancelButton = new();
        private readonly WinForms.Label _headerTitle = new();
        private readonly WinForms.Label _headerSubtitle = new();
        private readonly WinForms.Panel _contentPanel = new();
        private readonly WinForms.RadioButton _acceptLicenseRadio = new();
        private readonly WinForms.RadioButton _rejectLicenseRadio = new();
        private WizardPage _page = WizardPage.License;
        private bool _finished;

        public InstallerWizardForm(InstallerOptions options)
        {
            _initialOptions = options;
            ConfigureWindow();
            BuildLayout();
            RenderPage();
        }

        private void ConfigureWindow()
        {
            Text = "SunOrus Operator 0.6.29 - Instalador";
            StartPosition = WinForms.FormStartPosition.CenterScreen;
            FormBorderStyle = WinForms.FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            MinimizeBox = true;
            ClientSize = new Size(520, 390);
            BackColor = Color.FromArgb(240, 240, 240);
            Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            Icon = Icon.ExtractAssociatedIcon(Environment.ProcessPath ?? WinForms.Application.ExecutablePath);
        }

        private void BuildLayout()
        {
            var header = new WinForms.Panel
            {
                Dock = WinForms.DockStyle.Top,
                Height = 82,
                BackColor = Color.White,
            };
            Controls.Add(header);

            var logo = new WinForms.PictureBox
            {
                Image = LoadLogoImage(),
                SizeMode = WinForms.PictureBoxSizeMode.Zoom,
                Location = new Point(450, 13),
                Size = new Size(54, 54),
            };
            header.Controls.Add(logo);

            _headerTitle.Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point);
            _headerTitle.ForeColor = Color.Black;
            _headerTitle.Location = new Point(24, 15);
            _headerTitle.Size = new Size(390, 22);
            header.Controls.Add(_headerTitle);

            _headerSubtitle.ForeColor = Color.Black;
            _headerSubtitle.Location = new Point(42, 38);
            _headerSubtitle.Size = new Size(375, 34);
            header.Controls.Add(_headerSubtitle);

            var separator = new WinForms.Panel
            {
                Dock = WinForms.DockStyle.Top,
                Height = 1,
                BackColor = Color.FromArgb(176, 176, 176),
            };
            Controls.Add(separator);
            separator.BringToFront();

            _contentPanel.Dock = WinForms.DockStyle.Fill;
            _contentPanel.BackColor = Color.FromArgb(240, 240, 240);
            _contentPanel.Padding = new WinForms.Padding(24, 18, 24, 0);
            Controls.Add(_contentPanel);

            var footer = new WinForms.Panel
            {
                Dock = WinForms.DockStyle.Bottom,
                Height = 48,
                BackColor = Color.FromArgb(240, 240, 240),
            };
            Controls.Add(footer);
            footer.BringToFront();

            footer.Controls.Add(new WinForms.Panel
            {
                Dock = WinForms.DockStyle.Top,
                Height = 1,
                BackColor = Color.FromArgb(176, 176, 176),
            });

            footer.Controls.Add(new WinForms.Label
            {
                Text = "Portugues",
                ForeColor = Color.Gray,
                Location = new Point(8, 18),
                Size = new Size(110, 20),
            });

            _backButton.Text = "< Voltar";
            _backButton.Location = new Point(265, 12);
            _backButton.Size = new Size(78, 27);
            _backButton.Click += (_, _) => MoveBack();
            footer.Controls.Add(_backButton);

            _nextButton.Text = "Proximo >";
            _nextButton.Location = new Point(348, 12);
            _nextButton.Size = new Size(82, 27);
            _nextButton.Click += (_, _) => _ = MoveNextAsync();
            footer.Controls.Add(_nextButton);

            _cancelButton.Text = "Cancelar";
            _cancelButton.Location = new Point(436, 12);
            _cancelButton.Size = new Size(74, 27);
            _cancelButton.Click += (_, _) => Close();
            footer.Controls.Add(_cancelButton);
        }

        private void RenderPage()
        {
            _contentPanel.Controls.Clear();
            _backButton.Enabled = _page is WizardPage.Destination or WizardPage.Options;
            _cancelButton.Visible = _page != WizardPage.Finish;
            _nextButton.Enabled = true;

            switch (_page)
            {
                case WizardPage.License:
                    RenderLicensePage();
                    break;
                case WizardPage.Destination:
                    RenderDestinationPage();
                    break;
                case WizardPage.Options:
                    RenderOptionsPage();
                    break;
                case WizardPage.Installing:
                    RenderInstallingPage();
                    break;
                case WizardPage.Finish:
                    RenderFinishPage();
                    break;
            }
        }

        private void RenderLicensePage()
        {
            _headerTitle.Text = "Acordo de Licenca";
            _headerSubtitle.Text = "Por favor leia as seguintes informacoes importantes antes de continuar.";
            _nextButton.Text = "Proximo >";
            _nextButton.Enabled = _acceptLicenseRadio.Checked;

            _contentPanel.Controls.Add(new WinForms.Label
            {
                Text = "Por favor leia o seguinte Acordo de Licenca. Voce deve aceitar os termos deste acordo antes de continuar com a instalacao.",
                Location = new Point(40, 16),
                Size = new Size(430, 40),
            });

            var licenseBox = new WinForms.TextBox
            {
                Multiline = true,
                ReadOnly = true,
                ScrollBars = WinForms.ScrollBars.Vertical,
                Location = new Point(40, 66),
                Size = new Size(420, 146),
                Text = LicenseText,
            };
            _contentPanel.Controls.Add(licenseBox);

            _acceptLicenseRadio.Text = "Eu aceito o acordo";
            _acceptLicenseRadio.Location = new Point(40, 222);
            _acceptLicenseRadio.Size = new Size(180, 24);
            _acceptLicenseRadio.CheckedChanged -= LicenseChanged;
            _acceptLicenseRadio.CheckedChanged += LicenseChanged;
            _contentPanel.Controls.Add(_acceptLicenseRadio);

            _rejectLicenseRadio.Text = "Eu nao aceito o acordo";
            _rejectLicenseRadio.Location = new Point(40, 246);
            _rejectLicenseRadio.Size = new Size(210, 24);
            if (!_acceptLicenseRadio.Checked)
            {
                _rejectLicenseRadio.Checked = true;
            }
            _contentPanel.Controls.Add(_rejectLicenseRadio);
        }

        private void RenderDestinationPage()
        {
            _headerTitle.Text = "Selecione o Local de Destino";
            _headerSubtitle.Text = "Aonde o SunOrus Operator deve ser instalado?";
            _nextButton.Text = "Proximo >";

            _contentPanel.Controls.Add(new WinForms.PictureBox
            {
                Image = SystemIcons.WinLogo.ToBitmap(),
                Location = new Point(40, 36),
                Size = new Size(38, 38),
                SizeMode = WinForms.PictureBoxSizeMode.CenterImage,
            });

            _contentPanel.Controls.Add(new WinForms.Label
            {
                Text = "O Instalador instalara o SunOrus Operator na seguinte pasta.",
                Location = new Point(92, 42),
                Size = new Size(370, 26),
            });

            _contentPanel.Controls.Add(new WinForms.Label
            {
                Text = "Pra continuar, clique em Proximo. Se voce gostaria de selecionar uma pasta diferente, clique em Procurar.",
                Location = new Point(40, 96),
                Size = new Size(430, 42),
            });

            if (string.IsNullOrWhiteSpace(_installPathBox.Text))
            {
                _installPathBox.Text = ResolveInstallDirectory(_initialOptions);
            }
            _installPathBox.Location = new Point(40, 146);
            _installPathBox.Size = new Size(334, 24);
            _contentPanel.Controls.Add(_installPathBox);

            var browseButton = new WinForms.Button
            {
                Text = "Procurar...",
                Location = new Point(386, 145),
                Size = new Size(78, 26),
            };
            browseButton.Click += (_, _) => BrowseInstallDirectory();
            _contentPanel.Controls.Add(browseButton);

            _contentPanel.Controls.Add(new WinForms.Label
            {
                Text = "Pelo menos 350 MB de espaco livre em disco e requerido.",
                Location = new Point(40, 254),
                Size = new Size(420, 22),
            });
        }

        private void RenderOptionsPage()
        {
            _headerTitle.Text = "Selecione Tarefas Adicionais";
            _headerSubtitle.Text = "Escolha as opcoes que devem ser aplicadas durante a instalacao.";
            _nextButton.Text = "Instalar";

            _contentPanel.Controls.Add(new WinForms.Label
            {
                Text = "Quais tarefas adicionais o instalador deve executar?",
                Location = new Point(40, 36),
                Size = new Size(430, 28),
            });

            _desktopShortcutBox.Text = "Criar atalho na area de trabalho";
            _desktopShortcutBox.Checked = !_initialOptions.NoDesktopShortcut;
            _desktopShortcutBox.Location = new Point(58, 86);
            _desktopShortcutBox.Size = new Size(330, 24);
            _contentPanel.Controls.Add(_desktopShortcutBox);

            _launchBox.Text = "Abrir o SunOrus Operator ao concluir";
            _launchBox.Checked = _initialOptions.Launch;
            _launchBox.Location = new Point(58, 116);
            _launchBox.Size = new Size(330, 24);
            _contentPanel.Controls.Add(_launchBox);

            _contentPanel.Controls.Add(new WinForms.Label
            {
                Text = "Clique em Instalar para iniciar a copia dos arquivos.",
                Location = new Point(40, 210),
                Size = new Size(430, 28),
            });
        }

        private void RenderInstallingPage()
        {
            _headerTitle.Text = "Instalando";
            _headerSubtitle.Text = "Por favor aguarde enquanto o SunOrus Operator e instalado.";
            _backButton.Enabled = false;
            _nextButton.Enabled = false;
            _cancelButton.Enabled = false;
            _nextButton.Text = "Proximo >";

            _contentPanel.Controls.Add(new WinForms.Label
            {
                Text = "Extraindo e configurando arquivos...",
                Location = new Point(40, 62),
                Size = new Size(420, 24),
            });

            _progressBar.Location = new Point(40, 104);
            _progressBar.Size = new Size(424, 18);
            _progressBar.Style = WinForms.ProgressBarStyle.Continuous;
            _progressBar.Value = 0;
            _contentPanel.Controls.Add(_progressBar);

            _statusLabel.Text = "Preparando instalacao...";
            _statusLabel.ForeColor = Color.Black;
            _statusLabel.Location = new Point(40, 136);
            _statusLabel.Size = new Size(424, 42);
            _contentPanel.Controls.Add(_statusLabel);
        }

        private void RenderFinishPage()
        {
            _headerTitle.Text = "Concluindo o Assistente de Instalacao";
            _headerSubtitle.Text = "O SunOrus Operator foi instalado com sucesso.";
            _backButton.Enabled = false;
            _nextButton.Enabled = true;
            _nextButton.Text = "Concluir";
            _cancelButton.Visible = false;

            _contentPanel.Controls.Add(new WinForms.PictureBox
            {
                Image = SystemIcons.Information.ToBitmap(),
                Location = new Point(40, 44),
                Size = new Size(38, 38),
                SizeMode = WinForms.PictureBoxSizeMode.CenterImage,
            });

            _contentPanel.Controls.Add(new WinForms.Label
            {
                Text = "A instalacao do SunOrus Operator foi concluida. Clique em Concluir para sair do instalador.",
                Location = new Point(92, 46),
                Size = new Size(370, 56),
            });
        }

        private void LicenseChanged(object? sender, EventArgs e)
        {
            _nextButton.Enabled = _acceptLicenseRadio.Checked;
        }

        private void MoveBack()
        {
            _page = _page switch
            {
                WizardPage.Destination => WizardPage.License,
                WizardPage.Options => WizardPage.Destination,
                _ => _page,
            };
            RenderPage();
        }

        private async Task MoveNextAsync()
        {
            if (_finished)
            {
                Close();
                return;
            }

            switch (_page)
            {
                case WizardPage.License:
                    _page = WizardPage.Destination;
                    RenderPage();
                    break;
                case WizardPage.Destination:
                    _page = WizardPage.Options;
                    RenderPage();
                    break;
                case WizardPage.Options:
                    _page = WizardPage.Installing;
                    RenderPage();
                    await StartInstallAsync();
                    break;
                case WizardPage.Finish:
                    Close();
                    break;
            }
        }

        private async Task StartInstallAsync()
        {
            Environment.ExitCode = 1;

            var options = _initialOptions with
            {
                InstallDirectory = _installPathBox.Text.Trim(),
                NoDesktopShortcut = !_desktopShortcutBox.Checked,
                Launch = _launchBox.Checked,
            };

            IProgress<InstallProgress> progress = new Progress<InstallProgress>(item =>
            {
                _progressBar.Value = Math.Max(0, Math.Min(100, item.Percent));
                _statusLabel.Text = item.Message;
            });

            try
            {
                await Task.Run(() => Install(options, (percent, message) => progress.Report(new InstallProgress(percent, message))));
                _progressBar.Value = 100;
                _finished = true;
                Environment.ExitCode = 0;
                _page = WizardPage.Finish;
                RenderPage();
            }
            catch (Exception exc)
            {
                _statusLabel.Text = "Falha na instalacao.";
                WinForms.MessageBox.Show(
                    exc.Message,
                    "Falha no instalador",
                    WinForms.MessageBoxButtons.OK,
                    WinForms.MessageBoxIcon.Error);
                _page = WizardPage.Options;
                RenderPage();
            }
        }

        private void BrowseInstallDirectory()
        {
            using var dialog = new WinForms.FolderBrowserDialog
            {
                Description = "Escolha a pasta de instalacao do SunOrus Operator",
                UseDescriptionForTitle = true,
                SelectedPath = _installPathBox.Text,
            };

            if (dialog.ShowDialog(this) == WinForms.DialogResult.OK)
            {
                _installPathBox.Text = dialog.SelectedPath;
            }
        }

        private static Image? LoadLogoImage()
        {
            var assembly = Assembly.GetExecutingAssembly();
            var resourceName = assembly.GetManifestResourceNames()
                .FirstOrDefault(name => name.EndsWith("sunorus-logo.png", StringComparison.OrdinalIgnoreCase));
            if (resourceName is null)
            {
                return null;
            }

            using var stream = assembly.GetManifestResourceStream(resourceName);
            return stream is null ? null : Image.FromStream(stream);
        }

        private const string LicenseText = """
        Contrato de Licenca de Uso do programa SunOrus Operator

        Este software e fornecido pela SunOrus para uso operacional em ambientes autorizados de monitoramento, VMS e analytics.

        1. Aceitacao do contrato - Ao instalar ou usar este programa, o usuario declara que possui autorizacao para operar o sistema e concorda com os termos deste acordo.

        2. Uso permitido - O aplicativo deve ser usado apenas em equipamentos, servidores e ambientes vinculados ao projeto autorizado.

        3. Restricoes - E proibido redistribuir, modificar, vender, sublicenciar ou realizar engenharia reversa do programa sem autorizacao expressa.

        4. Responsabilidade operacional - O usuario e responsavel pela configuracao das cameras, credenciais, rede, armazenamento e politicas de seguranca aplicaveis.

        5. Suporte - Atualizacoes, manutencoes e suporte seguem as condicoes comerciais acordadas entre as partes.
        """;
    }

    private sealed record InstallProgress(int Percent, string Message);

    private sealed record InstallerOptions(
        bool Silent,
        bool Launch,
        bool NoDesktopShortcut,
        bool Uninstall,
        string? InstallDirectory)
    {
        public static InstallerOptions Parse(string[] args)
        {
            var silent = false;
            var launch = false;
            var noDesktop = false;
            var uninstall = false;
            string? installDirectory = null;

            foreach (var raw in args)
            {
                var arg = raw.Trim();
                if (arg.Equals("--silent", StringComparison.OrdinalIgnoreCase) || arg.Equals("/silent", StringComparison.OrdinalIgnoreCase))
                {
                    silent = true;
                }
                else if (arg.Equals("--launch", StringComparison.OrdinalIgnoreCase) || arg.Equals("/launch", StringComparison.OrdinalIgnoreCase))
                {
                    launch = true;
                }
                else if (arg.Equals("--no-desktop", StringComparison.OrdinalIgnoreCase) || arg.Equals("/no-desktop", StringComparison.OrdinalIgnoreCase))
                {
                    noDesktop = true;
                }
                else if (arg.Equals("--uninstall", StringComparison.OrdinalIgnoreCase) || arg.Equals("/uninstall", StringComparison.OrdinalIgnoreCase))
                {
                    uninstall = true;
                }
                else if (arg.StartsWith("--dir=", StringComparison.OrdinalIgnoreCase) || arg.StartsWith("/dir=", StringComparison.OrdinalIgnoreCase))
                {
                    installDirectory = arg[(arg.IndexOf('=') + 1)..].Trim('"');
                }
            }

            return new InstallerOptions(silent, launch, noDesktop, uninstall, installDirectory);
        }
    }
}
