using System.Collections.Generic;
using UnityEngine;
using UnityEngine.XR.ARFoundation;
using UnityEngine.XR.ARSubsystems;

public class SpawnMeccha : MonoBehaviour
{
    [Header("AR Components")]

    [SerializeField]
    private ARPlaneManager planeManager;

    [SerializeField]
    private ARRaycastManager raycastManager;

    [SerializeField]
    private Camera arCamera;

    [SerializeField]
    private ARAnchorManager anchorManager;

    [Header("Prefabs")]

    [SerializeField]
    private GameObject horizontalMecchaPrefab;

    [SerializeField]
    private GameObject verticalMecchaPrefab;

    [Header("Legacy UI Panel")]

    [Tooltip(
        "Old Meccha selection panel. " +
        "It remains disabled because Spawn directly creates " +
        "a horizontal Meccha."
    )]
    [SerializeField]
    private GameObject selectionPanel;

    [Header("Fine-Tune Controller")]

    public MecchaFineTuneController fineTuneController;

    [Header("Spawn Settings")]

    [Tooltip(
        "Minimum distance in meters required between newly spawned Mecchas."
    )]
    [SerializeField]
    private float minDistanceBetweenSpawns =
        0.5f;

    [Header("Camera Centre Targeting")]

    [Tooltip(
        "Small search radius around the centre of the phone screen. " +
        "This helps find a plane even if the exact centre pixel misses it."
    )]
    [Range(0f, 0.20f)]
    [SerializeField]
    private float centerSearchRadius =
        0.05f;

    [Header("Coordinate Display")]

    public MecchaCoordinateDisplay coordinateDisplay;

    [Header("Counter Manager")]

    public MecchaCounterManager counterManager;

    [Header("Multiplayer")]

    [SerializeField]
    private NetworkMecchaManager networkMecchaManager;

    private readonly List<GameObject> spawnedMecchas =
        new List<GameObject>();

    public bool LimitHiderSpawns { get; set; }
    public int SpawnedCount => spawnedMecchas.Count;

    private readonly List<ARRaycastHit> raycastHits =
        new List<ARRaycastHit>();

    private void Start()
    {
        if (arCamera == null)
        {
            arCamera =
                Camera.main;
        }

        if (planeManager == null)
        {
            planeManager =
                FindFirstObjectByType<
                    ARPlaneManager
                >();
        }

        if (raycastManager == null)
        {
            raycastManager =
                FindFirstObjectByType<
                    ARRaycastManager
                >();
        }

        if (anchorManager == null)
        {
            anchorManager =
                FindFirstObjectByType<
                    ARAnchorManager
                >();
        }

        // Old Horizontal / Vertical selection panel
        // is no longer used.
        if (selectionPanel != null)
        {
            selectionPanel.SetActive(
                false
            );
        }
    }

    // MAIN SPAWN BUTTON

    public void OpenSelectionPanel()
    {
        // Kept so your existing Spawn button
        // OnClick reference still works.
        SpawnHorizontal();
    }

    public void OnSpawnButtonClicked()
    {
        SpawnHorizontal();
    }

    // SPAWN HORIZONTAL MECCHA

    public void SpawnHorizontal()
    {
        TrySpawnHorizontal(out _);
    }

    public bool TrySpawnHorizontal(out string message)
    {
        return SpawnMecchaOnPlane(horizontalMecchaPrefab, Quaternion.identity, 0, out message);
    }

    // SPAWN VERTICAL MECCHA

    // Kept for backwards compatibility.
    public void SpawnVertical()
    {
        Quaternion tilt90OnX =
            Quaternion.Euler(
                90f,
                0f,
                0f
            );

        SpawnMecchaOnPlane(
            verticalMecchaPrefab,
            tilt90OnX,
            1,
            out _
        );
    }

    public bool HasSpawnedMecchas()
    {
        return spawnedMecchas.Count > 0;
    }
    
    // SPAWN MECCHA AT CAMERA-CENTRE PLANE

