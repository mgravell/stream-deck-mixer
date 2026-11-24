using Windows.Media.Control;

namespace FaderDisplay;

public enum MediaStatus { NoPlayer, Stopped, Paused, Playing }

/// <summary>
/// Play/pause through Windows' system media transport controls - the same session
/// the keyboard media keys and the volume flyout drive (Spotify, browsers, ...).
/// </summary>
public sealed class MediaControl
{
    private GlobalSystemMediaTransportControlsSessionManager? _manager;
    private DateTime _nextAttempt = DateTime.MinValue;

    public MediaStatus Status()
    {
        var session = Session();
        if (session is null) return MediaStatus.NoPlayer;
        try
        {
            return session.GetPlaybackInfo().PlaybackStatus switch
            {
                GlobalSystemMediaTransportControlsSessionPlaybackStatus.Playing => MediaStatus.Playing,
                GlobalSystemMediaTransportControlsSessionPlaybackStatus.Paused => MediaStatus.Paused,
                _ => MediaStatus.Stopped,
            };
        }
        catch
        {
            return MediaStatus.NoPlayer;
        }
    }

    public void TogglePlayPause()
    {
        var session = Session();
        if (session is null) return;
        _ = session.TryTogglePlayPauseAsync().AsTask().ContinueWith(
            t => Log.Warn($"play/pause failed: {t.Exception?.GetBaseException().Message}"),
            TaskContinuationOptions.OnlyOnFaulted);
    }

    private GlobalSystemMediaTransportControlsSession? Session()
    {
        if (_manager is null && DateTime.UtcNow >= _nextAttempt)
        {
            try
            {
                _manager = GlobalSystemMediaTransportControlsSessionManager.RequestAsync().AsTask().GetAwaiter().GetResult();
            }
            catch (Exception ex)
            {
                _nextAttempt = DateTime.UtcNow.AddSeconds(30);
                Log.Warn($"media session manager unavailable: {ex.Message}");
            }
        }
        try
        {
            return _manager?.GetCurrentSession();
        }
        catch
        {
            return null;
        }
    }
}
