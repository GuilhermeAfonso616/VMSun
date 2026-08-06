using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;

namespace Analitico.Operator.DragTest.Controls;

public sealed class DragGhostWindow : Window
{
    private readonly TextBlock _titleText;
    private readonly TextBlock _detailText;
    private readonly TextBlock _hintText;
    private bool _isShown;

    public DragGhostWindow()
    {
        Width = 300;
        Height = 82;
        MinWidth = Width;
        MinHeight = Height;
        MaxWidth = Width;
        MaxHeight = Height;
        CanResize = false;
        ShowActivated = false;
        ShowInTaskbar = false;
        SystemDecorations = SystemDecorations.None;
        Topmost = true;
        WindowStartupLocation = WindowStartupLocation.Manual;
        Background = Brushes.Transparent;
        TransparencyLevelHint = new[] { WindowTransparencyLevel.Transparent };

        _titleText = new TextBlock
        {
            Foreground = new SolidColorBrush(Color.FromRgb(219, 234, 254)),
            FontSize = 12,
            FontWeight = FontWeight.Bold,
            Text = "Arrastando video real",
        };
        _detailText = new TextBlock
        {
            Foreground = Brushes.White,
            FontSize = 11,
            MaxWidth = 270,
            TextTrimming = TextTrimming.CharacterEllipsis,
            Text = "Solte no alvo verde",
        };
        _hintText = new TextBlock
        {
            Foreground = new SolidColorBrush(Color.FromRgb(147, 197, 253)),
            FontSize = 10,
            Text = "captura global ativa",
        };

        Content = new Border
        {
            Background = new SolidColorBrush(Color.FromArgb(236, 11, 18, 32)),
            BorderBrush = new SolidColorBrush(Color.FromRgb(96, 165, 250)),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(4),
            Padding = new Thickness(12, 8),
            Child = new StackPanel
            {
                Spacing = 2,
                Children =
                {
                    _titleText,
                    _detailText,
                    _hintText,
                },
            },
        };
    }

    public void SetText(string title, string detail, string hint)
    {
        _titleText.Text = title;
        _detailText.Text = detail;
        _hintText.Text = hint;
    }

    public void ShowOrMove(Window owner, int screenX, int screenY)
    {
        MoveNear(screenX, screenY);
        if (_isShown)
        {
            Topmost = true;
            return;
        }

        _isShown = true;
        Show(owner);
        Topmost = true;
    }

    public void MoveNear(int screenX, int screenY)
    {
        Position = new PixelPoint(screenX + 18, screenY + 18);
    }

    public void HideGhost()
    {
        if (!_isShown)
        {
            return;
        }

        Hide();
        _isShown = false;
    }

    protected override void OnClosed(System.EventArgs e)
    {
        _isShown = false;
        base.OnClosed(e);
    }
}