    private bool SpawnMecchaOnPlane(
        GameObject prefabToSpawn,
        Quaternion extraRotation,
        int mecchaType,
        out string message
    )
    {
        if (!isActiveAndEnabled)
        {
            message = "SpawnMeccha is disabled";
            return false;
        }
        if (LimitHiderSpawns && SpawnedCount >= 2)
        {
            message = "Hider limit: two Mecchas already placed";
            Debug.Log(message);
            return false;
        }

        if (
            planeManager == null ||
            raycastManager == null ||
            prefabToSpawn == null
        )
        {
            message = "[SpawnMeccha] Missing Plane Manager, " +
                "Raycast Manager or Meccha Prefab.";
            Debug.LogWarning(message);

            return false;
        }

        if (arCamera == null)
        {
            message = "[SpawnMeccha] AR Camera is missing.";
            Debug.LogWarning(message);

            return false;
        }

        if (anchorManager == null)
        {
            message = "[SpawnMeccha] AR Anchor Manager is missing.";
            Debug.LogWarning(message);

            return false;
        }

        if (!anchorManager.enabled)
        {
            message = "[SpawnMeccha] AR Anchor Manager is disabled.";
            Debug.LogWarning(message);

            return false;
        }

        // Find a tracked AR plane at or very close
        // to the centre of the phone camera.
        bool foundTarget =
            TryGetPlaneNearScreenCenter(
                out ARPlane targetPlane,
                out Pose targetPose
            );

        if (!foundTarget)
        {
            message = "[SpawnMeccha] " +
                "No detected AR plane found near " +
                "the centre of the camera.";
            Debug.LogWarning(message);

            return false;
        }

        // Close any current fine-tune session.
        if (fineTuneController != null)
        {
            fineTuneController
                .CloseFineTunePanel();
        }

        // Use the ACTUAL camera raycast hit position,
        // not the centre of the AR plane.
        Vector3 spawnPosition =
            targetPose.position;

        Quaternion baseRotation;

        // VERTICAL WALL

        if (
            targetPlane.alignment ==
            PlaneAlignment.Vertical
        )
        {
            baseRotation =
                Quaternion.LookRotation(
                    targetPlane.normal
                );
        }

        // HORIZONTAL SURFACE

        else
        {
            Vector3 lookDirection =
                arCamera.transform.position -
                spawnPosition;

            // Keep Meccha upright.
            lookDirection.y =
                0f;

            if (
                lookDirection.sqrMagnitude <
                0.0001f
            )
            {
                lookDirection =
                    arCamera.transform.forward;

                lookDirection.y =
                    0f;
            }

            if (
                lookDirection.sqrMagnitude <
                0.0001f
            )
            {
                lookDirection =
                    Vector3.forward;
            }

            baseRotation =
                Quaternion.LookRotation(
                    lookDirection.normalized
                );
        }

        Quaternion finalRotation =
            baseRotation *
            extraRotation;

        // CREATE AR ANCHOR

        GameObject anchorObject =
            new GameObject(
                "MecchaAnchor"
            );

        anchorObject.transform
            .SetPositionAndRotation(
                spawnPosition,
                finalRotation
            );

        anchorObject.transform.localScale =
            Vector3.one;

        ARAnchor newAnchor =
            anchorObject.AddComponent<
                ARAnchor
            >();

        if (newAnchor == null)
        {
            message = "[SpawnMeccha] " +
                "Failed to create ARAnchor.";
            Debug.LogWarning(message);

            Destroy(
                anchorObject
            );

            return false;
        }

        // CREATE MECCHA

        GameObject newMeccha =
            Instantiate(
                prefabToSpawn,
                anchorObject.transform
            );

        if (newMeccha == null)
        {
            message = "[SpawnMeccha] " +
                "Failed to instantiate Meccha.";
            Debug.LogWarning(message);

            Destroy(
                anchorObject
            );

            return false;
        }

        newMeccha.transform.localPosition =
            Vector3.zero;

        newMeccha.transform.localRotation =
            Quaternion.identity;

        // Preserve prefab scale.

        Debug.Log(
            "[SpawnMeccha] Meccha spawned at camera target. " +
            "Plane = " +
            targetPlane.alignment +
            " | Position = " +
            spawnPosition
        );

        // REGISTER MECCHA

        spawnedMecchas.Add(
            newMeccha
        );

        if (networkMecchaManager != null)
        {
            networkMecchaManager
                .RegisterHiderMeccha(
                    newMeccha,
                    mecchaType
                );
        }

        OnMecchaSpawned(
            newMeccha
        );

        // UPDATE COUNTER

        if (counterManager != null)
        {
            counterManager
                .IncrementCount();
        }

        // Keep legacy selection panel hidden.
        if (selectionPanel != null)
        {
            selectionPanel.SetActive(
                false
            );
        }
        message = "Meccha spawned at camera target";
        return true;
    }

    // FIND PLANE NEAR CAMERA CENTRE

