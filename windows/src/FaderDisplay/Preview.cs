using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using FaderDisplay.Audio;
using FaderDisplay.Render;

namespace FaderDisplay;

/// <summary>
/// Renders docs/windows-*.png: the three pages on a 5x3 deck with a staged scene,
/// drawn by the real renderer (same framing as the Linux docs/render_preview.py).
/// </summary>
public static class Preview
{
    private sealed record Column(string Label, Rgb Color, int Volume, bool Muted, bool Accent = false,
        string? Exe = null, double Meter = 0, double Hold = 0);

    public static void Write(string directory)
    {
        Directory.CreateDirectory(directory);
        var icons = new Dictionary<string, Bitmap?>();
        Bitmap? Icon(string path) => icons.TryGetValue(path, out var b) ? b : icons[path] = IconResolver.Load(path);

        string pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        string pf86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        var apps = new[]
        {
            new Column("Google Chrome", Palette.ForName("Google Chrome"), 90, false,
                Exe: Path.Combine(pf, @"Google\Chrome\Application\chrome.exe"), Meter: 0.62, Hold: 0.71),
            new Column("Microsoft Edge", Palette.ForName("Microsoft Edge"), 70, false,
                Exe: Path.Combine(pf86, @"Microsoft\Edge\Application\msedge.exe"), Meter: 0.38, Hold: 0.45),
            new Column("Windows Media Player", Palette.ForName("Windows Media Player"), 100, true,
                Exe: Path.Combine(pf86, @"Windows Media Player\wmplayer.exe")),
        };
        var outputs = new[]
        {
            new Column("G3", Palette.Master, 60, false, Accent: true, Meter: 0.74, Hold: 0.81),
            new Column("Speakers", Palette.ForName("Speakers (Realtek(R) Audio)"), 45, false),
            new Column("Phones", Palette.ForName("Headphones (USB Audio)"), 80, true),
        };
        var inputs = new[]
        {
            new Column("USB Mic", Palette.ForName("Microphone (USB Mic)"), 75, true, Accent: true),
            new Column("Line In", Palette.ForName("Line In (Realtek(R) Audio)"), 50, false, Meter: 0.52, Hold: 0.6),
        };

        foreach (var (page, columns) in new[] { (Page.Apps, apps), (Page.Outputs, outputs), (Page.Inputs, inputs) })
        {
            var keys = new Dictionary<(int, int), KeyVisual>();
            for (int col = 0; col < columns.Length; col++)
            {
                var c = columns[col];
                bool hasIcon = c.Exe is not null && File.Exists(c.Exe);
                for (int row = 0; row < 3; row++)
                {
                    keys[(col, row)] = Engine.FaderVisual(row, 3, page, c.Label, c.Color, c.Volume, c.Muted, c.Accent,
                        hasIcon ? c.Exe : null, hasIcon, (c.Meter, c.Meter * 0.93, c.Hold, c.Hold * 0.95));
                }
            }
            keys[(4, 0)] = new ModeKey(page);
            keys[(4, 1)] = new PagerKey(columns.Length, 0, 4);
            keys[(4, 2)] = new PlayPauseKey(MediaStatus.Playing);

            var images = keys.ToDictionary(k => k.Key, k => KeyRenderer.Render(k.Value, Icon));
            string path = Path.Combine(directory, $"windows-{page.ToString().ToLowerInvariant()}.png");
            using var composite = Compose(images, 5, 3);
            composite.Save(path, ImageFormat.Png);
            Console.WriteLine(path);
        }
    }

    /// <summary>Lay key images out like a deck: rounded keys on a dark faceplate.</summary>
    public static Bitmap Compose(IReadOnlyDictionary<(int Col, int Row), Bitmap> keys, int columns, int rows)
    {
        const int key = 192, gap = 26, margin = 56, radius = 26;
        var canvas = new Bitmap(margin * 2 + columns * key + (columns - 1) * gap, margin * 2 + rows * key + (rows - 1) * gap);
        using var g = Graphics.FromImage(canvas);
        g.Clear(Color.FromArgb(17, 17, 19));
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        for (int col = 0; col < columns; col++)
        {
            for (int row = 0; row < rows; row++)
            {
                int x = margin + col * (key + gap), y = margin + row * (key + gap);
                using var path = new GraphicsPath();
                path.AddArc(x, y, radius * 2, radius * 2, 180, 90);
                path.AddArc(x + key - radius * 2, y, radius * 2, radius * 2, 270, 90);
                path.AddArc(x + key - radius * 2, y + key - radius * 2, radius * 2, radius * 2, 0, 90);
                path.AddArc(x, y + key - radius * 2, radius * 2, radius * 2, 90, 90);
                path.CloseFigure();
                using var empty = new SolidBrush(Color.FromArgb(18, 18, 18));
                g.FillPath(empty, path);
                if (!keys.TryGetValue((col, row), out var image)) continue;
                g.SetClip(path);
                g.DrawImage(image, x, y, key, key);
                g.ResetClip();
            }
        }
        return canvas;
    }
}
