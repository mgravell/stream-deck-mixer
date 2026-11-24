using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Drawing.Text;

namespace FaderDisplay.Render;

/// <summary>
/// Draws key images. Geometry is a straight port of the Linux plugin's PIL code
/// (Segment/AppSlot/Nav/MediaKey), expressed on the same 96x96 design canvas and
/// scaled to <see cref="Size"/> pixels. Text is drawn into the bitmap (rather than
/// as a Stream Deck title) so it looks the same everywhere.
/// </summary>
public static class KeyRenderer
{
    public const int Size = 144; // Elgato's recommended @2x key size; the app downsamples for 72px keys
    private const float Scale = Size / 96f;

    private static readonly Color Track = Color.FromArgb(24, 24, 24);
    private static readonly Color EmptyTrack = Color.FromArgb(18, 18, 18);
    private static readonly Color Cap = Color.FromArgb(235, 235, 235);
    private static readonly Color GlyphColor = Color.FromArgb(240, 240, 240);
    private static readonly Color MuteX = Color.FromArgb(255, 70, 60);
    private static readonly Color Outline = Color.FromArgb(12, 12, 12);
    private static readonly Color Dim = Color.FromArgb(110, 110, 110);

    // VU strip on the left edge: two 7px bars (L|R) with a 1px gap
    private const int MeterW = 15;
    private static readonly Color MeterTrack = Color.FromArgb(10, 10, 10);
    private static readonly Color MeterGreen = Color.FromArgb(60, 205, 90);
    private static readonly Color MeterYellow = Color.FromArgb(235, 200, 60);
    private static readonly Color MeterRed = Color.FromArgb(240, 70, 55);

    public static Bitmap Render(KeyVisual visual, Func<string, Bitmap?>? icons = null)
    {
        var bitmap = new Bitmap(Size, Size, PixelFormat.Format32bppArgb);
        using var g = Graphics.FromImage(bitmap);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.PixelOffsetMode = PixelOffsetMode.HighQuality;
        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;

        switch (visual)
        {
            case FaderKey fader: DrawFader(g, fader, icons); break;
            case ModeKey mode: DrawMode(g, mode); break;
            case PagerKey pager: DrawPager(g, pager); break;
            case PlayPauseKey media: DrawPlayPause(g, media); break;
            default: g.Clear(EmptyTrack); break;
        }
        return bitmap;
    }

    public static byte[] RenderPng(KeyVisual visual, Func<string, Bitmap?>? icons = null)
    {
        using var bitmap = Render(visual, icons);
        using var stream = new MemoryStream();
        bitmap.Save(stream, ImageFormat.Png);
        return stream.ToArray();
    }

    // ---- fader keys ---------------------------------------------------------------

    private static void DrawFader(Graphics g, FaderKey key, Func<string, Bitmap?>? icons)
    {
        g.Clear(Track);
        var c = key.Color;
        if (key.FillPx > 0) Rect(g, Color.FromArgb(c.R, c.G, c.B), 0, 96 - key.FillPx, 96, 96);
        if (key.FillPx is > 0 and < 96) Rect(g, Cap, 0, 96 - key.FillPx - 2, 96, 96 - key.FillPx);

        DrawMeter(g, key);
        DrawGlyph(g, key.Glyph, key.Muted);

        if (key.Glyph == Glyph.Up && key.IconReady && key.IconPath is not null && icons?.Invoke(key.IconPath) is { } icon)
        {
            // top key: app icon in the otherwise empty bottom half, 38px, centered on (55, 70)
            lock (icon) g.DrawImage(icon, D(55 - 19), D(70 - 19), D(38), D(38));
        }

        if (key.Accent) // ring around the column marking the default device
        {
            Rect(g, Cap, 0, 0, 1, 96);
            Rect(g, Cap, 94, 0, 96, 96);
            if (key.AccentTop) Rect(g, Cap, 0, 0, 96, 1);
            if (key.AccentBottom) Rect(g, Cap, 0, 94, 96, 96);
        }

        if (key.Text is { Length: > 0 } text) BottomLabel(g, text);
    }

    private static void DrawMeter(Graphics g, FaderKey key)
    {
        const int half = (MeterW - 1) / 2; // 7px per side
        Rect(g, MeterTrack, 0, 0, MeterW - 1, 96);
        int yellow = Math.Clamp(key.YellowFrom, 0, 96), red = Math.Clamp(key.RedFrom, 0, 96);
        foreach (var (x0, x1, bar, hold) in new[] { (0, half - 1, key.BarL, key.HoldL), (MeterW - half, MeterW - 1, key.BarR, key.HoldR) })
        {
            foreach (var (from, to, color) in new[] { (0, yellow, MeterGreen), (yellow, red, MeterYellow), (red, 96, MeterRed) })
            {
                int top = Math.Min(bar, to);
                if (top > from) Rect(g, color, x0, 96 - top, x1, 96 - from);
            }
            if (hold >= 0) Rect(g, Cap, x0, Math.Max(0, 96 - hold - 2), x1, Math.Min(96, 96 - hold));
        }
    }

