using NAudio.CoreAudioApi;
using NAudio.CoreAudioApi.Interfaces;

namespace FaderDisplay.Audio;

/// <summary>An active output or input device (a Core Audio endpoint).</summary>
public sealed class Endpoint(MMDevice device)
{
    public MMDevice Device { get; } = device;
    public string Id { get; } = device.ID;
    public string FullName { get; set; } = "";
    public string ShortName { get; set; } = "";
    public int Volume { get; set; }
    public bool Muted { get; set; }
}

/// <summary>All of one application's audio sessions, across every output device.</summary>
public sealed class AppGroup(string name, string? exePath)
{
    public string Name { get; } = name;
    public string? ExePath { get; } = exePath;
    public int Volume { get; set; }
    public bool Muted { get; set; } = true;
    public List<AudioSessionControl> Sessions { get; } = [];
}

/// <summary>
/// Core Audio access: device and session discovery, volume/mute and peak meters.
/// Not thread-safe; the engine calls it from its single worker thread.
/// </summary>
public sealed class AudioBackend : IDisposable
{
    private readonly MMDeviceEnumerator _enumerator = new();
    private readonly Dictionary<string, MMDevice> _devices = [];
    private readonly Dictionary<string, WasapiCapture?> _captures = [];
    private readonly ProcessInfoCache _processes = new();

    public List<Endpoint> Outputs { get; private set; } = [];
    public List<Endpoint> Inputs { get; private set; } = [];
    public List<AppGroup> Apps { get; private set; } = [];
    public string? DefaultOutputId { get; private set; }
    public string? DefaultInputId { get; private set; }

    /// <summary>Re-enumerate devices and sessions and re-read every volume.</summary>
    public void Refresh()
    {
        var seen = new HashSet<string>();
        Outputs = ReadEndpoints(DataFlow.Render, seen);
        Inputs = ReadEndpoints(DataFlow.Capture, seen);
        foreach (var id in _devices.Keys.Where(id => !seen.Contains(id)).ToList())
        {
            StopCapture(id);
            _devices[id].Dispose();
            _devices.Remove(id);
        }
        DefaultOutputId = DefaultId(DataFlow.Render);
        DefaultInputId = DefaultId(DataFlow.Capture);
        RefreshApps();
    }

    private List<Endpoint> ReadEndpoints(DataFlow flow, HashSet<string> seen)
    {
        var list = new List<Endpoint>();
        foreach (var fresh in _enumerator.EnumerateAudioEndPoints(flow, DeviceState.Active))
        {
            string id = fresh.ID;
            seen.Add(id);
            // keep one MMDevice per endpoint: its volume/meter/session objects are cached on it
            if (_devices.TryGetValue(id, out var device)) fresh.Dispose();
            else _devices[id] = device = fresh;
            try
            {
                var volume = device.AudioEndpointVolume;
                string full = device.FriendlyName;
                list.Add(new Endpoint(device)
                {
                    FullName = full,
                    ShortName = ShortName(full, device.DeviceFriendlyName),
                    Volume = (int)Math.Round(volume.MasterVolumeLevelScalar * 100),
                    Muted = volume.Mute,
                });
            }
            catch (Exception ex)
            {
                Log.Warn($"endpoint {id} unreadable: {ex.Message}");
            }
        }
        return list;
    }

    /// <summary>"Speakers (Sound Blaster G3)" -> "Speakers": the endpoint name, which the
    /// user can rename in Sound settings (the part in brackets is the adapter).</summary>
    private static string ShortName(string full, string adapter)
    {
        string suffix = $" ({adapter})";
        return full.EndsWith(suffix, StringComparison.Ordinal) && full.Length > suffix.Length
            ? full[..^suffix.Length]
            : full;
    }

    private string? DefaultId(DataFlow flow)
    {
        try
        {
            if (!_enumerator.HasDefaultAudioEndpoint(flow, Role.Multimedia)) return null;
            using var device = _enumerator.GetDefaultAudioEndpoint(flow, Role.Multimedia);
            return device.ID;
        }
        catch
        {
            return null;
        }
    }

    private void RefreshApps()
    {
        foreach (var app in Apps)
            foreach (var session in app.Sessions)
                session.Dispose();

        var groups = new Dictionary<string, AppGroup>();
        var order = new List<AppGroup>();
        foreach (var output in Outputs)
        {
            AudioSessionManager manager;
            try
            {
                manager = output.Device.AudioSessionManager;
                manager.RefreshSessions();
            }
            catch (Exception ex)
            {
                Log.Warn($"sessions on {output.FullName} unreadable: {ex.Message}");
                continue;
            }
            var sessions = manager.Sessions;
            for (int i = 0; i < sessions.Count; i++)
            {
                var session = sessions[i];
                if (!TryDescribe(session, out var info))
                {
                    session.Dispose();
                    continue;
                }
                if (!groups.TryGetValue(info.Name, out var group))
                {
                    groups[info.Name] = group = new AppGroup(info.Name, info.ExePath);
                    order.Add(group);
                }
                var volume = session.SimpleAudioVolume;
                group.Volume = Math.Max(group.Volume, (int)Math.Round(volume.Volume * 100));
                group.Muted &= volume.Mute;
                group.Sessions.Add(session);
            }
        }
        Apps = order;
    }

