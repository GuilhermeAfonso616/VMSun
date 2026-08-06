using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;

/// <summary>
/// Download HTTP compartilhado pelos provisionadores. Rodando em .NET Framework o
/// HttpClient usa o proxy configurado no Windows, o que costuma ser o caminho que
/// funciona em rede corporativa.
/// </summary>
internal static class Downloader
{
    private static bool _tlsAjustado;

    public static HttpClient Criar(TimeSpan timeout)
    {
        GarantirTls();
        var http = new HttpClient { Timeout = timeout };
        http.DefaultRequestHeaders.UserAgent.ParseAdd("SunOrusVideoHelperSetup/1.0");
        return http;
    }

    public static void ParaArquivo(HttpClient http, string url, string destino, CancellationToken cancellationToken)
    {
        try
        {
            using var resposta = http
                .GetAsync(url, HttpCompletionOption.ResponseHeadersRead, cancellationToken)
                .GetAwaiter()
                .GetResult();

            resposta.EnsureSuccessStatusCode();

            using var entrada = resposta.Content.ReadAsStreamAsync().GetAwaiter().GetResult();
            using var saida = File.Create(destino);
            entrada.CopyTo(saida, 1024 * 1024);
        }
        catch (HttpRequestException erro)
        {
            // "Ocorreu um erro ao enviar a solicitacao" nao diz nada a quem esta
            // instalando: quem atende o chamado precisa ver o endereco que falhou.
            throw new InvalidOperationException(
                "Nao foi possivel baixar:\n" + url
                + "\n\nVerifique se este computador alcanca esse endereco (DNS, proxy ou firewall)."
                + "\n\nDetalhe tecnico: " + CausaRaiz(erro),
                erro);
        }
        catch (TaskCanceledException erro)
        {
            throw new InvalidOperationException(
                "O download demorou demais e foi interrompido:\n" + url
                + "\n\nTente novamente em uma conexao mais estavel.",
                erro);
        }
    }

    private static string CausaRaiz(Exception erro)
    {
        var atual = erro;
        while (atual.InnerException is not null)
        {
            atual = atual.InnerException;
        }
        return atual.Message;
    }

    /// <summary>
    /// Busca um texto curto (tipicamente o arquivo .sha256 publicado ao lado do
    /// download). A ausencia dele nao e erro: quem chama decide o que fazer.
    /// </summary>
    public static string? TextoOpcional(HttpClient http, string url)
    {
        try
        {
            return http.GetStringAsync(url).GetAwaiter().GetResult();
        }
        catch
        {
            return null;
        }
    }

    public static string Sha256(string caminho)
    {
        using var stream = File.OpenRead(caminho);
        using var algoritmo = SHA256.Create();
        return BitConverter.ToString(algoritmo.ComputeHash(stream))
            .Replace("-", string.Empty)
            .ToLowerInvariant();
    }

    /// <summary>
    /// O .NET Framework respeita a politica de protocolos da maquina, que em
    /// instalacoes antigas ainda comeca em TLS 1.0 e derruba o HTTPS. Ligar o
    /// 1.2 explicitamente evita esse tropeco sem desabilitar o que ja estava la.
    /// </summary>
    private static void GarantirTls()
    {
        if (_tlsAjustado) return;
        _tlsAjustado = true;

        try
        {
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
        }
        catch (NotSupportedException)
        {
            // Maquina sem suporte ao valor: segue com o que o sistema oferecer.
        }
    }
}
