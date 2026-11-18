using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;
using System.Collections.Generic;
using G = MecchaGestureGate;

// Hider-only integration for the CURRENT AR controllers. No old panel or glove SDK extensions.
public class MecchaLiveController : MonoBehaviour
{
    public SpawnMeccha spawner;
    [SerializeField] private MecchaDeleteConfirmationController deleteConfirmation;
    public MecchaFineTuneController fineTune;
    public PaintUIController paintController;
    public MultiplayerRoleController multiplayerRoles;
    public NetworkGameState networkState;
    [SerializeField] private HunterGunRackManager gunRack;
    [SerializeField] private HunterGunShootController gunShoot;
    public float buttonInterval = 0.25f;
    public float movementThresholdG = 0.22f;
    public float rotationThreshold = 0.8f;
    public Vector3Int sensorAxes = new Vector3Int(0,1,2);
    public Vector3 sensorSigns = Vector3.one, moveSigns = Vector3.one;
    public bool showOverlay = true;
    public string status = "Waiting for gesture", gameStatus = "Waiting for Hider";
    public string rawGesture = "", stableGesture = "", voteStatus = "";
    readonly G gate = new G();
    string session, boot, gameKey;
    int generation=-1, epoch=-1;
    long lastSample=-1, lastTimestamp;
    float nextButton;
    bool flexFallback;
    GameObject grabbedMeccha;
    bool IsGrabbing => grabbedMeccha != null && FineOpen && fineTune.currentMeccha == grabbedMeccha;
    bool activeInput, haveSample, gameAllowed;
    Vector3 acceleration, filtered, neutral;
    bool FineOpen => fineTune!=null && fineTune.fineTunePanel!=null && fineTune.fineTunePanel.activeSelf;
    bool PaintOpen => paintController!=null && paintController.IsPaintModeActive;

