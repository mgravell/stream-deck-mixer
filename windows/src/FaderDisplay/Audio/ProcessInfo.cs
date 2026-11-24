using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace FaderDisplay.Audio;

public readonly record struct ProcessInfo(string Name, string? ExePath);

/// <summary>
/// pid -> (display name, exe path), cached; liveness is re-checked on every lookup
/// with a cheap OpenProcess/GetExitCodeProcess rather than Process.GetProcessById
/// (which snapshots every process on the machine).
/// </summary>
internal sealed class ProcessInfoCache
{
    private readonly Dictionary<uint, ProcessInfo> _cache = [];

    public bool TryGet(uint pid, out ProcessInfo info)
    {
        info = default;
        nint handle = OpenProcess(ProcessQueryLimitedInformation, false, pid);
        if (handle == 0)
        {
            _cache.Remove(pid);
            return false;
        }
        try
        {
            if (!GetExitCodeProcess(handle, out uint code) || code != StillActive)
            {
                _cache.Remove(pid);
                return false;
            }
            if (_cache.TryGetValue(pid, out info)) return true;

            string? path = ImagePath(handle);
            info = new ProcessInfo(DisplayName(path, pid), path);
            _cache[pid] = info;
            return true;
        }
        finally
        {
            CloseHandle(handle);
        }
    }

    private static string? ImagePath(nint handle)
    {
        var buffer = new StringBuilder(1024);
        int size = buffer.Capacity;
        return QueryFullProcessImageName(handle, 0, buffer, ref size) ? buffer.ToString(0, size) : null;
    }

    /// <summary>File description ("Google Chrome", "Spotify") beats the bare exe name.</summary>
    private static string DisplayName(string? path, uint pid)
    {
        if (path is null) return $"pid {pid}";
        try
        {
            string? description = FileVersionInfo.GetVersionInfo(path).FileDescription?.Trim();
            if (!string.IsNullOrEmpty(description)) return description;
        }
        catch
        {
            // fall through to the file name
        }
        return Path.GetFileNameWithoutExtension(path);
    }

    private const uint ProcessQueryLimitedInformation = 0x1000;
    private const uint StillActive = 259;

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern nint OpenProcess(uint access, bool inherit, uint pid);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetExitCodeProcess(nint handle, out uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(nint handle);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool QueryFullProcessImageName(nint handle, uint flags, StringBuilder name, ref int size);
}
