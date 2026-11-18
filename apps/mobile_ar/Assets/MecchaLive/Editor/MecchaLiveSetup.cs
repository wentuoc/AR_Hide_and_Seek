using System;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class MecchaLiveSetup
{
    static T One<T>(UnityEngine.SceneManagement.Scene scene) where T : Component
    {
        T found=null;
        foreach(T item in UnityEngine.Object.FindObjectsByType<T>(FindObjectsInactive.Include,FindObjectsSortMode.None))
        {
            if(item.gameObject.scene!=scene) continue;
            if(found!=null) throw new InvalidOperationException("Multiple "+typeof(T).Name+" in scene; keep one or bind manually");
            found=item;
        }
        if(found==null) throw new InvalidOperationException("Missing "+typeof(T).Name+"; use the working Hider dummy scene");
        return found;
    }
    [MenuItem("Meccha/Live AI/Create Hider Window5 Scene")]
    public static void CreateScene()
    {
        if(Application.isPlaying) throw new InvalidOperationException("Exit Play Mode first");
        if(!EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
        var scene=UnityEngine.SceneManagement.SceneManager.GetActiveScene();
        One<SpawnMeccha>(scene);
        if(string.IsNullOrEmpty(scene.path)) throw new InvalidOperationException("Save/open the working Hider scene first");
        if(!scene.path.EndsWith("_LiveAI_Window5.unity",StringComparison.Ordinal))
        {
            string copy=Path.ChangeExtension(scene.path,null)+"_LiveAI_Window5.unity";
            if(File.Exists(copy)) throw new InvalidOperationException("Open existing "+copy+" and rerun");
            if(!EditorSceneManager.SaveScene(scene,copy,true)) throw new IOException("Cannot copy scene");
            scene=EditorSceneManager.OpenScene(copy,OpenSceneMode.Single);
        }
        foreach(MonoBehaviour item in UnityEngine.Object.FindObjectsByType<MonoBehaviour>(FindObjectsInactive.Include,FindObjectsSortMode.None))
        {
            if(item==null || item.gameObject.scene!=scene) continue;
            string name=item.GetType().Name;
            if(name=="MecchaDummyReceiver" || name=="MecchaDummyUI" || name=="MecchaReceiver" || name=="MecchaARReceiver" ||
                name=="MecchaGestureController" || name=="MecchaLiveUI" || name=="MecchaMenuPointer")
            { Undo.RecordObject(item,"Disable legacy input"); item.enabled=false; EditorUtility.SetDirty(item); }
        }
        MecchaLiveReceiver receiver=null;
        foreach(var item in UnityEngine.Object.FindObjectsByType<MecchaLiveReceiver>(FindObjectsInactive.Include,FindObjectsSortMode.None))
        {
            if(item.gameObject.scene!=scene) continue;
            if(receiver!=null) throw new InvalidOperationException("Multiple live receivers"); receiver=item;
        }
        if(receiver==null)
        {
            var go=new GameObject("MecchaHiderLiveAI");
            Undo.RegisterCreatedObjectUndo(go,"Create Hider live AI input");
            receiver=go.AddComponent<MecchaLiveReceiver>();
        }
        var controller=receiver.GetComponent<MecchaLiveController>();
        if(controller==null) controller=receiver.gameObject.AddComponent<MecchaLiveController>();
        receiver.controller=controller;
        controller.spawner=One<SpawnMeccha>(scene);
        controller.fineTune=One<MecchaFineTuneController>(scene);
        controller.paintController=One<PaintUIController>(scene);
        controller.multiplayerRoles=One<MultiplayerRoleController>(scene);
        controller.networkState=One<NetworkGameState>(scene);
        if(controller.spawner.fineTuneController!=controller.fineTune)
            throw new InvalidOperationException("Spawner Fine Tune reference must match the controller Fine Tune reference");
        if(controller.fineTune.fineTunePanel==null) throw new InvalidOperationException("Assign existing Fine Tune panel");
        receiver.deviceId=1; receiver.laptopIPOverride="192.168.0.3"; receiver.portOverride=5503;
        Undo.RecordObject(controller.spawner.gameObject,"Enable direct spawn object");
        controller.spawner.gameObject.SetActive(true); controller.spawner.enabled=true;
        receiver.gameObject.SetActive(true); controller.enabled=receiver.enabled=true;
        EditorUtility.SetDirty(controller.spawner.gameObject); EditorUtility.SetDirty(controller.spawner);
        EditorUtility.SetDirty(receiver); EditorUtility.SetDirty(controller);
        EditorSceneManager.MarkSceneDirty(scene); EditorSceneManager.SaveScene(scene);
        Selection.activeGameObject=receiver.gameObject;
        Debug.Log("[HIDER LIVE AI] Saved "+scene.path+". Check parent objects active, actual laptop IP and Resources/phone_config.json before building.");
    }
    [MenuItem("Meccha/Live AI/Run Gesture Gate Checks")]
    public static void CheckGate() { MecchaLiveGateChecks.Run(); Debug.Log("Window5 checks passed"); }
}
