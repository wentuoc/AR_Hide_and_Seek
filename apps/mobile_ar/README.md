# AR Hide-and-Seek Visualiser

The visualiser is the Unity AR application for the CG4002 AR Hide-and-Seek game. It runs on two mobile devices and gives each player a different role:

- **Hider:** places, fine-tunes, paints and locks Mecchas in the physical environment.
- **Seeker:** scans the shared starting marker, views the synchronised Mecchas and searches for them using the hand-controlled gun system.

The two phones communicate through **Photon Fusion 2 Shared Mode**. AR Foundation and the device AR platform maintain local camera tracking, while the `InitialAprilTag` and `SharedAROriginManager` provide a common physical reference for both devices.

## Features

- Plane-based AR Meccha placement using a centre-screen raycast.
- Meccha translation, rotation, anchoring and position locking.
- Touch-based Meccha painting with palette and brush controls.
- Two-player Hider and Seeker gameplay on separate phones.
- Photon Fusion synchronisation of roles, phases, timers, Meccha records, positions, found states, counts and painted textures.
- InitialAprilTag-based shared AR origin.
- MediaPipe hand landmark tracking and wearable-glove gesture input.
- Hand-controlled Seeker gun, crosshair, ammunition, hit detection and muzzle flash.
- Role-specific UI for waiting, connection, hiding, phase transition and gameplay states.
- Final monoscopic AR view for a more stable and comfortable experience.

## Gameplay flow

1. Both phones open the main menu and select a role.
2. Each phone displays a waiting message until the other player joins the Photon session.
3. A connection confirmation panel is shown briefly before gameplay begins.
4. The Hider scans the `InitialAprilTag` and receives the hiding controls.
5. During the 120-second hiding phase, the Hider can spawn, fine-tune, paint, delete and lock Mecchas.
6. The Seeker sees a hiding panel with the remaining hiding time.
7. When hiding ends, a 20-second transition panel instructs the players to switch roles or prepare for the next phase.
8. The Seeker scans the same physical `InitialAprilTag` so that the shared AR origin is established on the second phone.
9. The Seeker gameplay begins. Meccha positions, counts, identities and painted textures are reconstructed from the Hider's final game state.
10. The Seeker searches for the Mecchas and uses the gun to reveal them. Found Mecchas are shown with the networked reveal visual, and the remaining Meccha counter is updated.

## Why the InitialAprilTag is required

Raw AR coordinates cannot be sent directly between phones. Each phone starts its AR session with a different local origin based on its camera position and orientation, so the same physical location can have different coordinate values on each device.

Both phones therefore scan the same `InitialAprilTag` in the same physical location. `SharedAROriginManager` uses the marker to establish a matching reference frame, and Meccha poses are stored or transmitted relative to this shared origin. The Seeker can then place the networked Mecchas at the corresponding physical locations instead of using unrelated raw phone coordinates.

The tag should remain flat and stationary between scans. Good lighting, a clear view of the full tag and visible environmental features improve tracking and relocalisation.

## System architecture

```text
Physical environment
        |
        v
AR Foundation / ARCore or ARKit
        |
        +--> InitialAprilTag --> SharedAROriginManager
        |
        +--> Centre-screen raycast --> Meccha spawn and anchors
        |
        +--> Hider visualiser
        |       +--> Fine-tuning
        |       +--> Painting
        |       +--> Locking
        |
        +--> Seeker visualiser
                +--> MediaPipe hand tracking
                +--> Gun and crosshair
                +--> Meccha detection and reveal

Photon Fusion 2 Shared Mode
        |
        +--> Roles, phases, timers and counters
        +--> Meccha identities and shared-origin poses
        +--> Painted textures and found states
```

## Main scripts and responsibilities

