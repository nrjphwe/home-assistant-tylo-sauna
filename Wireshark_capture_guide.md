## Wireshark capture guide (recommended)

If the integration is discovered but shows **N/A / no values**, a short packet capture usually pinpoints the issue
(network isolation, different firmware/protocol variant, or telemetry coming from a different node/IP).

### Where to capture (important)

You must capture on a machine that can actually **see** the relevant UDP traffic:

- **Best**: capture on the **Home Assistant host** (the machine where the integration runs). This is the most reliable option.
- **Alternative**: capture on a **desktop client (Mac/PC)**, but only if that client is an **endpoint** of the UDP session.
  The easiest way to ensure this is:
  - install/open the **official Tylo Elite app** on that desktop,
  - confirm the app **discovers the sauna and shows live data**,
  - then start the Wireshark capture on the same machine.

If the desktop is *not* an endpoint (and you don’t have port mirroring / monitor mode), you may see little or no unicast traffic even though the sauna is working.

### What we want to capture

Please capture UDP traffic related to Tylo:

- Discovery: UDP **54377 / 54378**
- Control + telemetry: uses a **dynamic, session-specific UDP port** (chosen by the controller) and may change after reboot.
  If in doubt, capture all UDP traffic to/from the sauna IP and we can identify the effective port from packet source ports and payload patterns.

### Wireshark – basic steps (macOS / Windows)

1. Install Wireshark (if you don’t already have it).
2. Start Wireshark.
3. Select the **network interface** that is on the same LAN as your sauna:
   - Wi‑Fi interface if your PC/Mac is on Wi‑Fi
   - Ethernet interface if you are wired
4. Apply this **display filter**:

   ```
   udp
   ```

   (Tip: you can also use a capture filter, but the display filter is easier.)
   If you know the sauna IP, this is even better:

   ```
   ip.addr == <SAUNA_IP> && udp
   ```

5. Click **Start capturing**.
6. Reproduce the issue while capturing:

   - Open the official Tylo app (iOS/macOS/Windows)
   - Wait until it discovers the controller
   - Perform a few actions:
     - Heat ON/OFF
     - Light ON/OFF
     - Set temperature
     - Set stop time (auto-off timer)

7. Capture for ~30–60 seconds, then click **Stop**.
8. Save the capture:
   - **File → Save As…**
   - Save as `.pcapng` (default)
   - Name it something like: `tylo_capture.pcapng`

9. Attach the `.pcapng` file to the GitHub issue.

### Optional: add IP filter (if you know the sauna IP)

If you know the sauna IP (example `192.168.1.29`), you can narrow the display filter:

```
ip.addr == 192.168.1.29 && udp
```

### Notes

- Please ensure the capture is done on a device that is **on the same LAN** as the sauna controller.
- If Home Assistant runs on another machine (e.g., VM/NAS), capturing on your PC/Mac while using the official app is still useful.
- If possible, mention:
  - sauna controller model (Elite / Elite WiFi / steam, etc.)
  - firmware versions shown on the panel
  - your network topology (VLAN/guest Wi‑Fi, Docker, etc.)
