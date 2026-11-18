using UnityEngine;
using UnityEngine.XR.ARFoundation;
using UnityEngine.XR.ARSubsystems;

public class PaintModeColorPaletteSpawner : MonoBehaviour
{
    [Header("AR")]

    [SerializeField]
    private ARTrackedImageManager trackedImageManager;

    [SerializeField]
    private ARAnchorManager anchorManager;

    [Header("Palette Reference Image")]

    [SerializeField]
    private string paletteImageName =
        "ColorPaletteAprilTag";

    [Header("Color Palette")]

    [SerializeField]
    private GameObject colorPalettePrefab;

    [Header("Spawn Offset")]

    [SerializeField]
    private Vector3 positionOffset =
        Vector3.zero;

    [SerializeField]
    private Vector3 rotationOffset =
        Vector3.zero;

    [Header("Debug")]

    [SerializeField]
    private bool logDebug =
        true;

    private GameObject paletteAnchorObject;

    private ARAnchor paletteAnchor;

    private GameObject spawnedPalette;

    private bool paintModeActive =
        false;

    private void Awake()
    {
        if (trackedImageManager == null)
        {
            trackedImageManager =
                FindFirstObjectByType<
                    ARTrackedImageManager
                >();
        }

        if (anchorManager == null)
        {
            anchorManager =
                FindFirstObjectByType<
                    ARAnchorManager
                >();
        }

        // Always begin with Paint Mode disabled.
        paintModeActive =
            false;
    }

    private void OnEnable()
    {
        if (trackedImageManager != null)
        {
            trackedImageManager
                .trackablesChanged
                .AddListener(
                    OnTrackedImagesChanged
                );
        }
    }

    private void OnDisable()
    {
        if (trackedImageManager != null)
        {
            trackedImageManager
                .trackablesChanged
                .RemoveListener(
                    OnTrackedImagesChanged
                );
        }

        paintModeActive =
            false;

        DestroyColorPalette();
    }

    // ============================================================
    // CALLED BY THE PAINT BUTTON
    // ============================================================

    public void TogglePaintMode()
    {
        paintModeActive =
            !paintModeActive;

        if (logDebug)
        {
            Debug.Log(
                "[ColorPaletteSpawner] Paint Mode = " +
                paintModeActive
            );
        }

        if (paintModeActive)
        {
            // If the AprilTag is already visible when
            // Paint Mode opens, allow immediate spawning.
            TrySpawnFromCurrentlyTrackedImage();
        }
        else
        {
            // Closing Paint Mode always removes
            // the palette and its AR anchor.
            DestroyColorPalette();
        }
    }

    // ============================================================
    // OPTIONAL EXPLICIT CONTROL
    // ============================================================

    public void SetPaintModeActive(
        bool active
    )
    {
        paintModeActive =
            active;

        if (paintModeActive)
        {
            TrySpawnFromCurrentlyTrackedImage();
        }
        else
        {
            DestroyColorPalette();
        }

        if (logDebug)
        {
            Debug.Log(
                "[ColorPaletteSpawner] Paint Mode set to " +
                paintModeActive
            );
        }
    }

    public bool IsPaintModeActive()
    {
        return paintModeActive;
    }

    // ============================================================
    // TRACKED IMAGE EVENTS
    // ============================================================

    private void OnTrackedImagesChanged(
        ARTrackablesChangedEventArgs<
            ARTrackedImage
        > args
    )
    {
        foreach (
            ARTrackedImage trackedImage
            in args.added
        )
        {
            HandleTrackedImage(
                trackedImage
            );
        }

        foreach (
            ARTrackedImage trackedImage
            in args.updated
        )
        {
            HandleTrackedImage(
                trackedImage
            );
        }
    }

    private void HandleTrackedImage(
        ARTrackedImage trackedImage
    )
    {
        if (trackedImage == null)
        {
            return;
        }

        // Ignore every reference image except
        // the Color Palette AprilTag.
        if (
            trackedImage.referenceImage.name !=
            paletteImageName
        )
        {
            return;
        }

        // CRITICAL:
        // Palette spawning is completely blocked
        // when Paint Mode is not active.
        if (!paintModeActive)
        {
            return;
        }

        // Only allow one palette.
        if (spawnedPalette != null)
        {
            return;
        }

        if (
            trackedImage.trackingState !=
            TrackingState.Tracking
        )
        {
            return;
        }

        SpawnColorPalette(
            trackedImage
        );
    }

