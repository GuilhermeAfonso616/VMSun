using System;
using System.Windows.Input;
using Avalonia.Threading;

namespace Analitico.Operator.App.ViewModels;

public sealed class RelayCommand : ICommand
{
    private readonly Action<object?> _execute;
    private readonly Predicate<object?>? _canExecute;

    public RelayCommand(Action<object?> execute, Predicate<object?>? canExecute = null)
    {
        _execute = execute;
        _canExecute = canExecute;
    }

    public event EventHandler? CanExecuteChanged;

    public bool CanExecute(object? parameter)
    {
        return _canExecute?.Invoke(parameter) ?? true;
    }

    public void Execute(object? parameter)
    {
        _execute(parameter);
    }

    public void RaiseCanExecuteChanged()
    {
        if (Dispatcher.UIThread.CheckAccess())
        {
            CanExecuteChanged?.Invoke(this, EventArgs.Empty);
        }
        else
        {
            Dispatcher.UIThread.Post(() => CanExecuteChanged?.Invoke(this, EventArgs.Empty));
        }
    }
}
