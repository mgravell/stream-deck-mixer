namespace FaderDisplay.Render;

public enum Glyph { None, Up, Down, Mute, Mic }

public enum Page { Apps, Outputs, Inputs }

/// <summary>
/// Everything that determines one key's image, as a value: the engine rebuilds
/// these every frame and only re-renders/sends keys whose visual changed.
/// Meter levels are pre-quantized (2px steps of the 96px design canvas) so tiny
/// level wobbles don't cause redraws.
/// </summary>
public abstract record KeyVisual;

/// <summary>Nothing assigned (or a fader column with no app/device in it).</summary>
public sealed record EmptyKey : KeyVisual
{
    public static readonly EmptyKey Instance = new();
}

/// <param name="FillPx">fader fill height within this key, 0..96</param>
/// <param name="Color">fill color, already dimmed if muted</param>
/// <param name="BarL">meter bar heights within this key (0..96, in 2px steps)</param>
/// <param name="HoldL">peak-hold marker height within this key, or -1 if it isn't on this key</param>
/// <param name="YellowFrom">height within this key where the meter's yellow zone starts (may be outside 0..96)</param>
/// <param name="RedFrom">likewise for the red zone</param>
/// <param name="AccentTop">draw the accent ring's top edge (top key of an accented column)</param>
public sealed record FaderKey(
    Glyph Glyph, int FillPx, Rgb Color, bool Muted,
    int BarL, int BarR, int HoldL, int HoldR, int YellowFrom, int RedFrom,
    string? Text, string? IconPath, bool IconReady,
    bool Accent, bool AccentTop, bool AccentBottom) : KeyVisual;

/// <summary>Page-mode key: shows the current page; pressing cycles to the next.</summary>
public sealed record ModeKey(Page Page) : KeyVisual;

public sealed record PagerKey(int Total, int Offset, int PerPage) : KeyVisual;

public sealed record PlayPauseKey(MediaStatus Status) : KeyVisual;