| Component or script | Responsibility |
|---|---|
| `NetworkGameState` and `MultiplayerRoleController` | Assign roles and synchronise phases, timers, Meccha records, painted-texture status, counters and final results. |
| `NetworkRunner`, `NetworkSceneManager` and `NetworkBootstrap` | Start or join the Photon Fusion session and synchronise the network scene. The demonstration session name is `CG4002_AR_GAME`. |
| `SharedAROriginManager` | Creates or recalibrates the common origin from the `InitialAprilTag`. |
| `DirectSpawnMeccha` | Raycasts from the centre of the camera view, places a Meccha on the nearest plane under the crosshair and selects the new target. |
| `MecchaFineTuneController` | Moves and rotates the selected Meccha. When fine-tuning ends, the final pose is used to create a new anchor. |
| `MecchaLockManager` | Locks the final Meccha placement, displays its coordinates and saves the position data. |
| `PaintModeColorPaletteSpawner` | Creates or enables the colour palette while Paint Mode is active. |
| `MecchaTexturePainter` | Applies brush strokes to the Meccha's skinned mesh texture. The configured painting texture is 1024 × 1024. |
| `NetworkMecchaIdentity` | Gives every spawned Meccha a unique identity so that multiple Mecchas can be tracked independently. |
| `NetworkMecchaManager` and `NetworkMecchaTextureManager` | Convert Meccha poses to shared-origin coordinates and transfer the final location and painted data to the Seeker. |
| `NetworkMecchaFoundVisual` | Displays a networked visual effect when a Meccha is found. |
| `MediaPipeHandManager` and Hand Tracking Canvas Overlay | Detect hand landmarks from the AR camera and display the hand-tracking overlay. |
| `HunterGunManager` and gun hand controllers | Manage gun selection, hand attachment and gun positioning. |
| `HunterCrosshairController` | Positions and smoothly moves the crosshair within the AR camera view. |
| `HunterGunShootController` | Handles shooting, ammunition, cooldowns, hit detection, muzzle flash and score updates. |
| `MultiplayerLocalGameplayController` | Activates the correct local Hider or Seeker systems according to the network role and current phase. |
| Multiplayer UI controllers | Display role-specific waiting, connection, timer, transition and gameplay panels. |

## Requirements

### Software

- Windows development machine.
- Unity **6.3 LTS (6000.3.22f1)** or the version used by the project.
- Android Build Support and/or iOS Build Support installed through Unity Hub.
- AR Foundation.
- XR Plugin Management.
- Google ARCore XR Plugin for Android builds.
- Apple ARKit XR Plugin for iPhone builds.
- Photon Fusion 2 Unity SDK.
- TextMeshPro.
- The MediaPipe hand-landmarker integration and the project's `hand_landmarker` model asset.
- Xcode on macOS for the final iPhone build.

### Hardware

- Two AR-capable Android phones or iPhones.
- A physical InitialAprilTag printed at a suitable size.
- The wearable gloves and their configured communication hardware for gesture input.
- A well-lit environment with textured surfaces for reliable AR tracking.

## Project setup

### 1. Open the Unity project

1. Open Unity Hub.
2. Select the project folder containing `Assets`, `Packages` and `ProjectSettings`.
3. Open it with the project's Unity version.
4. Allow Unity to finish importing packages and compiling scripts before opening the gameplay scene.

The final visualiser should use the scene containing the AR camera, Hider and Seeker panels, network objects and hand-tracking objects. In the current development scene, these objects include `XR Origin (AR Rig)`, `MainMenuManager`, `ARManager(Spawn)`, `FineTuneManager`, `CoordinateManager`, `PaintUIController`, `HunterGunManager`, `HunterGamePanel` and `MultiplayerNetwork`.

### 2. Install or import dependencies

Use **Window > Package Manager** to confirm that AR Foundation, XR Plugin Management, TextMeshPro and the correct device XR plugin are installed. Import Photon Fusion 2 through the package supplied with the project. Import the MediaPipe hand-tracking package and place the `hand_landmarker` model in the location expected by `ARMediaPipeHandTracker`.

Do not mix incompatible versions of the AR, MediaPipe or Photon packages. If Unity reports missing scripts after importing a package, resolve package version errors before editing scene references.

### 3. Configure Photon Fusion

1. Create or open the Photon application used by the project.
2. Copy the Photon App ID into the Fusion project settings or the network bootstrap configuration.
3. Add a `NetworkRunner` to the network object.
4. Add `NetworkSceneManagerDefault` and ensure the gameplay scene is included in the build settings.
5. Add `NetworkBootstrap` and set the session name to:

   ```text
   CG4002_AR_GAME
   ```

