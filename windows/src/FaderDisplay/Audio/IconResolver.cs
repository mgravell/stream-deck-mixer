using System.Collections.Concurrent;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

namespace FaderDisplay.Audio;

/// <summary>
/// Application icons from the shell (the same icon Explorer shows for the exe),
/// resolved on a background STA thread and cached by exe path. <see cref="Get"/>
/// returns null until the icon is ready; the engine notices the change on its
/// next frame and redraws.
/// </summary>
public sealed class IconResolver
{
    private readonly ConcurrentDictionary<string, Bitmap?> _cache = new(StringComparer.OrdinalIgnoreCase);
    private readonly BlockingCollection<string> _queue = [];

    public IconResolver()
    {
        var thread = new Thread(Worker) { IsBackground = true, Name = "icon-resolver" };
        thread.SetApartmentState(ApartmentState.STA); // shell icon handlers expect STA
        thread.Start();
    }

    public Bitmap? Get(string? exePath)
    {
        if (string.IsNullOrEmpty(exePath)) return null;
        if (_cache.TryGetValue(exePath, out var icon)) return icon;
        if (_cache.TryAdd(exePath, null)) _queue.Add(exePath);
        return null;
    }

    /// <summary>Synchronous lookup (for the preview renderer).</summary>
    public static Bitmap? Load(string path, int size = 64)
    {
        try
        {
            var iid = typeof(IShellItemImageFactory).GUID;
            SHCreateItemFromParsingName(path, 0, ref iid, out var factory);
            try
            {
                if (factory.GetImage(new Size(size, size), SiigbfIconOnly | SiigbfBiggerSizeOk, out nint hbitmap) != 0)
                    return null;
                try
                {
                    return FromHBitmap(hbitmap);
                }
                finally
                {
                    DeleteObject(hbitmap);
                }
            }
            finally
            {
                Marshal.ReleaseComObject(factory);
            }
        }
        catch (Exception ex)
        {
            Log.Warn($"icon for {path} failed: {ex.Message}");
            return null;
        }
    }

    private void Worker()
    {
        foreach (string path in _queue.GetConsumingEnumerable())
            _cache[path] = Load(path);
    }

    /// <summary>Copy a (premultiplied, usually bottom-up) 32-bit DIB section into a Bitmap,
    /// keeping its alpha - Image.FromHbitmap would drop it.</summary>
    private static Bitmap? FromHBitmap(nint hbitmap)
    {
        if (GetObject(hbitmap, Marshal.SizeOf<BITMAP>(), out var bm) == 0 || bm.bmBits == 0 || bm.bmBitsPixel != 32)
            return Image.FromHbitmap(hbitmap);

        int width = bm.bmWidth, height = Math.Abs(bm.bmHeight);
        var result = new Bitmap(width, height, PixelFormat.Format32bppPArgb);
        var data = result.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.WriteOnly, PixelFormat.Format32bppPArgb);
        try
        {
            bool anyAlpha = false;
            unsafe
            {
                for (int y = 0; y < height; y++)
                {
                    int srcRow = bm.bmHeight > 0 ? height - 1 - y : y; // positive height = bottom-up
                    var src = new Span<uint>((byte*)bm.bmBits + srcRow * bm.bmWidthBytes, width);
                    var dst = new Span<uint>((byte*)data.Scan0 + y * data.Stride, width);
                    src.CopyTo(dst);
                    foreach (uint px in src) anyAlpha |= (px >> 24) != 0;
                }
                if (!anyAlpha) // no alpha channel at all: the image is opaque
                {
                    for (int y = 0; y < height; y++)
                        foreach (ref uint px in new Span<uint>((byte*)data.Scan0 + y * data.Stride, width))
                            px |= 0xFF000000;
                }
            }
        }
        finally
        {
            result.UnlockBits(data);
        }
        return result;
    }

    private const int SiigbfBiggerSizeOk = 0x1;
    private const int SiigbfIconOnly = 0x4;

    [ComImport, Guid("bcc18b79-ba16-442f-80c4-8a59c30c463b"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellItemImageFactory
    {
        [PreserveSig]
        int GetImage(Size size, int flags, out nint hbitmap);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BITMAP
    {
        public int bmType, bmWidth, bmHeight, bmWidthBytes;
        public ushort bmPlanes, bmBitsPixel;
        public nint bmBits;
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern void SHCreateItemFromParsingName(string path, nint bindContext, ref Guid iid,
        [MarshalAs(UnmanagedType.Interface)] out IShellItemImageFactory item);

    [DllImport("gdi32.dll")]
    private static extern int GetObject(nint handle, int size, out BITMAP bitmap);

    [DllImport("gdi32.dll")]
    private static extern bool DeleteObject(nint handle);
}
