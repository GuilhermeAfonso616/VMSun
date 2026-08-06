using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;

/// <summary>
/// Entrega o zip do helper para o instalador. Por padrao ele nao viaja dentro do
/// setup: os 11 MB do pacote self-contained eram metade do executavel. O build
/// grava a URL e o SHA-256 no assembly e aqui so baixamos e conferimos.
/// O modo offline (-EmbutirPayload) volta a embutir o zip como recurso.
/// </summary>
internal static class PayloadProvisioner
{
    public const string NomeRecurso = "SunOrusVideoHelperPayload.zip";

    // Um payload legitimo tem dezenas de arquivos do runtime; abaixo disso e
    // download truncado ou pagina de erro salva como zip.
    private const long TamanhoMinimoBytes = 1L * 1024 * 1024;

    public static bool Embutido => NomeDoRecurso() is not null;

    public static string? Url => Metadados.Ler("PayloadDownloadUrl");

    public static void Obter(string destinoZip, CancellationToken cancellationToken = default)
    {
        if (ExtrairRecurso(destinoZip)) return;

        var url = Url ?? throw new InvalidOperationException(
            "Este instalador nao traz o pacote do helper nem sabe onde baixa-lo. "
            + "Gere o setup novamente informando -PayloadUrl.");

        using (var http = Downloader.Criar(TimeSpan.FromMinutes(20)))
        {
            Downloader.ParaArquivo(http, url, destinoZip, cancellationToken);
        }

        Validar(destinoZip);
    }

    /// <summary>
    /// O hash e fixado no build (nos geramos o zip), entao aqui a conferencia e
    /// obrigatoria quando existe: divergiu, nao instala.
    /// </summary>
    private static void Validar(string zip)
    {
        if (!File.Exists(zip) || new FileInfo(zip).Length < TamanhoMinimoBytes)
        {
            throw new InvalidOperationException(
                "O pacote do helper baixado esta incompleto. Verifique a conexao e tente novamente.");
        }

        var esperado = Metadados.Ler("PayloadSha256");
        if (esperado is null) return;

        var calculado = Downloader.Sha256(zip);
        if (!string.Equals(calculado, esperado, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                "O pacote do helper baixado nao confere com o publicado nesta versao do instalador. "
                + "Baixe o instalador novamente ou verifique o proxy da rede.");
        }
    }

    private static bool ExtrairRecurso(string destino)
    {
        var nome = NomeDoRecurso();
        if (nome is null) return false;

        var assembly = Assembly.GetExecutingAssembly();
        using var entrada = assembly.GetManifestResourceStream(nome)
            ?? throw new InvalidOperationException("Payload embutido nao pode ser aberto.");
        using var saida = File.Create(destino);
        entrada.CopyTo(saida);
        return true;
    }

    private static string? NomeDoRecurso() =>
        Assembly.GetExecutingAssembly()
            .GetManifestResourceNames()
            .FirstOrDefault(valor => valor.EndsWith(NomeRecurso, StringComparison.OrdinalIgnoreCase));
}
