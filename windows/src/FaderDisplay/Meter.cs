namespace FaderDisplay;

/// <summary>
/// Peak-meter ballistics for one stereo source, same constants as the Linux plugin:
/// fast attack, slow release, 1.2 s peak hold. Peaks are fed at the polling rate
/// and consumed at the display rate.
/// </summary>
public sealed class Meter
{
    public const double ReleasePerFrame = 0.86;
    public static readonly TimeSpan HoldTime = TimeSpan.FromSeconds(1.2);

    private readonly float[] _raw = new float[2];
    private readonly double[] _level = new double[2];
    private readonly double[] _hold = new double[2];
    private readonly TimeSpan[] _holdAt = new TimeSpan[2];

    public TimeSpan LastFed { get; private set; }

    public void Feed(float left, float right, TimeSpan now)
    {
        _raw[0] = Math.Max(_raw[0], left);
        _raw[1] = Math.Max(_raw[1], right);
        LastFed = now;
    }

    /// <summary>Advance one display frame; returns bar and hold levels as column fractions.</summary>
    public (double BarL, double BarR, double HoldL, double HoldR) Frame(TimeSpan now)
    {
        for (int ch = 0; ch < 2; ch++)
        {
            double raw = _raw[ch];
            _raw[ch] = 0;
            double level = Math.Max(raw, _level[ch] * ReleasePerFrame);
            _level[ch] = level >= 1e-4 ? level : 0;
            if (raw > _hold[ch])
            {
                _hold[ch] = raw;
                _holdAt[ch] = now;
            }
            else if (now - _holdAt[ch] > HoldTime)
            {
                _hold[ch] = _level[ch];
            }
        }
        return (ToFraction(_level[0]), ToFraction(_level[1]), ToFraction(_hold[0]), ToFraction(_hold[1]));
    }

    // Windows volumes top out at 100% (no overdrive), so 0 dBFS sits at the top of the
    // column, -42 dBFS at the bottom; the zones are green below -12 dB, red above -3 dB.
    public const double FloorDb = -42;
    public static readonly double YellowFrom = ToFractionDb(-12);
    public static readonly double RedFrom = ToFractionDb(-3);

    public static double ToFraction(double amplitude) =>
        amplitude < 1e-4 ? 0 : ToFractionDb(20 * Math.Log10(amplitude));

    private static double ToFractionDb(double db) => Math.Clamp((db - FloorDb) / -FloorDb, 0, 1);
}
