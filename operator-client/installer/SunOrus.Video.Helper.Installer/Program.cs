using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using Microsoft.Win32;

internal static class Program
{
    private const string AppName = "SunOrus Video Helper";
    private const string MainExeName = "SunOrus.Video.Helper.exe";
    private const string SetupExeName = "SunOrus.Video.Helper.Setup.exe";
    private const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\SunOrus Video Helper";
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";

    [STAThread]
    private static int Main(string[] args)
    {
        var silent = args.Any(value => value.Equals("/silent", StringComparison.OrdinalIgnoreCase));
        var uninstall = args.Any(value => value.Equals("/uninstall", StringComparison.OrdinalIgnoreCase));

        try
        {
            if (uninstall)
            {
                if (!silent && !Dialogs.Confirmar(
                        "Deseja remover o SunOrus Video Helper deste computador?",
                        AppName))
                {
                    return 1;
                }

                Uninstall();
                if (!silent)
                {
                    Dialogs.Informar("SunOrus Video Helper removido.", AppName);
                }
                return 0;
            }

            if (!silent && !Dialogs.Confirmar(
                    "Instalar o SunOrus Video Helper?\n\n"
                    + "Ele sera iniciado automaticamente e habilitara cameras H.265 "
                    + "no mosaico em navegadores sem suporte HEVC."
                    + AvisoDeDownload(),
                    AppName))
            {
                return 1;
            }

            Install();
            if (!silent)
            {
                Dialogs.Informar(
                    "Instalacao concluida.\n\nAtualize a pagina do mosaico no navegador.",
                    AppName);
            }
            return 0;
        }
        catch (Exception exception)
        {
            if (!silent)
            {
                Dialogs.Erro(
                    "Nao foi possivel concluir a operacao:\n\n" + exception.Message,
                    AppName);
            }
            return 2;
        }
    }