    static bool Finite(float v) => !float.IsNaN(v) && !float.IsInfinity(v);
    Vector3 Map(Vector3 v) => Vector3.Scale(new Vector3(v[Mathf.Clamp(sensorAxes.x,0,2)],v[Mathf.Clamp(sensorAxes.y,0,2)],v[Mathf.Clamp(sensorAxes.z,0,2)]),sensorSigns);
    bool SyncGameState()
    {
        bool ready=networkState!=null && networkState.IsNetworkReady;
        bool hiderPhase = ready && multiplayerRoles != null && multiplayerRoles.IsHider() &&
            networkState.Phase == MultiplayerPhase.HiderHiding;
        bool allowed = hiderPhase && !networkState.HiderTimerPaused;
        string key=ready ? multiplayerRoles==null ? "Missing role controller" : multiplayerRoles.GetLocalRole()+" / "+networkState.Phase+" / paused="+networkState.HiderTimerPaused : "Waiting for Fusion";
        if(key!=gameKey || allowed!=gameAllowed)
        {
            StopInput(); gameKey=key; gameAllowed=allowed;
            status=allowed ? "Ready for gesture" : "Complete normal Hider/origin setup";
        }
        if (spawner != null) spawner.LimitHiderSpawns = hiderPhase;
        gameStatus=key;
        return allowed;
    }
    public void Accept(MecchaLivePacket p)
    {
        if(session!=p.session || generation!=p.generation || boot!=p.boot_id || epoch!=p.input_epoch)
        {
            StopInput(); gate.Reset(true);
            session=p.session; generation=p.generation; boot=p.boot_id; epoch=p.input_epoch;
        }
        bool gameplay = SyncGameState();
        if(!p.online || !p.ready || !p.fresh || p.simulated ||
            !Finite(p.ax) || !Finite(p.ay) || !Finite(p.az) || !Finite(p.gx) || !Finite(p.gy) || !Finite(p.gz))
        {
            StopInput(); gate.Observe(p.event_id,-1);
            status=string.IsNullOrEmpty(p.ai_fault) ? "Waiting for fresh calibrated sensor + AI" : p.ai_fault;
            return;
        }
        if (!gameplay)
        {
            if (!p.ai_valid) { gate.Reset(); return; }
            activeInput = true;
            if (p.sample_seq != lastSample) {
                lastSample = p.sample_seq;
            }
            int gesture = ObserveGesture(p);
            if (gesture == G.Click) SelectTarget();
            else if ((gesture == G.Gun || gesture == G.Shoot) &&
                     networkState != null && networkState.IsNetworkReady &&
                     networkState.Phase == MultiplayerPhase.SeekerSearching &&
                     multiplayerRoles != null && multiplayerRoles.IsSeeker())
            {
                if (gesture == G.Gun)
                {
                    if (gunRack == null || !gunRack.isActiveAndEnabled)
                        status = "Assign and enable the Seeker gun rack";
                    else if (gunRack.HasSpawnedGunRack())
                        status = "Gun rack already placed";
                    else
                    {
                        status = gunRack.TryPressSpawnGunButton()
                            ? "Spawn Gun button pressed"
                            : "Spawn Gun button unavailable";
                    }
                }
                else if (gunShoot != null && gunShoot.isActiveAndEnabled)
                {
                    gunShoot.Shoot();
                }
                else status = "Assign and enable the Seeker shoot controller";
            }
            return;
        }
        activeInput=true; rawGesture=p.gesture;
        acceleration=Map(new Vector3(p.ax,p.ay,p.az));
        Vector3 gyro=Map(new Vector3(p.gx,p.gy,p.gz));
        bool newSample=p.sample_seq!=lastSample;
        if(newSample)
        {
            float dt=((p.ts_ms-lastTimestamp)&0xffffffffL)*0.001f;
            if(!haveSample) { filtered=acceleration; haveSample=true; }
            else filtered=Vector3.Lerp(filtered,acceleration,1-Mathf.Exp(-Mathf.Max(0f,dt)/0.12f));
            lastSample=p.sample_seq; lastTimestamp=p.ts_ms;
        }
        if (!p.ai_valid)
        {
            bool canContinue = (p.flex_invalid || flexFallback) && string.IsNullOrEmpty(p.ai_fault) &&
                IsGrabbing;
            if (!canContinue)
            {
                StopInput();
                status = "AI unavailable";
                return;
            }
            if (!flexFallback) gate.ClearVotes();
            flexFallback = true;
            rawGesture = "flex invalid";
            voteStatus = "waiting for valid inference";
            if (newSample) Move(gyro);
            status = "IMU fine tune continues; flex invalid";
            return;
        }
        if (flexFallback)
        {
            gate.ClearVotes();
            flexFallback = false;
        }
        int edge=ObserveGesture(p);
        if(edge>=0) HandleGesture(edge);
        // Use confirmed gesture: a single raw mismatch does not flicker modes.
        if(newSample && IsGrabbing) Move(gyro);
    }
    void SetPaint(bool open)
    {
        if(paintController!=null && paintController.IsPaintModeActive!=open) paintController.TogglePaintMode();
    }
    void CloseFine()
    {
        if(FineOpen) fineTune.CloseFineTunePanel();
    }
    void HandleGesture(int id)
    {
        if(id==G.Resting) return;
        if(id==G.Back)
        {
            if (PaintOpen)
            {
                SetPaint(false);
                status = "Back: paint OFF";
            }
            else if (spawner != null && spawner.SpawnedCount > 0)
            {
                if (deleteConfirmation == null)
                    deleteConfirmation = FindFirstObjectByType<MecchaDeleteConfirmationController>(FindObjectsInactive.Include);

                if (deleteConfirmation == null)
                {
                    status = "Back: assign Delete Confirmation";
                    return;
                }

                deleteConfirmation.OpenDeleteConfirmation();
                status = "Back: requested remove confirmation";
            }
            else status = "Back: no Meccha to delete";
            return;
        }
        if(id==G.Click) { SelectTarget(); return; }
        if(id==G.Drop)
        {
            if (IsGrabbing)
            {
                grabbedMeccha = null;
                CloseFine();
                status = "Drop: released Meccha";
                return;
            }
            if (spawner == null) { status = "Spawn blocked: assign spawner"; return; }
            spawner.TrySpawnHorizontal(out status);
        }
        else if(id==G.Grab)
        {
            if(fineTune==null || !fineTune.isActiveAndEnabled || fineTune.currentMeccha==null || fineTune.fineTunePanel==null)
            { status="Grab blocked: spawn first; check fine tune controller"; return; }
            if (IsGrabbing) return;
            neutral=filtered; nextButton=Time.realtimeSinceStartup+Mathf.Max(0.1f,buttonInterval);
            if(!FineOpen) fineTune.OpenFineTunePanel();
            grabbedMeccha = FineOpen ? fineTune.currentMeccha : null;
            status="Fine tune: wrist tilt/motion moves; gyro rotates";
        }
        else if(id==G.Paint)
        {
            if(paintController==null || !paintController.isActiveAndEnabled || fineTune==null || fineTune.currentMeccha==null)
            { status="Paint blocked: spawn first; check paint controller"; return; }
            SetPaint(true); status="Paint ON: use current AR tracked brush/palette";
        }
        else status="Hider: "+G.Labels[id]+" has no action in this build";
        Debug.Log("[HIDER AI] "+G.Labels[id]+": "+status);
    }
    int ObserveGesture(MecchaLivePacket p)
    {
        rawGesture=p.gesture;
        int edge=gate.Observe(p.event_id,p.class_id);
        stableGesture=gate.Stable<0 ? "none" : G.Labels[gate.Stable];
        voteStatus=(gate.Candidate<0 ? "none" : G.Labels[gate.Candidate])+" "+gate.Count+"/3";
        return edge;
    }
    void SelectTarget()
    {
        var camera = Camera.main;
        var system = EventSystem.current;
        if (system != null)
        {
            var pointer = new PointerEventData(system);
            pointer.position = camera != null ? (Vector2)camera.pixelRect.center :
                new Vector2(Screen.width / 2f, Screen.height / 2f);
            var hits = new List<RaycastResult>();
            system.RaycastAll(pointer, hits);
            if (hits.Count > 0)
            {
                var button = hits[0].gameObject.GetComponentInParent<Button>();
                if (button != null && button.isActiveAndEnabled && button.IsInteractable())
                {
                    status = "Selected " + button.name;
                    button.onClick.Invoke();
                    return;
                }
                if (hits[0].module is GraphicRaycaster) { status = "Aim at a button"; return; }
            }
        }
        status = "Aim at an enabled UI button";
    }
    void Move(Vector3 gyro)
    {
        if(fineTune==null || !fineTune.isActiveAndEnabled || fineTune.currentMeccha==null || Time.realtimeSinceStartup<nextButton) return;
        string command=null;
        if(Mathf.Max(Mathf.Abs(gyro.x),Mathf.Abs(gyro.y))>=Mathf.Max(0.05f,rotationThreshold))
            command=Mathf.Abs(gyro.y)>=Mathf.Abs(gyro.x) ? "rotate_h" : "rotate_v";
        else
        {
            Vector3 delta=Vector3.Scale((filtered-neutral)/9.80665f,moveSigns);
            int axis=Mathf.Abs(delta.x)>=Mathf.Abs(delta.y) ? 0 : 1;
            if(Mathf.Abs(delta.z)>Mathf.Abs(delta[axis])) axis=2;
            if(Mathf.Abs(delta[axis])>=Mathf.Max(0.02f,movementThresholdG))
                command=axis==0 ? delta.x>0 ? "right" : "left" : axis==1 ? delta.y>0 ? "up" : "down" : delta.z>0 ? "front" : "back";
        }
        if(command==null) return;
        nextButton=Time.realtimeSinceStartup+Mathf.Max(0.1f,buttonInterval);
        switch(command)
        {
            case "up": fineTune.MoveUp(); break; case "down": fineTune.MoveDown(); break;
            case "left": fineTune.MoveLeft(); break; case "right": fineTune.MoveRight(); break;
            case "front": fineTune.MoveFront(); break; case "back": fineTune.MoveBack(); break;
            case "rotate_h": fineTune.RotateHorizontal(); break; case "rotate_v": fineTune.RotateVertical(); break;
        }
        status="Fine tune: "+command;
    }
    public void Recenter()
    { neutral=filtered; nextButton=Time.realtimeSinceStartup+Mathf.Max(0.1f,buttonInterval); status="IMU neutral reset"; }
    public void StopInput()
    {
        bool wasActive=activeInput || gate.Stable != G.None;
        if(wasActive) CloseFine();
        grabbedMeccha = null;
        gate.Reset(); flexFallback=false; activeInput=false; haveSample=false; lastSample=-1;
        rawGesture=""; stableGesture="none"; voteStatus="none 0/3";
        status="Input stopped";
    }
    void Update()
    {
        SyncGameState();
    }
    void OnApplicationPause(bool paused) { if(paused) StopInput(); }
    void OnDisable()
    {
        StopInput();
        if (spawner != null) spawner.LimitHiderSpawns = false;
    }
    void OnGUI()
    {
        if(!showOverlay) return;
        var receiver=GetComponent<MecchaLiveReceiver>();
        var style=new GUIStyle(GUI.skin.box) { fontSize=Mathf.Max(16,Screen.width/85),wordWrap=true };
        Rect safe = Screen.safeArea;
        float margin = Mathf.Clamp(safe.width * 0.012f, 6f, 24f);
        float top = Screen.height - safe.yMax + margin;
        float right = safe.xMax - margin;
        float left = safe.xMin + margin;
        float width = Mathf.Min(safe.width * 0.42f, safe.height * 0.48f);
        float height = width * 0.34f;
        var panel = new Rect(right - width, top, width, height);
        Color previousColor = GUI.color;
        GUI.color = new Color(0.04f, 0.05f, 0.08f, 0.96f);
        GUI.DrawTexture(panel, Texture2D.whiteTexture);
        GUI.color = Color.white;
        var label = new GUIStyle(GUI.skin.label) {
            alignment = TextAnchor.MiddleCenter, fontStyle = FontStyle.Bold,
            fontSize = Mathf.Max(1, Mathf.RoundToInt(height * 0.21f)),
            padding = new RectOffset()
        };
        label.normal.textColor = Color.white;
        GUI.Label(new Rect(panel.x, top, width, height * 0.36f), "CURRENT GESTURE", label);
        label.fontSize = Mathf.Max(1, Mathf.RoundToInt(height * 0.45f));
        string current = gate.Stable == G.None ? "WAITING" : G.Labels[gate.Stable].ToUpperInvariant();
        GUI.Label(new Rect(panel.x, top + height * 0.32f, width, height * 0.68f), current, label);
        GUI.color = previousColor;
        GUI.Box(new Rect(left,top,panel.x-left-margin,135),"REAL AI / "+gameStatus+"\n"+(receiver!=null ? receiver.status : "")+
            "\nRaw: "+rawGesture+" | Confirmed: "+stableGesture+" | Count: "+voteStatus+"\n"+status,style);
        if(GUI.Button(new Rect(Screen.width-220,Screen.height-65,205,50),"Recenter glove")) Recenter();
    }
}
