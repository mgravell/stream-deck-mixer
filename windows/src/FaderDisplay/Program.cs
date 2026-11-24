using System.Text.Json;
using FaderDisplay;

// Launched by the Stream Deck app as:
//   FaderDisplay.exe -port <n> -pluginUUID <uuid> -registerEvent <event> -info <json>
// Developer modes (run from a terminal):
//   FaderDisplay.exe --preview <dir>   render the docs preview images
//   FaderDisplay.exe --probe           print live audio devices/apps/meters for a few seconds
//   FaderDisplay.exe --harness <dir> [col,row ...]
//                                      run the plugin against a fake Stream Deck host, saving key images

var options = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
for (int i = 0; i + 1 < args.Length; i += 2) options[args[i].TrimStart('-')] = args[i + 1];

try
{
    if (args.Length > 0 && args[0] == "--preview")
    {
        DevTools.UseParentConsole();
        Log.ToConsole = true;
        Preview.Write(args.Length > 1 ? args[1] : ".");
        return 0;
    }
    if (args.Length > 0 && args[0] == "--probe")
    {
        DevTools.UseParentConsole();
        Log.ToConsole = true;
        DevTools.Probe();
        return 0;
    }
    if (args.Length > 0 && args[0] == "--harness")
    {
        DevTools.UseParentConsole();
        Log.ToConsole = true;
        return await DevTools.HarnessAsync(args.Length > 1 ? args[1] : ".", args.Skip(2).ToList());
    }

    if (!options.TryGetValue("port", out var port) || !options.TryGetValue("pluginUUID", out var uuid) ||
        !options.TryGetValue("registerEvent", out var registerEvent))
    {
        Console.Error.WriteLine("FaderDisplay is a Stream Deck plugin; it is started by the Stream Deck app.");
        return 1;
    }

    var connection = new StreamDeckConnection(int.Parse(port), uuid, registerEvent);
    using var engine = new Engine(connection);

    if (options.TryGetValue("info", out var info))
    {
        using var doc = JsonDocument.Parse(info);
        if (doc.RootElement.TryGetProperty("devices", out var devices))
            foreach (var device in devices.EnumerateArray())
                AddDevice(device.GetProperty("id").GetString()!, device);
    }

    connection.MessageReceived += message =>
    {
        string? evt = message.TryGetProperty("event", out var e) ? e.GetString() : null;
        string context = message.TryGetProperty("context", out var c) ? c.GetString() ?? "" : "";
        switch (evt)
        {
            case "willAppear":
                var payload = message.GetProperty("payload");
                if (!payload.TryGetProperty("coordinates", out var coordinates)) break; // inside a multi-action
                engine.KeyAppeared(context, message.GetProperty("device").GetString()!,
                    coordinates.GetProperty("column").GetInt32(), coordinates.GetProperty("row").GetInt32());
                break;
            case "willDisappear":
                engine.KeyDisappeared(context);
                break;
            case "keyDown":
                engine.KeyDown(context);
                break;
            case "deviceDidConnect":
                AddDevice(message.GetProperty("device").GetString()!, message.GetProperty("deviceInfo"));
                break;
            case "systemDidWakeUp":
                engine.RedrawAll();
                break;
        }
    };

    await connection.RunAsync(CancellationToken.None);
    return 0;

    void AddDevice(string id, JsonElement device)
    {
        var size = device.GetProperty("size");
        engine.DeviceConnected(id, size.GetProperty("columns").GetInt32(), size.GetProperty("rows").GetInt32());
    }
}
catch (Exception ex)
{
    Log.Error($"fatal: {ex}");
    return 2;
}
