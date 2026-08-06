using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using Avalonia.Threading;

namespace Analitico.Operator.App.ViewModels;

public abstract class ObservableObject : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;

    protected bool SetProperty<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return false;
        }

        field = value;
        OnPropertyChanged(propertyName);
        return true;
    }

    protected void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        if (Dispatcher.UIThread.CheckAccess())
        {
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        }
        else
        {
            var args = new PropertyChangedEventArgs(propertyName);
            Dispatcher.UIThread.Post(() => PropertyChanged?.Invoke(this, args));
        }
    }
}
