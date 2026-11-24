using System.Collections.Concurrent;
using System.Diagnostics;
using FaderDisplay.Audio;
using FaderDisplay.Render;

namespace FaderDisplay;

public interface IKeySink
{
    void SetImage(string context, byte[] png);
}

/// <summary>
/// The mixer: owns the audio backend and every key's state on one worker thread.
/// Stream Deck events are posted in; each frame it rebuilds every key's
/// <see cref="KeyVisual"/> and sends only the ones that changed.
///
/// Layout is derived from key coordinates, so every key carries the same action:
/// on a deck with C columns and R rows, columns 0..C-2 are fader columns (top row
/// volume up, second row volume down, bottom row mute) and the last column holds
/// the page-mode key (top), the pager (second row) and play/pause (bottom).
/// </summary>
public sealed class Engine : IDisposable
{
    private static readonly TimeSpan RefreshInterval = TimeSpan.FromMilliseconds(500);
    private static readonly TimeSpan FrameInterval = TimeSpan.FromSeconds(1 / 15.0);
    private static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(30);
    private const int Step = 10;

    private sealed record KeyInfo(string Device, int Column, int Row);

    /// <summary>One fader column's worth of state, rebuilt on every refresh.</summary>
    private sealed record Strip(
        string MeterId, string Label, Rgb Color, int Volume, bool Muted, bool Accent, string? IconPath,
        Func<(float, float)> Peaks, Action<int> SetVolume, Action<bool> SetMute);

    private readonly IKeySink _sink;
    private readonly ConcurrentQueue<Action> _inbox = new();
    private readonly AutoResetEvent _wake = new(false);
    private readonly Thread _thread;
    private volatile bool _stopping;

    private readonly Dictionary<string, KeyInfo> _keys = [];
    private readonly Dictionary<string, (int Columns, int Rows)> _devices = [];
    private readonly Dictionary<string, KeyVisual> _sent = [];

    private readonly Config _config = new();
    private readonly IconResolver _icons = new();
    private readonly MediaControl _media = new();
    private readonly Dictionary<string, Meter> _meters = [];
    private readonly Dictionary<string, int> _appOrder = [];
    private readonly Dictionary<Page, int> _offsets = new() { [Page.Apps] = 0, [Page.Outputs] = 0, [Page.Inputs] = 0 };
    private AudioBackend? _audio;
    private Page _page = Page.Apps;
    private List<Strip> _strips = [];
    private MediaStatus _mediaStatus = MediaStatus.NoPlayer;

    public Engine(IKeySink sink)
    {
        _sink = sink;
        _thread = new Thread(Run) { IsBackground = true, Name = "fader-engine" };
        _thread.SetApartmentState(ApartmentState.MTA);
        _thread.Start();
    }

    // ---- events in (any thread) -------------------------------------------------

    public void DeviceConnected(string device, int columns, int rows) => Post(() =>
    {
        _devices[device] = (columns, rows);
        foreach (var (context, key) in _keys)
            if (key.Device == device) _sent.Remove(context);
    });

    public void KeyAppeared(string context, string device, int column, int row) => Post(() =>
    {
        _keys[context] = new KeyInfo(device, column, row);
        _sent.Remove(context); // always paint a newly visible key
    });

    public void KeyDisappeared(string context) => Post(() =>
    {
        _keys.Remove(context);
        _sent.Remove(context);
    });

    public void KeyDown(string context) => Post(() => Press(context));

    /// <summary>Resend every key (e.g. after the system wakes and the deck reconnects).</summary>
    public void RedrawAll() => Post(_sent.Clear);

    private void Post(Action action)
    {
        _inbox.Enqueue(action);
        _wake.Set();
    }

    // ---- worker loop --------------------------------------------------------------

    private void Run()
    {
        var clock = Stopwatch.StartNew();
        TimeSpan nextRefresh = TimeSpan.Zero, nextFrame = TimeSpan.Zero;
        while (!_stopping)
        {
            try
            {
                bool acted = false;
                while (_inbox.TryDequeue(out var action))
                {
                    action();
                    acted = true;
                }
                var now = clock.Elapsed;
                if (acted || now >= nextRefresh)
                {
                    Refresh();
                    nextRefresh = now + RefreshInterval;
                }
                PollMeters(now);
                if (acted || now >= nextFrame)
                {
                    Frame(now);
                    nextFrame = now - nextFrame > FrameInterval ? now + FrameInterval : nextFrame + FrameInterval;
                }
            }
            catch (Exception ex)
            {
                Log.Error($"engine: {ex}");
                ResetAudio();
                Thread.Sleep(1000);
            }
            _wake.WaitOne(PollInterval);
        }
    }

    private void ResetAudio()
    {
        try
        {
            _audio?.Dispose();
        }
        catch
        {
            // already broken
        }
        _audio = null;
        _strips = [];
    }

