using System.Diagnostics;
using System.Drawing;
using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using FaderDisplay.Audio;

namespace FaderDisplay;

/// <summary>Developer modes for checking the plugin without a Stream Deck attached.</summary>
public static class DevTools
{
    [DllImport("kernel32.dll")]
    private static extern bool AttachConsole(int processId);

    /// <summary>WinExe has no console of its own; borrow the terminal's.</summary>
    public static void UseParentConsole()
    {
        if (AttachConsole(-1))
        {
            Console.SetOut(new StreamWriter(Console.OpenStandardOutput()) { AutoFlush = true });
            Console.SetError(new StreamWriter(Console.OpenStandardError()) { AutoFlush = true });
        }
    }

    /// <summary>Print what the backend sees, then a few seconds of meter readings.</summary>
    public static void Probe()
    {
        using var audio = new AudioBackend();
        audio.Refresh();
        Console.WriteLine($"default output: {audio.DefaultOutputId}");
        Console.WriteLine($"default input:  {audio.DefaultInputId}");
        foreach (var (title, list) in new[] { ("Outputs", audio.Outputs), ("Inputs", audio.Inputs) })
        {
            Console.WriteLine(title + ":");
            foreach (var e in list)
                Console.WriteLine($"  {e.FullName,-50} short='{e.ShortName}' vol={e.Volume}{(e.Muted ? " MUTED" : "")}");
        }
        Console.WriteLine("Apps:");
        foreach (var app in audio.Apps)
            Console.WriteLine($"  {app.Name,-30} sessions={app.Sessions.Count} vol={app.Volume}{(app.Muted ? " MUTED" : "")} exe={app.ExePath}");

        var media = new MediaControl();
        Console.WriteLine($"media: {media.Status()}");

        audio.KeepCapturing(audio.Inputs.Where(e => e.Id == audio.DefaultInputId).ToList());
        Console.WriteLine("meters (L/R peak, 10 samples):");
        for (int i = 0; i < 10; i++)
        {
            Thread.Sleep(200);
            var parts = audio.Outputs.Select(e => $"{e.ShortName}={Fmt(AudioBackend.Peaks(e))}")
                .Concat(audio.Inputs.Select(e => $"[in]{e.ShortName}={Fmt(AudioBackend.Peaks(e))}"))
                .Concat(audio.Apps.Select(a => $"{a.Name}={Fmt(AudioBackend.Peaks(a))}"));
            Console.WriteLine("  " + string.Join("  ", parts));
        }
        audio.KeepCapturing([]);

        static string Fmt((float L, float R) p) => $"{p.L:0.00}/{p.R:0.00}";
    }