6. Ensure both phones use the same Photon App ID, session name and region settings.
7. Do not commit private credentials or local build secrets to the repository.

Photon Fusion Shared Mode is used for the two-phone demonstration. One device creates or joins the session and the other device joins the same session before role assignment is completed.

### 4. Configure AR Foundation

1. Add an `AR Session` and `XR Origin (AR Rig)` to the scene.
2. Attach an `AR Camera Manager` and `AR Camera Background` to the AR camera.
3. Add the required `AR Raycast Manager`, `AR Plane Manager` and anchor support.
4. Assign the AR camera and plane manager references in the spawn and origin scripts.
5. Use the ARCore provider for Android or the ARKit provider for iPhone.
6. Confirm that camera permission is enabled for the target platform.

The Hider requires plane detection for Meccha placement. Plane visualisation and unnecessary plane processing should be disabled for the Seeker once the shared origin and Meccha data are ready.

### 5. Configure the InitialAprilTag

1. Print the exact AprilTag used by the project without stretching it.
2. Place it flat in the play area and keep it stationary.
3. Assign the tag or its detector reference to `SharedAROriginManager`.
4. Connect the manager to the AR camera and the relevant image or marker tracking component.
5. Test scanning on the Hider phone first.
6. Keep the marker in the same physical location for the Seeker phone.
7. Scan the marker on the Seeker phone when the phase transition requests it.

The marker is a shared starting reference, not a replacement for AR tracking. After the scan, AR Foundation continues to track the camera and anchored content.

### 6. Configure Hider systems

Assign the following references in the Hider objects and panels:

- Horizontal and vertical Meccha prefabs.
- `ARPlaneManager` and `ARRaycastManager`.
- `DirectSpawnMeccha`.
- `MecchaFineTuneController`.
- `MecchaLockManager`.
- `MecchaTexturePainter` and the target skinned mesh renderer.
- `PaintModeColorPaletteSpawner` and palette target.
- Meccha counter and coordinate text.
- Hider timer ring, hiding panel and transition panel.

The final Hider interaction uses a centre-crosshair raycast for initial placement. Fine-tuning is used for small translation and rotation corrections, and the lock action preserves the final pose. Painting is touch-based in the final build because the experimental glove-IMU cursor was too sensitive and introduced synchronisation and jitter problems.

### 7. Configure Seeker systems

Assign the following references in the Seeker objects and panels:

- `MediaPipeHandManager` and `ARMediaPipeHandTracker`.
- The AR camera and camera manager.
- The `hand_landmarker` model.
- Hand landmark visualiser and hand-tracking canvas.
- Gun rack, gun prefab and gun hand controllers.
- `HunterCrosshairController`.
- `HunterGunShootController`.
- Shoot button, ammunition text and finished-ammunition panel.
- Meccha remaining text and found visual.
- Score controller and reveal-phase controller.

The Seeker uses a monoscopic AR view. A stereoscopic prototype was removed because quick movement caused tracking and anchoring problems, virtual objects drifted against the camera background and the dynamic crosshair was difficult to align between the two eyes.

## Hand-tracking and depth settings

The MediaPipe configuration used for the development build balances tracking quality with phone performance:

| Setting | Value |
|---|---:|
| Detection FPS | 15 |
| Processing width | 640 |
| Processing height | 480 |
| Hand depth | 0.5 |
| Depth sensitivity | 1 |
| Minimum depth | 0.25 m |
| Maximum depth | 2 m |
| Minimum gun scale | 0.65 |
| Maximum gun scale | 1.5 |
| Scale smooth speed | 12 |
| Position smooth speed | 20 |
| Grip offset | `(0, -0.05, 0)` |

Depth estimation and automatic muzzle alignment should remain enabled for the Seeker gun. If the hand landmarks jitter, first check lighting and camera framing before increasing the detection resolution, as higher resolution increases processing cost.

## Running the two-phone demonstration