    private void Refresh()
    {
        _audio ??= new AudioBackend();
        _config.Reload();
        _audio.Refresh();
        _mediaStatus = _media.Status();
        _strips = _page switch
        {
            Page.Apps => AppStrips(_audio),
            Page.Outputs => EndpointStrips(_audio, _audio.Outputs, _audio.DefaultOutputId, _config.Outputs, master: true),
            _ => EndpointStrips(_audio, _audio.Inputs, _audio.DefaultInputId, _config.Inputs, master: false),
        };
        int perPage = PerPage();
        if (_offsets[_page] >= _strips.Count || perPage <= 0) _offsets[_page] = 0;

        // An input's meter only runs while something captures from it. Open a capture on the
        // default input alone, and only while the Inputs page shows it: some interfaces (the
        // Sound Blaster G3, for one) allow a single capture across all their inputs, so
        // capturing more could lock the microphone away from a chat app. Other inputs meter
        // whenever another app is recording from them.
        _audio.KeepCapturing(_page == Page.Inputs
            ? _audio.Inputs.Where(e => e.Id == _audio.DefaultInputId && VisibleStrips().Any(s => s.MeterId == "in:" + e.Id)).ToList()
            : []);
    }

    private List<Strip> AppStrips(AudioBackend audio)
    {
        foreach (var app in audio.Apps) _appOrder.TryAdd(app.Name, _appOrder.Count); // stable first-seen columns
        return audio.Apps
            .OrderBy(a => _appOrder[a.Name])
            .Select(app => new Strip(
                "app:" + app.Name, app.Name, Palette.ForName(app.Name), app.Volume, app.Muted, false, app.ExePath,
                () => AudioBackend.Peaks(app),
                v => audio.SetAppVolume(app, v),
                m => audio.SetAppMute(app, m)))
            .ToList();
    }

    /// <summary>Default device first (accented; on Outputs it takes the "master" color), then
    /// devices listed in the config in order, then the rest alphabetically.</summary>
    private List<Strip> EndpointStrips(AudioBackend audio, List<Endpoint> endpoints, string? defaultId,
        IReadOnlyList<DeviceEntry> entries, bool master)
    {
        DeviceEntry? EntryFor(Endpoint e) => entries.FirstOrDefault(x =>
            x.Match == e.Id || x.Match.Equals(e.FullName, StringComparison.OrdinalIgnoreCase) ||
            x.Match.Equals(e.ShortName, StringComparison.OrdinalIgnoreCase));

        var ordered = endpoints
            .OrderBy(e => e.Id == defaultId ? -1 : EntryFor(e) is { } x ? entries.ToList().IndexOf(x) : int.MaxValue)
            .ThenBy(e => e.FullName, StringComparer.OrdinalIgnoreCase)
            .ToList();
        // short endpoint names ("Speakers") unless two devices share one
        var ambiguous = endpoints.GroupBy(e => e.ShortName).Where(g => g.Count() > 1).Select(g => g.Key).ToHashSet();
        string prefix = master ? "out:" : "in:";

        return ordered.Select(e =>
        {
            var entry = EntryFor(e);
            bool isDefault = e.Id == defaultId;
            var color = isDefault && master
                ? _config.MasterColor ?? entry?.Color ?? Palette.Master
                : entry?.Color ?? Palette.ForName(e.FullName);
            string label = entry?.Label ?? (ambiguous.Contains(e.ShortName) ? e.FullName : e.ShortName);
            return new Strip(prefix + e.Id, label, color, e.Volume, e.Muted, isDefault, null,
                () => AudioBackend.Peaks(e),
                v => audio.SetEndpointVolume(e, v),
                m => audio.SetEndpointMute(e, m));
        }).ToList();
    }

    /// <summary>Fader columns per page: every column but the utility one, on the narrowest deck.</summary>
    private int PerPage() => _devices.Count == 0 ? 4 : Math.Max(1, _devices.Values.Min(d => d.Columns) - 1);

    private IEnumerable<Strip> VisibleStrips() => _strips.Skip(_offsets[_page]).Take(PerPage());

    private void PollMeters(TimeSpan now)
    {
        foreach (var strip in VisibleStrips())
        {
            if (!_meters.TryGetValue(strip.MeterId, out var meter)) _meters[strip.MeterId] = meter = new Meter();
            var (l, r) = strip.Muted ? (0f, 0f) : strip.Peaks(); // muted = silent meter, as on Linux
            meter.Feed(l, r, now);
        }
    }

    // ---- frames -----------------------------------------------------------------

    private void Frame(TimeSpan now)
    {
        var levels = new Dictionary<string, (double, double, double, double)>();
        foreach (var (id, meter) in _meters.ToList())
        {
            if (now - meter.LastFed > TimeSpan.FromSeconds(5)) _meters.Remove(id); // off screen
            else levels[id] = meter.Frame(now);
        }

        foreach (var (context, key) in _keys)
        {
            var visual = Visual(key, levels);
            if (_sent.TryGetValue(context, out var previous) && previous.Equals(visual)) continue;
            try
            {
                _sink.SetImage(context, KeyRenderer.RenderPng(visual, _icons.Get));
                _sent[context] = visual;
            }
            catch (Exception ex)
            {
                Log.Warn($"render {visual} failed: {ex.Message}");
            }
        }
    }

