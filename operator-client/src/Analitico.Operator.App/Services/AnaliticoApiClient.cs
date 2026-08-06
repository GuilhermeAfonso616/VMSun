using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Analitico.Operator.App.Models;

namespace Analitico.Operator.App.Services;

public sealed class AnaliticoApiClient : IDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    private readonly HttpClient _httpClient = new()
    {
        Timeout = TimeSpan.FromSeconds(12),
    };

    private string? _authToken;
    public string? AuthToken
    {
        get => _authToken;
        set
        {
            _authToken = value;
            _httpClient.DefaultRequestHeaders.Authorization =
                string.IsNullOrWhiteSpace(value) ? null : new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", value);
        }
    }

    public async Task<(bool success, string? token, string? name, string? role, string? error)> LoginAsync(
        string serverUrl,
        string username,
        string password,
        CancellationToken cancellationToken)
    {
        try
        {
            var baseUri = NormalizeBaseUri(serverUrl);
            var requestUri = new Uri(baseUri, "api/auth/login");
            var payload = new { username, password };
            
            using var requestContent = JsonContent.Create(payload);
            using var response = await _httpClient.PostAsync(requestUri, requestContent, cancellationToken).ConfigureAwait(false);
            
            if (!response.IsSuccessStatusCode)
            {
                var errBody = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
                try
                {
                    using var doc = JsonDocument.Parse(errBody);
                    var detail = doc.RootElement.GetProperty("detail").GetString();
                    return (false, null, null, null, detail ?? "Erro ao realizar login.");
                }
                catch
                {
                    return (false, null, null, null, $"Erro HTTP {response.StatusCode}");
                }
            }
            
            using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
            using var docPayload = await JsonDocument.ParseAsync(stream, default, cancellationToken).ConfigureAwait(false);
            
            var root = docPayload.RootElement;
            var token = root.GetProperty("access_token").GetString();
            var userObj = root.GetProperty("user");
            var name = userObj.GetProperty("name").GetString();
            var role = userObj.GetProperty("role").GetString();
            
            return (true, token, name, role, null);
        }
        catch (Exception ex)
        {
            return (false, null, null, null, ex.Message);
        }
    }

    public Task<OperatorBootstrapResponse> GetBootstrapAsync(
        string serverUrl,
        CancellationToken cancellationToken,
        bool registerPaths = true)
    {
        var baseUri = NormalizeBaseUri(serverUrl);
        var requestUri = new Uri(baseUri, $"api/operator/bootstrap?register_paths={registerPaths.ToString().ToLowerInvariant()}");
        return GetJsonAsync<OperatorBootstrapResponse>(requestUri, cancellationToken);
    }

    public Task<DashboardMetricsResponse> GetDashboardMetricsAsync(string serverUrl, CancellationToken cancellationToken)
    {
        return GetJsonAsync<DashboardMetricsResponse>(new Uri(NormalizeBaseUri(serverUrl), "dashboard/metrics"), cancellationToken);
    }

    public Task<DashboardEventsResponse> GetDashboardEventsAsync(string serverUrl, CancellationToken cancellationToken)
    {
        return GetJsonAsync<DashboardEventsResponse>(new Uri(NormalizeBaseUri(serverUrl), "dashboard/events"), cancellationToken);
    }

    public Task<HealthCamerasResponse> GetHealthCamerasAsync(string serverUrl, CancellationToken cancellationToken)
    {
        return GetJsonAsync<HealthCamerasResponse>(new Uri(NormalizeBaseUri(serverUrl), "health/cameras"), cancellationToken);
    }

    public Task<OneDriveStatusResponse> GetOneDriveStatusAsync(string serverUrl, CancellationToken cancellationToken)
    {
        return GetJsonAsync<OneDriveStatusResponse>(new Uri(NormalizeBaseUri(serverUrl), "api/operator/drive-token/status"), cancellationToken);
    }

    public Task<MonitorTracksResponse> GetMonitorTracksAsync(
        string serverUrl,
        IEnumerable<int> cameraIds,
        CancellationToken cancellationToken)
    {
        var ids = string.Join(",", cameraIds.Distinct().Take(32));
        if (string.IsNullOrWhiteSpace(ids))
        {
            return Task.FromResult(new MonitorTracksResponse());
        }

        var uri = new Uri(NormalizeBaseUri(serverUrl), $"monitor/tracks?camera_ids={Uri.EscapeDataString(ids)}&max_age_seconds=1.8");
        return GetJsonAsync<MonitorTracksResponse>(uri, cancellationToken);
    }

    public async Task StreamMonitorTracksAsync(
        string serverUrl,
        IEnumerable<int> cameraIds,
        int intervalMs,
        Func<MonitorTracksResponse, Task> onMessage,
        CancellationToken cancellationToken)
    {
        var ids = string.Join(",", cameraIds.Distinct().Take(32));
        if (string.IsNullOrWhiteSpace(ids))
        {
            return;
        }

        var requestUri = new Uri(
            NormalizeBaseUri(serverUrl),
            $"monitor/tracks/stream?camera_ids={Uri.EscapeDataString(ids)}&max_age_seconds=1.8&interval_ms={Math.Clamp(intervalMs, 100, 2000)}");

        using var response = await _httpClient
            .GetAsync(requestUri, HttpCompletionOption.ResponseHeadersRead, cancellationToken)
            .ConfigureAwait(false);
        response.EnsureSuccessStatusCode();

        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        using var reader = new StreamReader(stream, Encoding.UTF8);
        var data = new StringBuilder();

        while (!reader.EndOfStream && !cancellationToken.IsCancellationRequested)
        {
            var line = await reader.ReadLineAsync(cancellationToken).ConfigureAwait(false);
            if (line is null)
            {
                break;
            }

            if (line.Length == 0)
            {
                if (data.Length > 0)
                {
                    var payloadText = data.ToString();
                    data.Clear();
                    var payload = JsonSerializer.Deserialize<MonitorTracksResponse>(payloadText, JsonOptions);
                    if (payload is not null)
                    {
                        await onMessage(payload).ConfigureAwait(false);
                    }
                }

                continue;
            }

            if (line.StartsWith("data:", StringComparison.OrdinalIgnoreCase))
            {
                data.Append(line[5..].TrimStart());
            }
        }
    }

    public async Task<string> GetRawStringAsync(
        string serverUrl,
        string relativeUrl,
        CancellationToken cancellationToken)
    {
        var uri = new Uri(NormalizeBaseUri(serverUrl), relativeUrl.TrimStart('/'));
        return await _httpClient.GetStringAsync(uri, cancellationToken).ConfigureAwait(false);
    }

    public Task StartCameraAsync(string serverUrl, int cameraId, CancellationToken cancellationToken)
    {
        var uri = new Uri(NormalizeBaseUri(serverUrl), $"api/cameras/{cameraId}/start?use_motion_test=true");
        return PostAsync(uri, cancellationToken);
    }

    public Task StopCameraAsync(string serverUrl, int cameraId, CancellationToken cancellationToken)
    {
        var uri = new Uri(NormalizeBaseUri(serverUrl), $"api/cameras/{cameraId}/stop");
        return PostAsync(uri, cancellationToken);
    }

    public Task AcknowledgeEventAsync(string serverUrl, int eventId, CancellationToken cancellationToken)
    {
        var uri = new Uri(NormalizeBaseUri(serverUrl), $"api/events/{eventId}");
        return PutJsonAsync(uri, new { status = "acknowledged" }, cancellationToken);
    }

    public Task CloseEventAsync(string serverUrl, int eventId, CancellationToken cancellationToken)
    {
        var uri = new Uri(NormalizeBaseUri(serverUrl), $"api/events/{eventId}");
        return PutJsonAsync(uri, new { status = "closed" }, cancellationToken);
    }

    public Task PostOperatorPerformanceLogAsync(string serverUrl, object payload, CancellationToken cancellationToken)
    {
        var uri = new Uri(NormalizeBaseUri(serverUrl), "api/operator/performance-log");
        return PostJsonAsync(uri, payload, cancellationToken);
    }

    public Task SaveOneDriveTokenAsync(string serverUrl, string token, CancellationToken cancellationToken)
    {
        var uri = new Uri(NormalizeBaseUri(serverUrl), "api/operator/drive-token");
        return PostJsonAsync(uri, new { token }, cancellationToken);
    }

    public async Task<OneDriveReviewedEventsUploadResponse> UploadReviewedEventsToOneDriveAsync(
        string serverUrl,
        int limit,
        CancellationToken cancellationToken)
    {
        var safeLimit = Math.Clamp(limit, 1, 1000);
        var uri = new Uri(NormalizeBaseUri(serverUrl), $"api/operator/drive-reviewed-events/upload?limit={safeLimit}");
        using var response = await _httpClient.PostAsync(uri, content: null, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        var payload = await JsonSerializer.DeserializeAsync<OneDriveReviewedEventsUploadResponse>(stream, JsonOptions, cancellationToken).ConfigureAwait(false);
        return payload ?? new OneDriveReviewedEventsUploadResponse();
    }

    public static Uri NormalizeBaseUri(string serverUrl)
    {
        var value = string.IsNullOrWhiteSpace(serverUrl)
            ? "http://192.168.2.62:8000"
            : serverUrl.Trim();

        if (!value.Contains("://", StringComparison.Ordinal))
        {
            value = $"http://{value}";
        }

        if (Uri.TryCreate(value, UriKind.Absolute, out var parsed)
            && string.Equals(parsed.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase)
            && parsed.IsDefaultPort)
        {
            value = new UriBuilder(parsed)
            {
                Port = 8000,
            }.Uri.ToString();
        }

        if (!value.EndsWith("/", StringComparison.Ordinal))
        {
            value += "/";
        }

        return new Uri(value, UriKind.Absolute);
    }

    private async Task<T> GetJsonAsync<T>(Uri requestUri, CancellationToken cancellationToken)
        where T : new()
    {
        await using var stream = await _httpClient.GetStreamAsync(requestUri, cancellationToken).ConfigureAwait(false);
        var response = await JsonSerializer.DeserializeAsync<T>(stream, JsonOptions, cancellationToken).ConfigureAwait(false);
        return response ?? new T();
    }

    private async Task PostAsync(Uri requestUri, CancellationToken cancellationToken)
    {
        using var response = await _httpClient.PostAsync(requestUri, content: null, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
    }

    private async Task PutJsonAsync<T>(Uri requestUri, T payload, CancellationToken cancellationToken)
    {
        using var response = await _httpClient.PutAsJsonAsync(requestUri, payload, JsonOptions, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
    }

    private async Task PostJsonAsync<T>(Uri requestUri, T payload, CancellationToken cancellationToken)
    {
        using var response = await _httpClient.PostAsJsonAsync(requestUri, payload, JsonOptions, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
    }

    public async Task<List<StoredViewPreset>> GetViewPresetsAsync(string serverUrl, CancellationToken cancellationToken)
    {
        var baseUri = NormalizeBaseUri(serverUrl);
        var requestUri = new Uri(baseUri, "api/view-presets");
        return await GetJsonAsync<List<StoredViewPreset>>(requestUri, cancellationToken).ConfigureAwait(false);
    }

    public async Task SaveViewPresetAsync(string serverUrl, StoredViewPreset preset, CancellationToken cancellationToken)
    {
        var baseUri = NormalizeBaseUri(serverUrl);
        var requestUri = new Uri(baseUri, "api/view-presets");
        
        var payload = new
        {
            id = string.IsNullOrWhiteSpace(preset.Id) ? ("view_" + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()) : preset.Id,
            name = preset.Name,
            grid_size = preset.GridSize,
            camera_ids = preset.CameraIds ?? new List<int?>(),
            hide_offline = preset.HideOffline,
            boxes_enabled = preset.BoxesEnabled,
            is_shared = preset.IsShared
        };
        await PostJsonAsync(requestUri, payload, cancellationToken).ConfigureAwait(false);
    }

    public async Task DeleteViewPresetAsync(string serverUrl, string id, CancellationToken cancellationToken)
    {
        var baseUri = NormalizeBaseUri(serverUrl);
        var requestUri = new Uri(baseUri, $"api/view-presets/{id}");
        using var response = await _httpClient.DeleteAsync(requestUri, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
    }

    public async Task<byte[]> GetByteArrayAsync(Uri uri, CancellationToken cancellationToken)
    {
        using var response = await _httpClient.GetAsync(uri, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadAsByteArrayAsync(cancellationToken).ConfigureAwait(false);
    }

    public async Task<byte[]> ExportBackupAsync(string serverUrl, string password, CancellationToken cancellationToken)
    {
        var baseUri = NormalizeBaseUri(serverUrl);
        var requestUri = new Uri(baseUri, "api/backup/export");
        var payload = new { password = password };
        using var response = await _httpClient.PostAsJsonAsync(requestUri, payload, JsonOptions, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadAsByteArrayAsync(cancellationToken).ConfigureAwait(false);
    }

    public async Task<string> ImportBackupAsync(string serverUrl, byte[] backupBytes, string password, CancellationToken cancellationToken)
    {
        var baseUri = NormalizeBaseUri(serverUrl);
        var requestUri = new Uri(baseUri, "api/backup/import");
        
        using var content = new MultipartFormDataContent();
        
        var fileContent = new ByteArrayContent(backupBytes);
        fileContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("application/octet-stream");
        content.Add(fileContent, "file", "vms_backup.enc");
        
        content.Add(new StringContent(password), "password");
        
        using var response = await _httpClient.PostAsync(requestUri, content, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        
        var result = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        return result;
    }

    public void Dispose()
    {
        _httpClient.Dispose();
    }
}