1. Connect both phones to a network with Internet access.
2. Install the same build on both phones.
3. Start the application on both devices.
4. Select **Hider** on one phone and **Seeker** on the other.
5. Wait for the connection confirmation on both screens.
6. Place and scan the InitialAprilTag using the Hider phone.
7. During the hiding phase, spawn and customise the Mecchas.
8. Keep the Mecchas locked until the hiding timer ends.
9. When prompted, scan the same InitialAprilTag using the Seeker phone.
10. Confirm that the Seeker displays the correct Meccha count, positions and painted appearance.
11. Use the Seeker gun and crosshair to find the Mecchas.
12. Confirm that a found Meccha is revealed and that the remaining counter updates on the Seeker phone.

## Building for Android

1. Open **File > Build Profiles** or **File > Build Settings**.
2. Select Android and switch the platform if required.
3. Enable ARCore support through XR Plug-in Management.
4. Use an ARM64-compatible build configuration.
5. Confirm camera permission and the correct application identifier.
6. Connect the phone with USB debugging enabled, or build an APK and install it manually.
7. Test on a physical ARCore-supported device rather than only in the Unity editor.

## Building for iPhone

1. Select iOS in Unity Build Profiles or Build Settings.
2. Enable the ARKit provider through XR Plug-in Management.
3. Enable camera usage permission and provide a suitable camera usage description.
4. Use IL2CPP and ARM64 for the device build.
5. Export the Xcode project.
6. Open the project in Xcode, select the correct signing team and provisioning profile, then build to a physical iPhone.
7. Recheck MediaPipe, camera permissions and network access after the platform switch. Android and iOS builds can expose different plugin and camera-initialisation issues.

## Troubleshooting

### The two phones show different Meccha locations

Check that both phones scanned the same physical InitialAprilTag and that the marker was not moved between scans. Do not transmit raw AR world coordinates directly. Confirm that `SharedAROriginManager` is active and that Meccha positions are converted relative to the shared origin.

### The Seeker cannot join the session

Check the Photon App ID, session name, region, Internet connection and firewall settings. Both devices must run compatible builds and use the same session name, `CG4002_AR_GAME`.

### The Meccha spawns in an unexpected location

Confirm that the Hider's centre crosshair is over a detected plane and that the `ARRaycastManager` is assigned. The current spawn logic uses the nearest plane hit by the centre-screen raycast, not an arbitrary nearest plane in the scene.

### Hand tracking is delayed or jittery

Improve lighting, keep the hand inside the camera view and avoid rapid movement. Confirm the 15 FPS and 640 × 480 processing settings, then check the landmark confidence thresholds and smoothing value.

### The gun does not align with the crosshair

Confirm that depth estimation, automatic muzzle alignment and the gun hand controller are enabled. Check the hand depth, grip offset and smoothing values in the Seeker gun configuration.

### Painted Mecchas appear with the wrong texture

Confirm that each Meccha has a unique `NetworkMecchaIdentity`, that the texture manager is linked to the correct target renderer and that the final painted data is associated with the same Meccha identity as the synchronised pose.

## Known limitations

- Shared-origin accuracy depends on a clear InitialAprilTag scan and stable AR tracking.
- Poor lighting, motion blur and low-texture surfaces can reduce tracking quality.
- MediaPipe hand tracking consumes additional processing resources on mobile devices.
- Network delays can affect the timing of state and texture updates.
- The final build uses a monoscopic view because stereoscopic rendering caused drift, anchoring instability and crosshair alignment problems.
- The final painting interaction is touch-based; the glove-IMU dynamic paint cursor remains an experimental feature because its gyroscope sensitivity and synchronisation were not reliable enough.

## Future expansion

Possible extensions include an object-detection model for the Seeker, a better calibrated AR paintbrush, physical Meccha models linked to their AR counterparts and a system that transfers paint applied to the physical model into the game. A simplified version could also be tested with senior activity centres and community organisations in Singapore to support creative, social and cognitive engagement through cooperative AR play.

## License and project information

This visualiser was developed as part of the CG4002 capstone project. Refer to the repository's `LICENSE` file for the project licence and the other component README files for hardware, communications and AI setup information.
