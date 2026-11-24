namespace FaderDisplay;

/// <summary>Minimal file log: %APPDATA%\FaderDisplay\plugin.log (truncated at startup when over 1 MB).</summary>
public static class Log
{
    private static readonly Lock Sync = new();
    private static readonly string FilePath = Path.Combine(Config.Directory, "plugin.log");
    public static bool ToConsole { get; set; }

    static Log()
    {
        try
        {
            System.IO.Directory.CreateDirectory(Config.Directory);
            if (File.Exists(FilePath) && new FileInfo(FilePath).Length > 1_000_000) File.Delete(FilePath);
        }
        catch
        {
            // logging must never take the plugin down
        }
    }

    public static void Info(string message) => Write("INFO", message);
    public static void Warn(string message) => Write("WARN", message);
    public static void Error(string message) => Write("ERROR", message);

    private static void Write(string level, string message)
    {
        string line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} {level} {message}";
        if (ToConsole) Console.Error.WriteLine(line);
        try
        {
            lock (Sync) File.AppendAllText(FilePath, line + Environment.NewLine);
        }
        catch
        {
            // ignore
        }
    }
}
