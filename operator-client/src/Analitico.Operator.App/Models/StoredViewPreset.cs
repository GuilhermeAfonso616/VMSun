using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace Analitico.Operator.App.Models;

public sealed class StoredViewPreset
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("grid_size")]
    public int GridSize { get; set; }

    [JsonPropertyName("camera_ids")]
    public List<int?> CameraIds { get; set; } = new();

    [JsonPropertyName("hide_offline")]
    public bool HideOffline { get; set; }

    [JsonPropertyName("boxes_enabled")]
    public bool BoxesEnabled { get; set; } = true;

    [JsonPropertyName("is_shared")]
    public bool IsShared { get; set; }

    [JsonPropertyName("owner_username")]
    public string? OwnerUsername { get; set; }

    [JsonPropertyName("can_manage")]
    public bool CanManage { get; set; } = true;
}
