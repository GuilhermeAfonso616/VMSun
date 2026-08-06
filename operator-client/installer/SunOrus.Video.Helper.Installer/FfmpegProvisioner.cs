using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Threading;

/// <summary>
/// Obtem o FFmpeg em tempo de instalacao em vez de embutir no setup.
/// O binario full pesava 217 MB (92% do instalador) para usarmos quatro coisas:
/// demuxer RTSP, decoder HEVC/H264, encoder MJPEG e os filtros scale/fps.
/// </summary>
internal static class FfmpegProvisioner
{
    // Abaixo disso o arquivo certamente nao e um ffmpeg utilizavel (download
    // truncado, pagina de erro salva como zip, etc).
    private const long TamanhoMinimoBytes = 20L * 1024 * 1024;

    public static string ResolverUrl() =>
        Metadados.Ler("FfmpegDownloadUrl")
        ?? "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip";

    public static bool JaInstalado(string destino) =>
        File.Exists(destino) && new FileInfo(destino).Length >= TamanhoMinimoBytes;

    /// <summary>
    /// Baixa o pacote, confere o SHA-256 publicado ao lado e extrai apenas o
    /// ffmpeg.exe. O hash protege contra download corrompido ou truncado; contra
    /// origem comprometida quem responde e o TLS do proprio site.
    /// </summary>
    public static void Baixar(string url, string destinoExe, CancellationToken cancellationToken = default)
    {
        var tempZip = Path.Combine(Path.GetTempPath(), $"sunorus-ffmpeg-{Guid.NewGuid():N}.zip");
        try
        {
            using (var http = Downloader.Criar(TimeSpan.FromMinutes(20)))
            {
                Downloader.ParaArquivo(http, url, tempZip, cancellationToken);

                var esperado = ObterHashPublicado(http, url);
                if (esperado is not null)
                {
                    var calculado = Downloader.Sha256(tempZip);
                    if (!string.Equals(calculado, esperado, StringComparison.OrdinalIgnoreCase))
                    {
                        throw new InvalidOperationException(
                            "O FFmpeg baixado nao confere com o hash publicado. "
                            + "Tente novamente ou verifique a conexao/proxy da rede.");
                    }
                }
            }

            ExtrairFfmpeg(tempZip, destinoExe);
        }
        finally
        {
            try
            {
                if (File.Exists(tempZip)) File.Delete(tempZip);
            }
            catch
            {
                // Limpeza do temporario e best effort.
            }
        }
    }

    private static string? ObterHashPublicado(System.Net.Http.HttpClient http, string url)
    {
        var conteudo = Downloader.TextoOpcional(http, url + ".sha256");
        if (conteudo is null) return null;

        // O arquivo costuma vir como "<hash>" ou "<hash>  <nome-do-arquivo>".
        var primeiro = conteudo.Trim().Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries).FirstOrDefault();
        return primeiro is { Length: 64 } ? primeiro : null;
    }

    private static void ExtrairFfmpeg(string zipPath, string destinoExe)
    {
        using var archive = ZipFile.OpenRead(zipPath);
        var entrada = archive.Entries.FirstOrDefault(
            item => string.Equals(item.Name, "ffmpeg.exe", StringComparison.OrdinalIgnoreCase))
            ?? throw new InvalidOperationException("O pacote baixado nao contem ffmpeg.exe.");

        Directory.CreateDirectory(Path.GetDirectoryName(destinoExe)!);
        entrada.ExtractToFile(destinoExe, overwrite: true);

        if (new FileInfo(destinoExe).Length < TamanhoMinimoBytes)
        {
            throw new InvalidOperationException("O ffmpeg.exe extraido parece incompleto.");
        }
    }
}
