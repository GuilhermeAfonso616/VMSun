using System;
using Avalonia.Media;
using Analitico.Operator.App.Models;

namespace Analitico.Operator.App.ViewModels;

public sealed class TrackBoxViewModel : ObservableObject
{
    private static readonly IBrush DefaultStrokeBrush = new SolidColorBrush(Color.FromRgb(37, 99, 235));
    private static readonly IBrush DefaultFillBrush = new SolidColorBrush(Color.FromArgb(34, 37, 99, 235));
    private static readonly IBrush DefaultLabelBrush = new SolidColorBrush(Color.FromRgb(37, 99, 235));

    private TrackBoxPayload _source;
    private double _left;
    private double _top;
    private double _width;
    private double _height;

    public TrackBoxViewModel(
        string key,
        TrackBoxPayload source,
        double left,
        double top,
        double width,
        double height)
    {
        Key = key;
        _source = source;
        _left = left;
        _top = top;
        _width = width;
        _height = height;
        LastSeenUtc = DateTimeOffset.UtcNow;
    }

    public string Key { get; }

    public TrackBoxPayload Source => _source;

    public DateTimeOffset LastSeenUtc { get; private set; }

    public double Left
    {
        get => _left;
        private set => SetProperty(ref _left, value);
    }

    public double Top
    {
        get => _top;
        private set => SetProperty(ref _top, value);
    }

    public double Width
    {
        get => _width;
        private set => SetProperty(ref _width, value);
    }

    public double Height
    {
        get => _height;
        private set => SetProperty(ref _height, value);
    }

    public string Label
    {
        get
        {
            var label = string.IsNullOrWhiteSpace(_source.Label) ? "person" : _source.Label!;
            var confidence = _source.Confidence is null ? "" : $" {_source.Confidence.Value:0.00}";
            return $"{label}{confidence}";
        }
    }

    public IBrush StrokeBrush { get; } = DefaultStrokeBrush;

    public IBrush FillBrush { get; } = DefaultFillBrush;

    public IBrush LabelBrush { get; } = DefaultLabelBrush;

    public void Update(
        TrackBoxPayload source,
        double targetLeft,
        double targetTop,
        double targetWidth,
        double targetHeight,
        bool snap = false)
    {
        _source = source;
        LastSeenUtc = DateTimeOffset.UtcNow;

        var alpha = snap ? 1.0 : 0.9;
        Left = Blend(Left, targetLeft, alpha);
        Top = Blend(Top, targetTop, alpha);
        Width = Blend(Width, targetWidth, alpha);
        Height = Blend(Height, targetHeight, alpha);
        OnPropertyChanged(nameof(Source));
        OnPropertyChanged(nameof(Label));
    }

    private static double Blend(double current, double target, double alpha)
    {
        if (double.IsNaN(current) || double.IsInfinity(current))
        {
            return target;
        }

        return current + ((target - current) * alpha);
    }
}
