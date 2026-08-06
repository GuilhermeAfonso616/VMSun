using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

const int HelperPort = 34020;
// O navegador abre no maximo 6 conexoes simultaneas por origem (HTTP/1.1) e cada
// MJPEG segura uma delas enquanto o tile viver. Escutando em portas vizinhas o
// mosaico ganha origens distintas - 4 portas x 6 conexoes cobrem 24 cameras.
const int HelperPortCount = 4;
const int RtspPort = 8554;
const int MaxStreams = 32;

using var singleInstance = new Mutex(initiallyOwned: true, "Local\\SunOrus.Video.Helper", out var ownsMutex);
if (!ownsMutex)
{
    return;
}

var builder = WebApplication.CreateSlimBuilder(args);
builder.WebHost.UseKestrel(options =>
{
    for (var offset = 0; offset < HelperPortCount; offset++)
    {
        options.ListenLocalhost(HelperPort + offset);
    }
});
builder.Logging.ClearProviders();
builder.Logging.AddEventLog(settings => settings.SourceName = "SunOrus Video Helper");

var app = builder.Build();
var streamSlots = new SemaphoreSlim(MaxStreams, MaxStreams);
var ffmpegPath = FfmpegLocator.Find();

app.Use(async (context, next) =>
{
    var origin = context.Request.Headers.Origin.ToString();
    if (!string.IsNullOrWhiteSpace(origin) && OriginPolicy.IsAllowed(origin))
    {
        context.Response.Headers.AccessControlAllowOrigin = origin;
        context.Response.Headers.AccessControlAllowMethods = "GET, OPTIONS";
        context.Response.Headers.AccessControlAllowHeaders = "Content-Type";
        context.Response.Headers["Access-Control-Allow-Private-Network"] = "true";
        context.Response.Headers.Vary = "Origin";
    }

    if (HttpMethods.IsOptions(context.Request.Method))
    {
        context.Response.StatusCode = StatusCodes.Status204NoContent;
        return;
    }

    await next();
});

app.UseWebSockets(new WebSocketOptions { KeepAliveInterval = TimeSpan.FromSeconds(20) });

app.MapGet("/", () => Results.Json(
    new HelperInfo(
        "SunOrus Video Helper",
        typeof(Program).Assembly.GetName().Version?.ToString(3),
        ffmpegPath is null ? "ffmpeg_missing" : "ready"),
    HelperJsonContext.Default.HelperInfo));

app.MapGet("/health", () =>
{
    return Results.Json(
        new HelperHealth(
            ffmpegPath is not null,
            "sunorus-video-helper",
            typeof(Program).Assembly.GetName().Version?.ToString(3),
            ffmpegPath is null ? "not_found" : "ffmpeg",
            HelperPort,
            // O mosaico distribui as cameras entre estas portas para nao esbarrar
            // no limite de conexoes por origem do navegador.
            Enumerable.Range(HelperPort, HelperPortCount).ToArray(),
            MaxStreams),
        HelperJsonContext.Default.HelperHealth,
        statusCode: ffmpegPath is null ? 503 : 200);
});

app.Map("/ws", async context =>
{
    if (!context.WebSockets.IsWebSocketRequest)
    {
        context.Response.StatusCode = StatusCodes.Status400BadRequest;
        return;
    }

    using var socket = await context.WebSockets.AcceptWebSocketAsync();
    var buffer = new byte[2048];
    while (socket.State == WebSocketState.Open && !context.RequestAborted.IsCancellationRequested)
    {
        var message = await socket.ReceiveAsync(buffer, context.RequestAborted);
        if (message.MessageType == WebSocketMessageType.Close)
        {
            await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", CancellationToken.None);
            break;
        }

        var response = JsonSerializer.SerializeToUtf8Bytes(
            new WsStatus("status", ffmpegPath is not null, ffmpegPath is null ? "not_found" : "ffmpeg"),
            HelperJsonContext.Default.WsStatus);
        await socket.SendAsync(response, WebSocketMessageType.Text, true, context.RequestAborted);
    }
});

