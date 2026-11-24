using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace FaderDisplay;

public readonly record struct Rgb(byte R, byte G, byte B)
{
    public Rgb Scale(double factor) =>
        new((byte)Math.Round(R * factor), (byte)Math.Round(G * factor), (byte)Math.Round(B * factor));

    public static Rgb? FromJson(JsonElement element)
    {
        if (element.ValueKind != JsonValueKind.Array || element.GetArrayLength() < 3) return null;
        return new((byte)element[0].GetInt32(), (byte)element[1].GetInt32(), (byte)element[2].GetInt32());
    }
}

/// <summary>Stable colors for unconfigured apps/devices: same palette and
/// MD5-first-byte hash as the Linux plugin, so a name gets the same color on both.</summary>
public static class Palette
{
    private static readonly Rgb[] Colors =
    [
        new(0, 165, 185),   // teal
        new(150, 90, 220),  // purple
        new(225, 125, 0),   // orange
        new(205, 60, 130),  // pink
        new(95, 180, 60),   // lime
        new(90, 130, 235),  // steel blue
        new(220, 180, 0),   // gold
        new(200, 80, 60),   // brick
    ];

    public static readonly Rgb Master = new(235, 145, 35);

    public static Rgb ForName(string name) =>
        Colors[MD5.HashData(Encoding.UTF8.GetBytes(name))[0] % Colors.Length];
}

public sealed record DeviceEntry(string Match, string? Label, Rgb? Color);

/// <summary>
/// Optional %APPDATA%\FaderDisplay\fader-display.json, re-read when it changes:
/// <code>
/// {
///   "outputs": [{"match": "Speakers (Sound Blaster G3)", "label": "G3"}],
///   "inputs":  [{"match": "Microphone (USB Mic)", "color": [45, 175, 85]}],
///   "master":  {"color": [235, 145, 35]}
/// }
/// </code>
/// "match" compares against the device's full name (as Sound settings shows it,
/// e.g. "Speakers (Sound Blaster G3)"), its short endpoint name ("Speakers") or
/// its endpoint ID. Listed devices come first in order (after the default device).
/// </summary>
public sealed class Config
{
    public static string Directory { get; } =
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "FaderDisplay");

    public static string FilePath { get; } = Path.Combine(Directory, "fader-display.json");

    public IReadOnlyList<DeviceEntry> Outputs { get; private set; } = [];
    public IReadOnlyList<DeviceEntry> Inputs { get; private set; } = [];
    public Rgb? MasterColor { get; private set; }

    private DateTime? _loadedStamp = DateTime.MinValue;

    /// <summary>Re-read the file if its timestamp changed; cheap enough to call on every refresh.</summary>
    public void Reload()
    {
        DateTime? stamp = File.Exists(FilePath) ? File.GetLastWriteTimeUtc(FilePath) : null;
        if (stamp == _loadedStamp) return;
        _loadedStamp = stamp;
        Outputs = [];
        Inputs = [];
        MasterColor = null;
        if (stamp is null) return;
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(FilePath),
                new JsonDocumentOptions { CommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true });
            var root = doc.RootElement;
            Outputs = ReadEntries(root, "outputs");
            Inputs = ReadEntries(root, "inputs");
            if (root.TryGetProperty("master", out var master) && master.TryGetProperty("color", out var color))
                MasterColor = Rgb.FromJson(color);
        }
        catch (Exception ex)
        {
            Log.Warn($"bad config {FilePath}: {ex.Message}");
        }
    }

    private static List<DeviceEntry> ReadEntries(JsonElement root, string name)
    {
        var list = new List<DeviceEntry>();
        if (!root.TryGetProperty(name, out var array) || array.ValueKind != JsonValueKind.Array) return list;
        foreach (var item in array.EnumerateArray())
        {
            if (!item.TryGetProperty("match", out var match) || match.GetString() is not { Length: > 0 } m) continue;
            string? label = item.TryGetProperty("label", out var l) ? l.GetString() : null;
            Rgb? color = item.TryGetProperty("color", out var c) ? Rgb.FromJson(c) : null;
            list.Add(new DeviceEntry(m, label, color));
        }
        return list;
    }
}