    private bool TryGetPlaneNearScreenCenter(
        out ARPlane targetPlane,
        out Pose targetPose
    )
    {
        targetPlane =
            null;

        targetPose =
            default;

        if (
            raycastManager == null ||
            planeManager == null
        )
        {
            return false;
        }

        if (
            Screen.width <= 0 ||
            Screen.height <= 0
        )
        {
            return false;
        }

        Vector2 screenCenter =
            new Vector2(
                Screen.width * 0.5f,
                Screen.height * 0.5f
            );

        float searchRadiusPixels =
            Mathf.Min(
                Screen.width,
                Screen.height
            ) *
            centerSearchRadius;

        // Exact centre is checked first.
        // Nearby points are only used if the exact
        // centre does not hit a valid AR plane.
        Vector2[] samplePoints =
        {
            screenCenter,

            screenCenter +
            new Vector2(
                -searchRadiusPixels,
                0f
            ),

            screenCenter +
            new Vector2(
                searchRadiusPixels,
                0f
            ),

            screenCenter +
            new Vector2(
                0f,
                searchRadiusPixels
            ),

            screenCenter +
            new Vector2(
                0f,
                -searchRadiusPixels
            ),

            screenCenter +
            new Vector2(
                -searchRadiusPixels,
                searchRadiusPixels
            ),

            screenCenter +
            new Vector2(
                searchRadiusPixels,
                searchRadiusPixels
            ),

            screenCenter +
            new Vector2(
                -searchRadiusPixels,
                -searchRadiusPixels
            ),

            screenCenter +
            new Vector2(
                searchRadiusPixels,
                -searchRadiusPixels
            )
        };

        foreach (
            Vector2 screenPoint
            in samplePoints
        )
        {
            raycastHits.Clear();

            bool hitSomething =
                raycastManager.Raycast(
                    screenPoint,
                    raycastHits,
                    TrackableType.PlaneWithinPolygon
                );

            if (!hitSomething)
            {
                continue;
            }

            foreach (
                ARRaycastHit hit
                in raycastHits
            )
            {
                ARPlane plane =
                    planeManager.GetPlane(
                        hit.trackableId
                    );

                if (plane == null)
                {
                    continue;
                }

                if (
                    plane.trackingState !=
                    TrackingState.Tracking
                )
                {
                    continue;
                }

                bool validPlane =
                    plane.alignment ==
                        PlaneAlignment.HorizontalUp ||
                    plane.alignment ==
                        PlaneAlignment.HorizontalDown ||
                    plane.alignment ==
                        PlaneAlignment.Vertical;

                if (!validPlane)
                {
                    continue;
                }

                // Check the ACTUAL requested spawn point
                // rather than the centre of the entire plane.
                if (
                    IsSpawnLocationOccupied(
                        hit.pose.position
                    )
                )
                {
                    continue;
                }

                targetPlane =
                    plane;

                targetPose =
                    hit.pose;

                return true;
            }
        }

        return false;
    }

    // CHECK SPAWN DISTANCE

    private bool IsSpawnLocationOccupied(
        Vector3 spawnPosition
    )
    {
        foreach (
            GameObject spawned
            in spawnedMecchas
        )
        {
            if (spawned == null)
            {
                continue;
            }

            float distance =
                Vector3.Distance(
                    spawnPosition,
                    spawned.transform.position
                );

            if (
                distance <
                minDistanceBetweenSpawns
            )
            {
                return true;
            }
        }

        return false;
    }

    // ============================================================
    // DELETE MOST RECENT MECCHA
    // ============================================================

    public void DeleteMostRecentMeccha()
    {
        if (
            spawnedMecchas.Count ==
            0
        )
        {
            return;
        }

        int lastIndex =
            spawnedMecchas.Count -
            1;

        GameObject lastMeccha =
            spawnedMecchas[
                lastIndex
            ];

        // Close fine-tune panel.
        if (fineTuneController != null)
        {
            fineTuneController
                .CloseFineTunePanel();
        }

        // Remove from tracking list.
        spawnedMecchas.RemoveAt(
            lastIndex
        );

        if (counterManager != null)
        {
            counterManager
                .DecrementCount();
        }

        // Target previous Meccha.
        GameObject prevMeccha =
            spawnedMecchas.Count > 0
                ? spawnedMecchas[
                    spawnedMecchas.Count - 1
                ]
                : null;

        if (fineTuneController != null)
        {
            fineTuneController
                .SetTargetMeccha(
                    prevMeccha
                );
        }

        if (coordinateDisplay != null)
        {
            coordinateDisplay
                .SetTargetMeccha(
                    prevMeccha
                );
        }

        if (
            networkMecchaManager != null &&
            lastMeccha != null
        )
        {
            networkMecchaManager
                .RemoveHiderMeccha(
                    lastMeccha
                );
        }

        // Delete Meccha and its anchor.
        if (lastMeccha != null)
        {
            Transform parent =
                lastMeccha.transform.parent;

            if (parent != null)
            {
                ARAnchor parentAnchor =
                    parent.GetComponent<
                        ARAnchor
                    >();

                if (parentAnchor != null)
                {
                    Destroy(
                        parentAnchor.gameObject
                    );

                    Debug.Log(
                        "Deleted Meccha and its ARAnchor."
                    );
                }
                else
                {
                    Destroy(
                        lastMeccha
                    );

                    Debug.Log(
                        "Deleted Meccha without parent ARAnchor."
                    );
                }
            }
            else
            {
                Destroy(
                    lastMeccha
                );

                Debug.Log(
                    "Deleted Meccha with no parent."
                );
            }
        }
    }

    // MECCHA SPAWNED

    public void OnMecchaSpawned(
        GameObject newlySpawnedMeccha
    )
    {
        if (
            newlySpawnedMeccha == null
        )
        {
            return;
        }

        if (fineTuneController != null)
        {
            fineTuneController
                .SetTargetMeccha(
                    newlySpawnedMeccha
                );
        }

        if (coordinateDisplay != null)
        {
            coordinateDisplay
                .SetTargetMeccha(
                    newlySpawnedMeccha
                );
        }
    }
}