app.MapGet("/stream/{cameraId:int}.mjpeg", async (
    int cameraId,
    string server,
    int? width,
    int? fps,
    HttpContext context) =>
{
    if (cameraId <= 0 || cameraId > 100000)
    {
        return HelperError.AsResult("camera_id_invalid", 400);
    }

    if (ffmpegPath is null)
    {
        return HelperError.AsResult("ffmpeg_not_found", 503);
    }

    if (!await PrivateNetworkPolicy.IsPrivateHostAsync(server, context.RequestAborted))
    {
        return HelperError.AsResult("server_must_be_private", 400);
    }

    if (!await streamSlots.WaitAsync(TimeSpan.FromSeconds(2), context.RequestAborted))
    {
        return HelperError.AsResult("stream_limit_reached", 429);
    }

    try
    {
        var safeWidth = Math.Clamp(width ?? 960, 320, 1920);
        var safeFps = Math.Clamp(fps ?? 10, 1, 20);
        var source = $"rtsp://{FormatHost(server)}:{RtspPort}/cam_{cameraId}";
        var arguments = BuildFfmpegArguments(source, safeWidth, safeFps);

        using var process = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = ffmpegPath,
                Arguments = arguments,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            },
            EnableRaisingEvents = true
        };

        if (!process.Start())
        {
            return HelperError.AsResult("decoder_start_failed", 502);
        }

        // O stderr do ffmpeg era descartado: quando o decoder falhava (RTSP
        // recusado, path inexistente) o navegador recebia 200 com corpo vazio e
        // nao sobrava rastro nenhum. Agora a ultima saida fica no Event Log.
        var stderrTask = process.StandardError.ReadToEndAsync();
        _ = stderrTask.ContinueWith(task =>
        {
            var mensagem = task.IsCompletedSuccessfully ? task.Result : "";
            if (!string.IsNullOrWhiteSpace(mensagem))
            {
                app.Logger.LogWarning(
                    "ffmpeg camera {CameraId} encerrou com saida: {Saida}",
                    cameraId,
                    mensagem.Length > 2000 ? mensagem[^2000..] : mensagem);
            }
        }, TaskScheduler.Default);
        context.Response.StatusCode = StatusCodes.Status200OK;
        context.Response.ContentType = "multipart/x-mixed-replace; boundary=frame";
        context.Response.Headers.CacheControl = "no-store, no-cache, must-revalidate";
        context.Response.Headers.Pragma = "no-cache";
        context.Response.Headers["X-Content-Type-Options"] = "nosniff";

        try
        {
            await process.StandardOutput.BaseStream.CopyToAsync(
                context.Response.Body,
                64 * 1024,
                context.RequestAborted);
        }
        catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested)
        {
            // The browser closed or changed the tile.
        }
        catch (IOException) when (context.RequestAborted.IsCancellationRequested)
        {
            // The response pipe was closed by the browser.
        }
        finally
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
                await process.WaitForExitAsync(CancellationToken.None);
            }
        }

        return Results.Empty;
    }
    finally
    {
        streamSlots.Release();
    }
});

app.Lifetime.ApplicationStarted.Register(() =>
{
    var directory = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SunOrus",
        "VideoHelper");
    Directory.CreateDirectory(directory);
    File.WriteAllText(
        Path.Combine(directory, "status.json"),
        JsonSerializer.Serialize(
            new HelperStatusFile(
                Environment.ProcessId,
                HelperPort,
                Enumerable.Range(HelperPort, HelperPortCount).ToArray(),
                DateTimeOffset.Now,
                ffmpegPath is not null),
            HelperJsonContext.Default.HelperStatusFile));
});

await app.RunAsync();

static string BuildFfmpegArguments(string source, int width, int fps)
{
    var quotedSource = source.Replace("\"", "\\\"");
    return "-hide_banner -loglevel warning -nostdin -rtsp_transport tcp "
        + $"-i \"{quotedSource}\" -map 0:v:0 -an -sn -dn "
        + $"-vf \"fps={fps},scale='min({width},iw)':-2\" "
        + "-c:v mjpeg -q:v 5 -f mpjpeg -boundary_tag frame pipe:1";
}

static string FormatHost(string host)
{
    return IPAddress.TryParse(host, out var address) && address.AddressFamily == AddressFamily.InterNetworkV6
        ? $"[{host}]"
        : host;
}

// Tipos concretos em vez de anonimos: com PublishTrimmed/AOT o serializador por
// reflexao e removido do binario, e o app deixava de subir. O source generator do
// System.Text.Json resolve isso em tempo de compilacao.
internal sealed record HelperInfo(string Name, string? Version, string Status);

internal sealed record HelperHealth(
    bool Ok,
    string Service,
    string? Version,
    string Decoder,
    int Port,
    int[] Ports,
    int MaxStreams);