    private static void DrawGlyph(Graphics g, Glyph glyph, bool muted)
    {
        // glyphs live in the top half of the canvas, centered on (48, 24)
        const int cx = 48, cy = 24;
        switch (glyph)
        {
            case Glyph.Up:
                Stamp((dx, dy, col) => Polygon(g, col ?? GlyphColor, (cx - 15 + dx, cy + 8 + dy), (cx + 15 + dx, cy + 8 + dy), (cx + dx, cy - 10 + dy)));
                break;
            case Glyph.Down:
                Stamp((dx, dy, col) => Polygon(g, col ?? GlyphColor, (cx - 15 + dx, cy - 8 + dy), (cx + 15 + dx, cy - 8 + dy), (cx + dx, cy + 10 + dy)));
                break;
            case Glyph.Mute:
            {
                var body = muted ? MuteX : GlyphColor;
                Stamp((dx, dy, col) => Polygon(g, col ?? body,
                    (cx - 16 + dx, cy - 5 + dy), (cx - 8 + dx, cy - 5 + dy), (cx + 2 + dx, cy - 13 + dy),
                    (cx + 2 + dx, cy + 13 + dy), (cx - 8 + dx, cy + 5 + dy), (cx - 16 + dx, cy + 5 + dy)));
                if (muted)
                {
                    Stamp((dx, dy, col) =>
                    {
                        Line(g, col ?? MuteX, 4, cx + 7 + dx, cy - 8 + dy, cx + 18 + dx, cy + 8 + dy);
                        Line(g, col ?? MuteX, 4, cx + 18 + dx, cy - 8 + dy, cx + 7 + dx, cy + 8 + dy);
                    });
                }
                else
                {
                    Stamp((dx, dy, col) => Arc(g, col ?? GlyphColor, 3, cx + 4 + dx, cy - 10 + dy, cx + 20 + dx, cy + 10 + dy, -55, 55));
                }
                break;
            }
            case Glyph.Mic:
            {
                var body = muted ? MuteX : GlyphColor;
                Stamp((dx, dy, col) => RoundedRect(g, col ?? body, cx - 7 + dx, cy - 15 + dy, cx + 7 + dx, cy + 1 + dy, 7));
                Stamp((dx, dy, col) =>
                {
                    Arc(g, col ?? GlyphColor, 3, cx - 12 + dx, cy - 8 + dy, cx + 12 + dx, cy + 8 + dy, 0, 180);
                    Line(g, col ?? GlyphColor, 3, cx + dx, cy + 8 + dy, cx + dx, cy + 14 + dy);
                    Line(g, col ?? GlyphColor, 3, cx - 7 + dx, cy + 14 + dy, cx + 7 + dx, cy + 14 + dy);
                });
                if (muted) Stamp((dx, dy, col) => Line(g, col ?? MuteX, 4, cx - 14 + dx, cy + 13 + dy, cx + 14 + dx, cy - 13 + dy));
                break;
            }
        }
    }

    /// <summary>Draw a shape with a dark outline so it reads on any fill color.</summary>
    private static void Stamp(Action<int, int, Color?> shape)
    {
        foreach (var (dx, dy) in new[] { (-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, -2), (-2, 2), (2, 2) })
            shape(dx, dy, Outline);
        shape(0, 0, null); // null -> the shape's real color
    }

    // ---- utility keys -------------------------------------------------------------

    private static void DrawMode(Graphics g, ModeKey key)
    {
        g.Clear(Track);
        const int cx = 48, cy = 38;
        switch (key.Page)
        {
            case Page.Outputs: // speaker with sound waves
                Polygon(g, GlyphColor, (cx - 18, cy - 7), (cx - 8, cy - 7), (cx + 2, cy - 16),
                    (cx + 2, cy + 16), (cx - 8, cy + 7), (cx - 18, cy + 7));
                Arc(g, GlyphColor, 4, cx + 5, cy - 12, cx + 23, cy + 12, -55, 55);
                break;
            case Page.Inputs: // microphone
                RoundedRect(g, GlyphColor, cx - 8, cy - 20, cx + 8, cy + 2, 8);
                Arc(g, GlyphColor, 4, cx - 14, cy - 8, cx + 14, cy + 12, 0, 180);
                Line(g, GlyphColor, 4, cx, cy + 12, cx, cy + 20);
                break;
            default: // 2x2 app grid
                foreach (int dx in new[] { -18, 4 })
                    foreach (int dy in new[] { -18, 4 })
                        RoundedRect(g, GlyphColor, cx + dx, cy + dy, cx + dx + 14, cy + dy + 14, 4);
                break;
        }
        BottomLabel(g, key.Page.ToString());
    }