    private enum Role { Blank, Fader, Mode, Pager, PlayPause }

    private (Role Role, int Rows) RoleOf(KeyInfo key)
    {
        var (columns, rows) = _devices.TryGetValue(key.Device, out var size) ? size : (5, 3);
        if (key.Column < columns - 1) return (Role.Fader, rows);
        if (key.Row == 0) return (Role.Mode, rows);
        if (key.Row == rows - 1) return (Role.PlayPause, rows);
        if (key.Row == 1) return (Role.Pager, rows);
        return (Role.Blank, rows);
    }

    private KeyVisual Visual(KeyInfo key, Dictionary<string, (double BarL, double BarR, double HoldL, double HoldR)> levels)
    {
        var (role, rows) = RoleOf(key);
        switch (role)
        {
            case Role.Mode: return new ModeKey(_page);
            case Role.Pager: return new PagerKey(_strips.Count, _offsets[_page], PerPage());
            case Role.PlayPause: return new PlayPauseKey(_mediaStatus);
            case Role.Blank: return EmptyKey.Instance;
        }

        int index = _offsets[_page] + key.Column;
        if (key.Column >= PerPage() || index >= _strips.Count) return EmptyKey.Instance;
        var strip = _strips[index];
        string? icon = key.Row == 0 ? strip.IconPath : null;
        return FaderVisual(key.Row, rows, _page, strip.Label, strip.Color, strip.Volume, strip.Muted, strip.Accent,
            icon, icon is not null && _icons.Get(icon) is not null, levels.GetValueOrDefault(strip.MeterId));
    }

    /// <summary>One key of a fader column (also used by the preview renderer).</summary>
    internal static FaderKey FaderVisual(int row, int rows, Page page, string label, Rgb color, int volume, bool muted,
        bool accent, string? iconPath, bool iconReady, (double BarL, double BarR, double HoldL, double HoldR) level)
    {
        // this key's slice of the column: bottom row 0..1/R, top row (R-1)/R..1
        double lo = (double)(rows - 1 - row) / rows, hi = (double)(rows - row) / rows;
        int Px(double frac) => (int)Math.Round(96 * Math.Clamp((frac - lo) / (hi - lo), 0, 1));
        int Quantized(double frac) => Px(frac) / 2 * 2;
        int Hold(double frac) => frac > 0 && frac > lo && frac <= hi ? Quantized(frac) : -1;
        int Edge(double frac) => (int)Math.Round(96 * (frac - lo) / (hi - lo));

        bool top = row == 0, bottom = row == rows - 1;
        var glyph = top ? Glyph.Up : bottom ? (page == Page.Inputs ? Glyph.Mic : Glyph.Mute) : row == 1 ? Glyph.Down : Glyph.None;
        string? text = bottom ? Truncate(label) : row == 1 ? volume.ToString() : null;

        return new FaderKey(
            glyph, Px(volume / 100.0), muted ? color.Scale(0.35) : color, muted,
            Quantized(level.BarL), Quantized(level.BarR), Hold(level.HoldL), Hold(level.HoldR),
            Edge(Meter.YellowFrom), Edge(Meter.RedFrom),
            text, top ? iconPath : null, top && iconReady,
            accent, accent && top, accent && bottom);
    }

    private static string Truncate(string label) => label.Length <= 9 ? label : label[..8] + "…";

    // ---- presses ------------------------------------------------------------------

    private void Press(string context)
    {
        if (!_keys.TryGetValue(context, out var key)) return;
        var (role, rows) = RoleOf(key);
        switch (role)
        {
            case Role.Mode:
                _page = _page switch { Page.Apps => Page.Outputs, Page.Outputs => Page.Inputs, _ => Page.Apps };
                break;
            case Role.Pager:
                int perPage = PerPage();
                _offsets[_page] = _strips.Count > perPage && _offsets[_page] + perPage < _strips.Count
                    ? _offsets[_page] + perPage
                    : 0;
                break;
            case Role.PlayPause:
                _media.TogglePlayPause();
                break;
            case Role.Fader:
                int index = _offsets[_page] + key.Column;
                if (key.Column >= PerPage() || index >= _strips.Count) return;
                var strip = _strips[index];
                if (key.Row == 0) strip.SetVolume(strip.Volume + Step);
                else if (key.Row == rows - 1) strip.SetMute(!strip.Muted);
                else if (key.Row == 1) strip.SetVolume(strip.Volume - Step);
                break;
        }
    }

    public void Dispose()
    {
        _stopping = true;
        _wake.Set();
        _thread.Join(TimeSpan.FromSeconds(2));
        _audio?.Dispose();
    }
}