internal sealed record WsStatus(string Type, bool Ok, string Decoder);

internal sealed record HelperStatusFile(
    int Pid,
    int Port,
    int[] Ports,
    DateTimeOffset StartedAt,
    bool DecoderReady);

internal sealed record HelperError(string Error)
{
    public static IResult AsResult(string code, int statusCode) =>
        Results.Json(new HelperError(code), HelperJsonContext.Default.HelperError, statusCode: statusCode);
}

[JsonSourceGenerationOptions(PropertyNamingPolicy = JsonKnownNamingPolicy.CamelCase)]
[JsonSerializable(typeof(HelperInfo))]
[JsonSerializable(typeof(HelperHealth))]
[JsonSerializable(typeof(WsStatus))]
[JsonSerializable(typeof(HelperStatusFile))]
[JsonSerializable(typeof(HelperError))]
internal sealed partial class HelperJsonContext : JsonSerializerContext;

internal static class OriginPolicy
{
    public static bool IsAllowed(string origin)
    {
        if (!Uri.TryCreate(origin, UriKind.Absolute, out var uri)
            || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
        {
            return false;
        }

        if (uri.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase)
            || (IPAddress.TryParse(uri.Host, out var address) && PrivateNetworkPolicy.IsPrivateAddress(address)))
        {
            return true;
        }

        // O helper e local, mas a pagina oficial pode ser servida por HTTPS.
        // Permite somente o dominio SunOrus (e subdominios), sem CORS global.
        if (uri.Scheme == Uri.UriSchemeHttps
            && (uri.Host.Equals("sunorus.com.br", StringComparison.OrdinalIgnoreCase)
                || uri.Host.EndsWith(".sunorus.com.br", StringComparison.OrdinalIgnoreCase)))
        {
            return true;
        }

        var configured = Environment.GetEnvironmentVariable("SUNORUS_VIDEO_HELPER_ALLOWED_ORIGINS");
        return (configured ?? string.Empty)
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Any(item => Uri.TryCreate(item, UriKind.Absolute, out var allowed)
                && allowed.Scheme.Equals(uri.Scheme, StringComparison.OrdinalIgnoreCase)
                && allowed.Host.Equals(uri.Host, StringComparison.OrdinalIgnoreCase)
                && allowed.Port == uri.Port);
    }
}

internal static class PrivateNetworkPolicy
{
    public static async Task<bool> IsPrivateHostAsync(string? host, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(host) || host.Length > 253)
        {
            return false;
        }

        if (IPAddress.TryParse(host, out var parsed))
        {
            return IsPrivateAddress(parsed);
        }

        try
        {
            var addresses = await Dns.GetHostAddressesAsync(host, cancellationToken);
            return addresses.Length > 0 && addresses.All(IsPrivateAddress);
        }
        catch (SocketException)
        {
            return false;
        }
    }

    public static bool IsPrivateAddress(IPAddress address)
    {
        if (IPAddress.IsLoopback(address))
        {
            return true;
        }

        if (address.AddressFamily == AddressFamily.InterNetwork)
        {
            var bytes = address.GetAddressBytes();
            return bytes[0] == 10
                || (bytes[0] == 172 && bytes[1] is >= 16 and <= 31)
                || (bytes[0] == 192 && bytes[1] == 168)
                || (bytes[0] == 169 && bytes[1] == 254);
        }

        if (address.AddressFamily == AddressFamily.InterNetworkV6)
        {
            var bytes = address.GetAddressBytes();
            return address.IsIPv6LinkLocal || (bytes[0] & 0xfe) == 0xfc;
        }

        return false;
    }
}

internal static class FfmpegLocator
{
    public static string? Find()
    {
        var candidates = new[]
        {
            Environment.GetEnvironmentVariable("SUNORUS_FFMPEG_PATH"),
            Path.Combine(AppContext.BaseDirectory, "ffmpeg.exe"),
            FindOnPath()
        };

        return candidates.FirstOrDefault(path => !string.IsNullOrWhiteSpace(path) && File.Exists(path));
    }

    private static string? FindOnPath()
    {
        var pathValue = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (var directory in pathValue.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
        {
            try
            {
                var candidate = Path.Combine(directory.Trim(), "ffmpeg.exe");
                if (File.Exists(candidate))
                {
                    return candidate;
                }
            }
            catch
            {
                // Ignore malformed PATH entries.
            }
        }

        return null;
    }
}

public partial class Program;