    private static string InstallDirectory => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Programs",
        "SunOrus",
        "Video Helper");

    /// <summary>
    /// O setup enxuto busca na rede o que nao carrega dentro de si. Avisar antes
    /// evita que o operador ache que a janela travou durante o download.
    /// </summary>
    private static string AvisoDeDownload()
    {
        var baixaFfmpeg = !FfmpegProvisioner.JaInstalado(Path.Combine(InstallDirectory, "ffmpeg.exe"))
            && !PayloadTrazFfmpeg();
        var baixaPayload = !PayloadProvisioner.Embutido;

        if (!baixaFfmpeg && !baixaPayload) return string.Empty;

        var tamanho = baixaFfmpeg ? "~105 MB" : "~11 MB";
        return $"\n\nOs componentes de video ({tamanho}) serao baixados agora. "
            + "Isso leva de alguns segundos a poucos minutos, conforme a conexao. "
            + "A janela pode parecer parada durante o download.";
    }

    private static void Install()
    {
        StopHelper();
        Directory.CreateDirectory(InstallDirectory);

        var tempZip = Path.Combine(Path.GetTempPath(), $"sunorus-video-helper-{Guid.NewGuid():N}.zip");
        try
        {
            PayloadProvisioner.Obter(tempZip);
            ExtractPayload(tempZip, InstallDirectory);
        }
        finally
        {
            TryDelete(tempZip);
        }

        var mainExe = Path.Combine(InstallDirectory, MainExeName);
        if (!File.Exists(mainExe))
        {
            throw new InvalidOperationException("O pacote do helper esta incompleto.");
        }

        // O FFmpeg nao viaja mais dentro do setup: se o payload nao trouxe e a
        // maquina ainda nao tem, busca agora. Em atualizacao o binario ja esta la
        // e o download e pulado.
        var ffmpegExe = Path.Combine(InstallDirectory, "ffmpeg.exe");
        if (!FfmpegProvisioner.JaInstalado(ffmpegExe))
        {
            FfmpegProvisioner.Baixar(FfmpegProvisioner.ResolverUrl(), ffmpegExe);
        }

        var installedSetup = Path.Combine(InstallDirectory, SetupExeName);
        File.Copy(CaminhoDoExecutavel(), installedSetup, overwrite: true);

        using (var run = Registry.CurrentUser.CreateSubKey(RunKey))
        {
            run.SetValue(AppName, Quote(mainExe));
        }

        using (var uninstall = Registry.CurrentUser.CreateSubKey(UninstallKey))
        {
            uninstall.SetValue("DisplayName", AppName);
            uninstall.SetValue("Publisher", "SunOrus");
            uninstall.SetValue("DisplayVersion", GetVersion());
            uninstall.SetValue("InstallLocation", InstallDirectory);
            uninstall.SetValue("DisplayIcon", mainExe);
            uninstall.SetValue("UninstallString", Quote(installedSetup) + " /uninstall");
            uninstall.SetValue("QuietUninstallString", Quote(installedSetup) + " /uninstall /silent");
            uninstall.SetValue("NoModify", 1, RegistryValueKind.DWord);
            uninstall.SetValue("NoRepair", 1, RegistryValueKind.DWord);
        }

        Process.Start(new ProcessStartInfo(mainExe)
        {
            WorkingDirectory = InstallDirectory,
            UseShellExecute = true
        });
    }

    private static void Uninstall()
    {
        StopHelper();
        using (var run = Registry.CurrentUser.OpenSubKey(RunKey, writable: true))
        {
            run?.DeleteValue(AppName, throwOnMissingValue: false);
        }
        Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, throwOnMissingSubKey: false);

        var installedSetup = Path.Combine(InstallDirectory, SetupExeName);
        if (Path.GetFullPath(CaminhoDoExecutavel()).Equals(Path.GetFullPath(installedSetup), StringComparison.OrdinalIgnoreCase))
        {
            ScheduleSelfDelete(InstallDirectory);
        }
        else if (Directory.Exists(InstallDirectory))
        {
            Directory.Delete(InstallDirectory, recursive: true);
        }
    }

    private static void StopHelper()
    {
        foreach (var process in Process.GetProcessesByName("SunOrus.Video.Helper"))
        {
            try
            {
                KillTree(process.Id);
                process.WaitForExit(5000);
            }
            catch
            {
                // A later file operation will report a useful error if it is still locked.
            }
            finally
            {
                process.Dispose();
            }
        }
    }

    /// <summary>
    /// O helper mantem um ffmpeg filho por camera. Process.Kill do .NET Framework
    /// nao derruba a arvore, e um ffmpeg orfao segura o arquivo na atualizacao;
    /// o taskkill resolve os dois casos.
    /// </summary>
    private static void KillTree(int pid)
    {
        using var taskkill = Process.Start(new ProcessStartInfo
        {
            FileName = "taskkill.exe",
            Arguments = $"/PID {pid} /T /F",
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        });
        taskkill?.WaitForExit(5000);
    }

    /// <summary>
    /// Builds offline embutem o ffmpeg no payload; nesse caso nao ha nada a
    /// baixar e o aviso de download nao deve aparecer.
    /// </summary>
    private static bool PayloadTrazFfmpeg()
    {
        if (!PayloadProvisioner.Embutido) return false;

        try
        {
            var assembly = Assembly.GetExecutingAssembly();
            var name = assembly.GetManifestResourceNames().FirstOrDefault(
                value => value.EndsWith(PayloadProvisioner.NomeRecurso, StringComparison.OrdinalIgnoreCase));
            if (name is null) return false;

            using var input = assembly.GetManifestResourceStream(name);
            if (input is null) return false;
            using var archive = new ZipArchive(input, ZipArchiveMode.Read);
            return archive.Entries.Any(
                entry => string.Equals(entry.Name, "ffmpeg.exe", StringComparison.OrdinalIgnoreCase));
        }
        catch
        {
            return false;
        }
    }

    private static void ExtractPayload(string archivePath, string destinationRoot)
    {
        var root = Path.GetFullPath(destinationRoot).TrimEnd(Path.DirectorySeparatorChar)
            + Path.DirectorySeparatorChar;
        using var archive = ZipFile.OpenRead(archivePath);
        foreach (var entry in archive.Entries)
        {
            var destination = Path.GetFullPath(Path.Combine(root, entry.FullName));
            if (!destination.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Entrada invalida no pacote.");
            }

            if (string.IsNullOrEmpty(entry.Name))
            {
                Directory.CreateDirectory(destination);
                continue;
            }

            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            entry.ExtractToFile(destination, overwrite: true);
        }
    }

    private static void ScheduleSelfDelete(string directory)
    {
        var escaped = directory.Replace("\"", "\"\"");
        Process.Start(new ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = $"/d /c ping 127.0.0.1 -n 3 >nul & rmdir /s /q \"{escaped}\"",
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        });
    }

    private static string CaminhoDoExecutavel()
    {
        using var atual = Process.GetCurrentProcess();
        return atual.MainModule?.FileName
            ?? throw new InvalidOperationException("Caminho do instalador indisponivel.");
    }

    private static string GetVersion()
    {
        return Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "0.1.0";
    }

    private static string Quote(string value) => "\"" + value + "\"";

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path)) File.Delete(path);
        }
        catch
        {
            // Temp cleanup is best effort.
        }
    }
}
