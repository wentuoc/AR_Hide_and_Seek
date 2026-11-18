using System;
public static class MecchaLiveGateChecks
{
    static void Check(bool condition,string why) { if(!condition) throw new Exception(why); }
    public static void Run()
    {
        var g=new MecchaGestureGate(); long id=0;
        Check(g.Observe(++id,MecchaGestureGate.Click)==-1,"Click 1");
        Check(g.Observe(id,MecchaGestureGate.Click)==-1 && g.Count==1,"Duplicate ignored");
        g.Observe(++id,MecchaGestureGate.Click);
        Check(g.Observe(++id,MecchaGestureGate.Click)==MecchaGestureGate.Click,"First click confirms directly");
        g.Observe(++id,MecchaGestureGate.Drop);
        g.Observe(++id,MecchaGestureGate.Grab);
        g.Observe(++id,MecchaGestureGate.Drop);
        Check(g.Stable==MecchaGestureGate.Click,"Pending gesture retains click");
        Check(g.Observe(++id,MecchaGestureGate.Drop)==-1 && g.Count==2,"Mismatch deducted one");
        Check(g.Observe(++id,MecchaGestureGate.Drop)==MecchaGestureGate.Drop,"Count three confirms");
        Check(g.Observe(++id,MecchaGestureGate.Drop)==-1,"Held spawn fires once");
        Check(g.Observe(id-2,MecchaGestureGate.Grab)==-1,"Old inference ignored");
        for(int i=0;i<3;i++) g.Observe(++id,MecchaGestureGate.Grab);
        Check(g.GrabHeld,"Old votes expire");
        g.Observe(++id,MecchaGestureGate.Resting);
        Check(g.GrabHeld,"One resting vote keeps pinch");
        g.Observe(++id,-1);
        Check(g.Stable==-1 && g.Count==0,"Invalid clears window");
    }
}
