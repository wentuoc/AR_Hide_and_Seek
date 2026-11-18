# Windows laptop relay

The laptop gives the FireBeetle gloves and Unity phones a reachable address on the local network. It forwards their TCP connections to the Ultra96 through an SSH jump host. The Ultra96 handles glove message decryption, phone TLS and gesture inference.

## Start the live relay

1. Connect the laptop to a network reachable by the gloves and phones. Use the laptop's LAN IP in the glove firmware and phone app. The laptop also needs SSH access to the Ultra96 through the jump host.
2. Install Python 3.9 or later with the Windows `py` launcher and ensure `ssh.exe` is available. In `Start-Live.ps1`, check the jump-host username and the Ultra96 destination for your assigned board.
3. Start the [Ultra96 service](../Ultra96_Code/README.md). Open PowerShell **inside Laptop_Code** and run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\Start-Live.ps1
   ```

4. Complete SSH login in the new window. Keep the SSH and relay windows open. The relay prints `[RELAY] boards :5500; phones TLS :5503; SSH must be connected`. This confirms the laptop listeners are ready. Check the Ultra96 console for connected devices.

| Traffic | Laptop client endpoint | Laptop SSH listener | Ultra96 service |
| --- | --- | --- | --- |
| Glove messages (AES-GCM) | LAN IP, port 5500 | 127.0.0.1:15602 | 127.0.0.1:5602 |
| Phone connection (TLS) | LAN IP, port 5503 | 127.0.0.1:15603 | 127.0.0.1:5603 |
| Local calibration and status | 127.0.0.1:15604 | 127.0.0.1:15604 | 127.0.0.1:5604 |

## Calibration and demo tools

- **Live relay:** `Start-Live.ps1` launches SSH and `relay_live.py`. For browser calibration, open a second PowerShell window in this folder and run `py .\calibration_web.py`. Open the local URL it prints.
- **Keyboard demo:** `Start-PC.ps1` launches the same SSH forwards and runs `pipeline.py pc` for the keyboard-controlled demo. Both launchers use the same ports. Close the active launcher before switching modes.

If the launcher reports a port already in use, identify the old relay or tunnel using that port and close it. If both clients time out, check that SSH login finished and the board service is running. If only one client times out, check its configured laptop address and port.
