using System;
using System.Linq;
using System.Reflection;

/// <summary>
/// Valores gravados no assembly em tempo de build (dotnet publish -p:Chave=valor).
/// Sao eles que dizem ao setup de onde baixar o payload e o FFmpeg, sem precisar
/// de arquivo de configuracao ao lado do executavel.
/// </summary>
internal static class Metadados
{
    public static string? Ler(string chave)
    {
        var valor = Assembly.GetExecutingAssembly()
            .GetCustomAttributes<AssemblyMetadataAttribute>()
            .FirstOrDefault(item => string.Equals(item.Key, chave, StringComparison.Ordinal))?.Value;

        return string.IsNullOrWhiteSpace(valor) ? null : valor;
    }
}
