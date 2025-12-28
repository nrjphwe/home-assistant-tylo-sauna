# Tylo Sauna – Home Assistant Custom Integration

Local (LAN) Home Assistant integration for **Tylo Elite / Elite WiFi** controllers.

> ⚠️ **Unofficial.** This integration is based on reverse-engineering of the controller’s local UDP protocol.
> Not affiliated with Tylo / TylöHelo / Helo. Use at your own risk.

---

## Features

For each configured controller the integration creates one device with:

### Core control

* **Climate** – `climate.tylo_sauna`

  * HVAC modes: `off` / `heat`
  * Target temperature (°C)
  * Current temperature (°C)
  * Attributes:

    * `stop_after_min` – configured “Stop after” (minutes)
    * `stop_remaining_min` – remaining countdown to auto-off (minutes)
    * `door_fault_pending` – blocks starting heat when acknowledgement is required

* **Light** – `light.tylo_sauna_light`

  * Simple on/off control

* **Number** – `number.tylo_sauna_stop_time`

  * “Stop after” minutes (0–600 by default)
  * Sends the same command sequence as the official app

* **Sensor** – `sensor.tylo_sauna_time_to_off`

  * Remaining time until auto-off (minutes)

### Favorites (presets)

* **Select** – `select.tylo_sauna_favorite`

  * Auto-updated list of controller presets (favorites)
  * Selecting a preset applies it as a “scene”:

    * target temperature
    * stop-after minutes
    * light on/off
  * (Heating start remains a separate action via the climate entity, same as the official app UX.)

### Door safety / faults (diagnostics)

* `sensor.tylo_sauna_fault_code` – last fault code (e.g. door cancellation)
* `sensor.tylo_sauna_fault_message` – last fault message
* The integration prevents “silent start failures” by blocking heat start when the controller requires acknowledgement.

### Connectivity & diagnostics (no Activity spam)

* **Binary sensor** – `binary_sensor.tylo_sauna_online` (Diagnostic)

  * Always available, even when the sauna is offline.
  * Includes diagnostic attributes:

    * `last_seen` / `seconds_ago` (as attributes, not a separate sensor)
    * configured host/port
    * effective telemetry source (useful in multi-node setups)
    * learned control port
    * rx/tx counters

---

## Requirements

* Tylo Elite / Elite WiFi controller reachable on the LAN
* Home Assistant and the controller must be able to exchange UDP packets

### Ports (observed)

* Discovery uses UDP **54377 / 54378** (same as the official app)
* Control/telemetry port is **not always 42156**
  Some firmwares advertise a different control port via broadcast.
  **This integration auto-detects the control port from discovery and learns it from incoming telemetry.**

---

## Installation

### Via HACS (recommended)

1. HACS → Integrations → Custom repositories
2. Add:

   * URL: `https://github.com/skyer/home-assistant-tylo-sauna`
   * Category: Integration
3. Install **Tylo Sauna**
4. Restart Home Assistant
5. Settings → Devices & Services → Add Integration → **Tylo Sauna**

### Manual

Copy `custom_components/tylo_sauna/` into your HA `config/custom_components/` and restart HA.

---

## Setup (config flow)

The integration uses a **two-step** setup:

1. **Select discovered device** (if discovery works) or choose **Manual**.
2. **Confirm settings**:

   * Name (default is the controller’s advertised name if available)
   * “Allow telemetry from other IPs (recommended)”
     Enable this if your system sends telemetry from a different node/IP (common for sauna + steam systems).

### Docker note

If you run HA in Docker with bridge networking, UDP broadcast discovery may not work.
Manual setup still works; the integration can learn the effective control port from incoming packets.

---

## Changing IP / port later

Use **Settings → Devices & Services → Tylo Sauna → Configure** (Options flow) to change:

* host/IP
* UDP port
* name
* “Allow telemetry from other IPs”

No need to remove/re-add the integration.

---

## Upgrading / migration notes

Some releases may change entity/device identifiers to improve long-term stability (e.g., when the controller IP changes).
If you upgrade from an older version and see duplicated entities (for example `*_2`) or stale `restored/unavailable` entities, the best path is:

1. Remove the **Tylo Sauna** integration (Settings → Devices & Services).
2. Restart Home Assistant.
3. Add the integration again.

Alternative (advanced): manually clean up old entities in the entity registry instead of re-adding.

---

## Troubleshooting

### “Discovered but no data / all entities N/A”

Most common causes:

* Controller uses a **different control port** than expected (fixed in 0.2.1 via discovery parsing + port learning).
* Home Assistant cannot receive UDP replies (firewall, VLAN isolation, guest Wi-Fi, etc.).
* Telemetry arrives from a different node/IP → enable “Allow telemetry from other IPs”.

Check `binary_sensor.tylo_sauna_online`:

* If `off`, inspect its attributes:

  * `configured_host/port`
  * `effective_telemetry_host`
  * `control_port`
  * `seconds_ago`

### Enable debug logging

Add to `configuration.yaml` temporarily:

```yaml
logger:
  default: info
  logs:
    custom_components.tylo_sauna: debug
```

Restart HA and reproduce the issue.

### Packet capture (recommended)

Short capture helps confirm whether the controller replies and which port it uses.

**Best (capture on HA host):**

```bash
sudo tcpdump -i any -nn -s0 -w tylo_capture.pcapng 'udp and host <SAUNA_IP>'
```

Note: do **not** rely on UDP port **42156** being fixed. Some setups use a different control/telemetry port.
If you capture in Wireshark, filtering by IP is the safest starting point (e.g. `ip.addr == <SAUNA_IP> && udp`).

Attach the `.pcapng` to the GitHub issue.

---

## Notes & limitations

* Reverse engineered protocol; firmware updates may change behavior.
* This project targets Tylo Elite local LAN mode; other models may differ.

---

## Disclaimer

This project is a personal reverse-engineering effort and is not endorsed by Tylo / TylöHelo / Helo.
Saunas are high-power devices — follow manufacturer safety guidelines and local regulations.