    private static void DrawPager(Graphics g, PagerKey key)
    {
        g.Clear(Track);
        var color = key.Total > key.PerPage ? GlyphColor : Dim;
        const int cx = 48, cy = 38;
        foreach (int dx in new[] { -10, 8 }) // double chevron right
            Polygon(g, color, (cx + dx - 6, cy - 14), (cx + dx + 6, cy), (cx + dx - 6, cy + 14));
        // sits directly under the page-mode key, so a bare range is enough
        string text = key.Total > key.PerPage
            ? $"{key.Offset + 1}-{Math.Min(key.Offset + key.PerPage, key.Total)}/{key.Total}"
            : key.Total > 1 ? $"1-{key.Total}" : key.Total.ToString();
        BottomLabel(g, text);
    }

    private static void DrawPlayPause(Graphics g, PlayPauseKey key)
    {
        g.Clear(Track);
        var color = key.Status == MediaStatus.NoPlayer ? Dim : GlyphColor;
        if (key.Status == MediaStatus.Playing)
        {
            Rect(g, color, 34, 30, 44, 66); // pause: two bars
            Rect(g, color, 52, 30, 62, 66);
        }
        else
        {
            Polygon(g, color, (38, 28), (38, 68), (72, 48)); // play triangle
        }
    }

    // ---- primitives (PIL semantics on the 96px design canvas) ----------------------

    private static float D(float v) => v * Scale;

    /// <summary>PIL rectangle: inclusive corners. Snapped to device pixels so fills stay crisp.</summary>
    private static void Rect(Graphics g, Color color, int x0, int y0, int x1, int y1)
    {
        int left = (int)Math.Round(x0 * Scale), top = (int)Math.Round(y0 * Scale);
        int right = (int)Math.Round((x1 + 1) * Scale), bottom = (int)Math.Round((y1 + 1) * Scale);
        if (right <= left || bottom <= top) return;
        var mode = g.SmoothingMode;
        g.SmoothingMode = SmoothingMode.None;
        using var brush = new SolidBrush(color);
        g.FillRectangle(brush, left, top, right - left, bottom - top);
        g.SmoothingMode = mode;
    }

    private static void Polygon(Graphics g, Color color, params (int X, int Y)[] points)
    {
        using var brush = new SolidBrush(color);
        g.FillPolygon(brush, points.Select(p => new PointF(D(p.X + 0.5f), D(p.Y + 0.5f))).ToArray());
    }

    private static void Line(Graphics g, Color color, float width, int x0, int y0, int x1, int y1)
    {
        using var pen = new Pen(color, D(width));
        g.DrawLine(pen, D(x0 + 0.5f), D(y0 + 0.5f), D(x1 + 0.5f), D(y1 + 0.5f));
    }

    /// <summary>PIL arc: bounding box, angles clockwise from 3 o'clock, stroke drawn inward.</summary>
    private static void Arc(Graphics g, Color color, float width, int x0, int y0, int x1, int y1, float start, float end)
    {
        using var pen = new Pen(color, D(width));
        float inset = width / 2;
        g.DrawArc(pen, D(x0 + inset), D(y0 + inset), D(x1 - x0 + 1 - width), D(y1 - y0 + 1 - width), start, end - start);
    }

    private static void RoundedRect(Graphics g, Color color, int x0, int y0, int x1, int y1, float radius)
    {
        using var path = new GraphicsPath();
        float l = D(x0), t = D(y0), r = D(x1 + 1), b = D(y1 + 1), d = Math.Min(D(radius * 2), Math.Min(r - l, b - t));
        path.AddArc(l, t, d, d, 180, 90);
        path.AddArc(r - d, t, d, d, 270, 90);
        path.AddArc(r - d, b - d, d, d, 0, 90);
        path.AddArc(l, b - d, d, d, 90, 90);
        path.CloseFigure();
        using var brush = new SolidBrush(color);
        g.FillPath(brush, path);
    }

    private static readonly FontFamily LabelFont = PickFont();

    private static FontFamily PickFont()
    {
        try
        {
            return new FontFamily("Segoe UI");
        }
        catch
        {
            return FontFamily.GenericSansSerif;
        }
    }

    /// <summary>Bold white caption along the bottom edge, outlined so it reads on any fill.</summary>
    private static void BottomLabel(Graphics g, string text)
    {
        using var path = new GraphicsPath();
        using var format = new StringFormat(StringFormat.GenericTypographic)
        {
            Alignment = StringAlignment.Center,
            LineAlignment = StringAlignment.Far,
            FormatFlags = StringFormatFlags.NoWrap,
        };
        path.AddString(text, LabelFont, (int)FontStyle.Bold, D(17), new RectangleF(D(-20), 0, D(136), D(93)), format);
        using var pen = new Pen(Color.Black, D(3.5f)) { LineJoin = LineJoin.Round };
        g.DrawPath(pen, path);
        using var brush = new SolidBrush(Color.White);
        g.FillPath(brush, path);
    }
}