    /// <summary>
    /// Stand in for the Stream Deck app: listen on a local port, launch this exe as the
    /// plugin, show it a 5x3 deck, press the page-mode key through each page and save
    /// what the keys displayed after each step as harness-*.png. Optional "col,row"
    /// presses are made on the Apps page first (each saved as harness-press-N.png) -
    /// note these really change volumes.
    /// </summary>
    public static async Task<int> HarnessAsync(string directory, IReadOnlyList<string> presses)
    {
        Directory.CreateDirectory(directory);
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        int port = ((IPEndPoint)listener.LocalEndpoint).Port;
        string info = new JsonObject
        {
            ["devices"] = new JsonArray(new JsonObject
            {
                ["id"] = "DECK1", ["name"] = "Fake MK.2", ["type"] = 7,
                ["size"] = new JsonObject { ["columns"] = 5, ["rows"] = 3 },
            }),
        }.ToJsonString();

        var plugin = Process.Start(new ProcessStartInfo(Environment.ProcessPath!)
        {
            ArgumentList = { "-port", port.ToString(), "-pluginUUID", "HARNESS", "-registerEvent", "registerPlugin", "-info", info },
            UseShellExecute = false,
        })!;
        try
        {
            using var client = await listener.AcceptTcpClientAsync().WaitAsync(TimeSpan.FromSeconds(10));
            var stream = client.GetStream();
            await HandshakeAsync(stream);
            using var socket = WebSocket.CreateFromStream(stream, new WebSocketCreationOptions { IsServer = true });

            var latest = new Dictionary<string, byte[]>();
            int imageCount = 0;
            var receiver = Task.Run(async () =>
            {
                var buffer = new byte[1 << 20];
                using var message = new MemoryStream();
                while (socket.State == WebSocketState.Open)
                {
                    var result = await socket.ReceiveAsync(buffer, CancellationToken.None);
                    if (result.MessageType == WebSocketMessageType.Close) break;
                    message.Write(buffer, 0, result.Count);
                    if (!result.EndOfMessage) continue;
                    using var doc = JsonDocument.Parse(message.ToArray());
                    message.SetLength(0);
                    var root = doc.RootElement;
                    string evt = root.GetProperty("event").GetString()!;
                    if (evt == "registerPlugin") Console.WriteLine($"plugin registered: {root.GetProperty("uuid")}");
                    else if (evt == "setImage")
                    {
                        string data = root.GetProperty("payload").GetProperty("image").GetString()!;
                        lock (latest) latest[root.GetProperty("context").GetString()!] = Convert.FromBase64String(data[(data.IndexOf(',') + 1)..]);
                        Interlocked.Increment(ref imageCount);
                    }
                }
            });

            async Task Send(JsonObject message) =>
                await socket.SendAsync(Encoding.UTF8.GetBytes(message.ToJsonString()), WebSocketMessageType.Text, true, CancellationToken.None);

            for (int col = 0; col < 5; col++)
                for (int row = 0; row < 3; row++)
                    await Send(KeyEvent("willAppear", col, row));

            await Task.Delay(TimeSpan.FromSeconds(2));
            for (int i = 0; i < presses.Count; i++)
            {
                var xy = presses[i].Split(',').Select(int.Parse).ToArray();
                await Send(KeyEvent("keyDown", xy[0], xy[1]));
                await Send(KeyEvent("keyUp", xy[0], xy[1]));
                await Task.Delay(TimeSpan.FromSeconds(1));
                SaveComposite(latest, Path.Combine(directory, $"harness-press-{i + 1}.png"));
            }

            foreach (string page in new[] { "apps", "outputs", "inputs" })
            {
                int before = imageCount;
                await Task.Delay(TimeSpan.FromSeconds(3));
                Console.WriteLine($"{page}: {imageCount - before} images in 3s");
                SaveComposite(latest, Path.Combine(directory, $"harness-{page}.png"));
                await Send(KeyEvent("keyDown", 4, 0)); // page-mode key
                await Send(KeyEvent("keyUp", 4, 0));
            }

            await socket.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "done", CancellationToken.None);
            bool exited = plugin.WaitForExit(5000);
            Console.WriteLine(exited ? $"plugin exited with code {plugin.ExitCode}" : "plugin did not exit after close");
            return exited ? 0 : 1;
        }
        finally
        {
            if (!plugin.HasExited) plugin.Kill();
            listener.Stop();
        }

        static JsonObject KeyEvent(string evt, int col, int row) => new()
        {
            ["action"] = "com.marc.faderdisplay.key",
            ["event"] = evt,
            ["context"] = $"ctx-{col}-{row}",
            ["device"] = "DECK1",
            ["payload"] = new JsonObject
            {
                ["settings"] = new JsonObject(),
                ["coordinates"] = new JsonObject { ["column"] = col, ["row"] = row },
                ["state"] = 0,
                ["isInMultiAction"] = false,
            },
        };
    }

    private static void SaveComposite(Dictionary<string, byte[]> latest, string path)
    {
        var images = new Dictionary<(int, int), Bitmap>();
        lock (latest)
        {
            foreach (var (context, png) in latest)
            {
                var parts = context.Split('-');
                images[(int.Parse(parts[1]), int.Parse(parts[2]))] = new Bitmap(new MemoryStream(png));
            }
        }
        using var composite = Preview.Compose(images, 5, 3);
        composite.Save(path, System.Drawing.Imaging.ImageFormat.Png);
        foreach (var image in images.Values) image.Dispose();
        Console.WriteLine($"  saved {path}");
    }

    private static async Task HandshakeAsync(NetworkStream stream)
    {
        var request = new StringBuilder();
        var buffer = new byte[1];
        while (!request.ToString().EndsWith("\r\n\r\n"))
        {
            if (await stream.ReadAsync(buffer) == 0) throw new IOException("client hung up during handshake");
            request.Append((char)buffer[0]);
        }
        string key = request.ToString().Split("\r\n")
            .First(l => l.StartsWith("Sec-WebSocket-Key:", StringComparison.OrdinalIgnoreCase))
            .Split(':', 2)[1].Trim();
        string accept = Convert.ToBase64String(SHA1.HashData(Encoding.ASCII.GetBytes(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")));
        await stream.WriteAsync(Encoding.ASCII.GetBytes(
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" +
            $"Sec-WebSocket-Accept: {accept}\r\n\r\n"));
    }
}
