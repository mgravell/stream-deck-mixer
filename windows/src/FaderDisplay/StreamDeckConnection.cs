using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Channels;

namespace FaderDisplay;

/// <summary>
/// The Stream Deck SDK's plugin protocol: a WebSocket to the Stream Deck app on
/// localhost, JSON events in both directions. Only the handful of events this
/// plugin needs are handled; see https://docs.elgato.com/streamdeck/sdk/.
/// </summary>
public sealed class StreamDeckConnection(int port, string pluginUuid, string registerEvent) : IKeySink
{
    private readonly ClientWebSocket _socket = new();
    private readonly Channel<string> _outbox = Channel.CreateUnbounded<string>(new() { SingleReader = true });

    public event Action<JsonElement>? MessageReceived;

    public async Task RunAsync(CancellationToken cancel)
    {
        await _socket.ConnectAsync(new Uri($"ws://127.0.0.1:{port}"), cancel);
        await SendNowAsync(new JsonObject { ["event"] = registerEvent, ["uuid"] = pluginUuid }.ToJsonString(), cancel);
        Log.Info($"registered with Stream Deck on port {port}");

        var sender = Task.Run(async () =>
        {
            await foreach (var message in _outbox.Reader.ReadAllAsync(cancel))
                await SendNowAsync(message, cancel);
        }, cancel);

        var buffer = new byte[64 * 1024];
        using var message = new MemoryStream();
        while (_socket.State == WebSocketState.Open && !cancel.IsCancellationRequested)
        {
            var result = await _socket.ReceiveAsync(buffer, cancel);
            if (result.MessageType == WebSocketMessageType.Close) break;
            message.Write(buffer, 0, result.Count);
            if (!result.EndOfMessage) continue;
            try
            {
                using var doc = JsonDocument.Parse(message.ToArray());
                MessageReceived?.Invoke(doc.RootElement);
            }
            catch (Exception ex)
            {
                Log.Error($"handling {Encoding.UTF8.GetString(message.ToArray())}: {ex}");
            }
            message.SetLength(0);
        }
        _outbox.Writer.TryComplete();
        Log.Info("Stream Deck connection closed");
    }

    public void SetImage(string context, byte[] png) => Send(new JsonObject
    {
        ["event"] = "setImage",
        ["context"] = context,
        ["payload"] = new JsonObject
        {
            ["image"] = "data:image/png;base64," + Convert.ToBase64String(png),
            ["target"] = 0,
        },
    });

    private void Send(JsonObject message) => _outbox.Writer.TryWrite(message.ToJsonString());

    private Task SendNowAsync(string message, CancellationToken cancel) =>
        _socket.SendAsync(Encoding.UTF8.GetBytes(message), WebSocketMessageType.Text, true, cancel);
}
