using System.Collections.Generic;
using System.Linq;
using System.Windows.Input;

namespace Analitico.Operator.App.ViewModels;

public sealed class ViewPresetViewModel
{
    public ViewPresetViewModel(string id, string name, int gridSize, IEnumerable<int?> cameraIds, bool hideOffline, bool boxesEnabled, bool isShared = false, string? ownerUsername = null, bool canManage = true)
    {
        Id = id;
        Name = name;
        GridSize = gridSize;
        CameraIds = cameraIds.ToList();
        HideOffline = hideOffline;
        BoxesEnabled = boxesEnabled;
        IsShared = isShared;
        OwnerUsername = ownerUsername;
        CanManage = canManage;
        LoadCommand = new RelayCommand(_ => LoadAction?.Invoke(this));
    }

    public string Id { get; }

    public string Name { get; }

    public int GridSize { get; }

    public List<int?> CameraIds { get; }

    public bool HideOffline { get; }

    public bool BoxesEnabled { get; }

    public bool IsShared { get; }

    public string? OwnerUsername { get; }

    public bool CanManage { get; }

    public string Summary => IsShared
        ? $"{GridSize} slots | {CameraIds.Count(id => id is not null)} cam. | Compartilhado por {OwnerUsername ?? "admin"}"
        : $"{GridSize} slots | {CameraIds.Count(id => id is not null)} cam.";

    public ICommand LoadCommand { get; }

    public System.Action<ViewPresetViewModel>? LoadAction { get; set; }
}