    // ============================================================
    // TAG ALREADY VISIBLE WHEN PAINT MODE OPENS
    // ============================================================

    private void TrySpawnFromCurrentlyTrackedImage()
    {
        if (!paintModeActive)
        {
            return;
        }

        if (spawnedPalette != null)
        {
            return;
        }

        if (trackedImageManager == null)
        {
            return;
        }

        foreach (
            ARTrackedImage trackedImage
            in trackedImageManager.trackables
        )
        {
            if (trackedImage == null)
            {
                continue;
            }

            if (
                trackedImage.referenceImage.name !=
                paletteImageName
            )
            {
                continue;
            }

            if (
                trackedImage.trackingState !=
                TrackingState.Tracking
            )
            {
                continue;
            }

            SpawnColorPalette(
                trackedImage
            );

            return;
        }
    }

    // ============================================================
    // SPAWN COLOR PALETTE
    // ============================================================

    private void SpawnColorPalette(
        ARTrackedImage trackedImage
    )
    {
        // Extra safety check.
        if (!paintModeActive)
        {
            return;
        }

        if (spawnedPalette != null)
        {
            return;
        }

        if (trackedImage == null)
        {
            return;
        }

        if (colorPalettePrefab == null)
        {
            Debug.LogError(
                "[ColorPaletteSpawner] " +
                "Color Palette Prefab is not assigned."
            );

            return;
        }

        if (anchorManager == null)
        {
            Debug.LogError(
                "[ColorPaletteSpawner] " +
                "ARAnchorManager is not assigned."
            );

            return;
        }

        if (!anchorManager.enabled)
        {
            Debug.LogError(
                "[ColorPaletteSpawner] " +
                "ARAnchorManager is disabled."
            );

            return;
        }

        Vector3 worldPosition =
            trackedImage.transform
                .TransformPoint(
                    positionOffset
                );

        Quaternion worldRotation =
            trackedImage.transform.rotation *
            Quaternion.Euler(
                rotationOffset
            );

        // Create independent AR anchor.
        paletteAnchorObject =
            new GameObject(
                "ColorPaletteARAnchor"
            );

        paletteAnchorObject.transform
            .SetPositionAndRotation(
                worldPosition,
                worldRotation
            );

        paletteAnchorObject.transform.localScale =
            Vector3.one;

        paletteAnchor =
            paletteAnchorObject
                .AddComponent<ARAnchor>();

        if (paletteAnchor == null)
        {
            Debug.LogError(
                "[ColorPaletteSpawner] " +
                "Failed to create Color Palette ARAnchor."
            );

            Destroy(
                paletteAnchorObject
            );

            paletteAnchorObject =
                null;

            return;
        }

        spawnedPalette =
            Instantiate(
                colorPalettePrefab,
                paletteAnchor.transform
            );

        spawnedPalette.transform.localPosition =
            Vector3.zero;

        spawnedPalette.transform.localRotation =
            Quaternion.identity;

        if (logDebug)
        {
            Debug.Log(
                "[ColorPaletteSpawner] " +
                "Palette spawned successfully."
            );
        }
    }

    // ============================================================
    // DESTROY COLOR PALETTE
    // ============================================================

    public void DestroyColorPalette()
    {
        if (spawnedPalette != null)
        {
            Destroy(
                spawnedPalette
            );

            spawnedPalette =
                null;
        }

        if (paletteAnchorObject != null)
        {
            Destroy(
                paletteAnchorObject
            );

            paletteAnchorObject =
                null;
        }

        paletteAnchor =
            null;

        if (logDebug)
        {
            Debug.Log(
                "[ColorPaletteSpawner] " +
                "Palette removed."
            );
        }
    }

    public bool HasSpawnedPalette()
    {
        return
            spawnedPalette != null;
    }
}