    /// <summary>Sessions shown on the Apps page: those of running processes that
    /// haven't expired (what Windows' own volume mixer lists), minus system sounds.</summary>
    private bool TryDescribe(AudioSessionControl session, out ProcessInfo info)
    {
        info = default;
        try
        {
            if (session.IsSystemSoundsSession || session.State == AudioSessionState.AudioSessionStateExpired)
                return false;
            uint pid = session.GetProcessID;
            if (pid == 0 || !_processes.TryGet(pid, out info)) return false;
            string display = session.DisplayName;
            if (!string.IsNullOrWhiteSpace(display) && !display.StartsWith('@'))
                info = info with { Name = display };
            return true;
        }
        catch
        {
            return false;
        }
    }

    // ---- commands -------------------------------------------------------------

    public void SetEndpointVolume(Endpoint endpoint, int percent) =>
        Try(() => endpoint.Device.AudioEndpointVolume.MasterVolumeLevelScalar = Math.Clamp(percent, 0, 100) / 100f);

    public void SetEndpointMute(Endpoint endpoint, bool mute) =>
        Try(() => endpoint.Device.AudioEndpointVolume.Mute = mute);

    public void SetAppVolume(AppGroup app, int percent)
    {
        float value = Math.Clamp(percent, 0, 100) / 100f;
        foreach (var session in app.Sessions) Try(() => session.SimpleAudioVolume.Volume = value);
    }

    public void SetAppMute(AppGroup app, bool mute)
    {
        foreach (var session in app.Sessions) Try(() => session.SimpleAudioVolume.Mute = mute);
    }

    private static void Try(Action action)
    {
        try
        {
            action();
        }
        catch (Exception ex)
        {
            Log.Warn($"audio command failed: {ex.Message}"); // device or session may have just vanished
        }
    }

    // ---- meters ---------------------------------------------------------------

    public static (float L, float R) Peaks(Endpoint endpoint)
    {
        try
        {
            return Peaks(endpoint.Device.AudioMeterInformation);
        }
        catch
        {
            return default;
        }
    }

    public static (float L, float R) Peaks(AppGroup app)
    {
        float l = 0, r = 0;
        foreach (var session in app.Sessions)
        {
            try
            {
                var (sl, sr) = Peaks(session.AudioMeterInformation);
                l = Math.Max(l, sl);
                r = Math.Max(r, sr);
            }
            catch
            {
                // session went away
            }
        }
        return (l, r);
    }

    private static (float L, float R) Peaks(AudioMeterInformation meter)
    {
        var channels = meter.PeakValues;
        return channels.Count switch
        {
            0 => default,
            1 => (channels[0], channels[0]),
            _ => (channels[0], channels[1]),
        };
    }

    /// <summary>
    /// An input endpoint's peak meter only runs while something is capturing from it,
    /// so keep a discard-everything capture open on the given inputs (only while the
    /// Inputs page shows them: Windows reports the microphone as in use meanwhile).
    /// </summary>
    public void KeepCapturing(IReadOnlyCollection<Endpoint> inputs)
    {
        var wanted = inputs.Select(e => e.Id).ToHashSet();
        foreach (var id in _captures.Keys.Where(id => !wanted.Contains(id)).ToList()) StopCapture(id);
        foreach (var input in inputs)
        {
            if (_captures.ContainsKey(input.Id)) continue;
            try
            {
                var capture = new WasapiCapture(input.Device, useEventSync: false, audioBufferMillisecondsLength: 100);
                capture.StartRecording();
                _captures[input.Id] = capture;
            }
            catch (Exception ex)
            {
                Log.Warn($"meter capture on {input.FullName} failed: {ex.Message}");
                _captures[input.Id] = null; // don't retry every frame; cleared when the device goes away
            }
        }
    }

    private void StopCapture(string id)
    {
        if (!_captures.Remove(id, out var capture) || capture is null) return;
        try
        {
            capture.StopRecording();
            capture.Dispose();
        }
        catch
        {
            // device gone
        }
    }

    public void Dispose()
    {
        foreach (var id in _captures.Keys.ToList()) StopCapture(id);
        foreach (var app in Apps)
            foreach (var session in app.Sessions)
                session.Dispose();
        foreach (var device in _devices.Values) device.Dispose();
        _enumerator.Dispose();
    }
}
