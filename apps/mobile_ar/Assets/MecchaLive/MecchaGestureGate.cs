// Counts distinct inference IDs, never repeated 20 Hz network snapshots.
public sealed class MecchaGestureGate
{
    public const int None=-1;
    public const int Back=0, Click=1, Drop=2, Grab=3, Gun=4, Paint=5, Shoot=6, Resting=7;
    public static readonly string[] Labels={"back","click","drop","grab","gun","paint","shoot","resting"};
    public int Stable { get; private set; } = None;
    public int Candidate { get; private set; } = None;
    public int Count { get; private set; }
    public bool GrabHeld => Stable==Grab;
    public const int Threshold = 3;
    private long lastEvent = -1;

    public void Reset(bool newStream = false)
    {
        Stable = Candidate = None;
        Count = 0;
        if (newStream) lastEvent = -1;
    }

    // Forget inference evidence while preserving the confirmed mode.
    public void ClearVotes()
    {
        Candidate = None;
        Count = 0;
    }

    public int Observe(long eventId, int gesture)
    {
        if (eventId <= lastEvent) return None;
        lastEvent = eventId;
        if (gesture < 0 || gesture >= Labels.Length)
        {
            Reset();
            return None;
        }

        if (Count == 0 || (Candidate == Stable && gesture != Stable))
        {
            Candidate = gesture;
            Count = 1;
        }
        else if (gesture == Candidate)
        {
            Count = System.Math.Min(Count + 1, Threshold);
        }
        else
        {
            Count--;
            if (Count == 0) Candidate = None;
        }
        if (Count < Threshold || Candidate == Stable) return None;
        Stable = Candidate;
        return Stable;
    }
}
