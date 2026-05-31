# Fork by nrjphwe

# Tylo Sauna – Home Assistant Custom Integration

Local (LAN) Home Assistant integration for **Tylo Elite / Elite WiFi** controllers.

> ⚠️ **Unofficial.** This integration is based on reverse-engineering of the controller’s local UDP protocol.
> Not affiliated with Tylo / TylöHelo / Helo. Use at your own risk.

---

## Features

For each configured controller the integration creates one device with:

### Core control

- **Climate** – `climate.tylo_sauna`
  - HVAC modes: `off` / `heat_cool` (standby) / `heat`
  - Target temperature (°C)
  - Current temperature (°C)
  - Attributes:
    - `stop_after_min` – configured "Stop after" (minutes)
    - `stop_remaining_min` – remaining countdown to auto-off (minutes)
    - `door_fault_pending` – blocks starting heat when acknowledgement is required
    - `standby_enabled` – whether standby mode is configured on the controller
    - `standby_delta_c` – temperature reduction in standby mode (°C)

- **Light** – `light.tylo_sauna_light`
  - Simple on/off control

- **Number** – `number.tylo_sauna_stop_time`
  - “Stop after” minutes (0–600 by default)
  - Sends the same command sequence as the official app

- **Sensor** – `sensor.tylo_sauna_time_to_off`
  - Remaining time until auto-off (minutes)

### Favorites (presets)

- **Select** – `select.tylo_sauna_favorite`
  - Auto-updated list of controller presets (favorites)
  - Selecting a preset applies it as a “scene”:
    - target temperature
    - stop-after minutes
    - light on/off

  - (Heating start remains a separate action via the climate entity, same as the official app UX.)

### Schedule / programs (read-only)

- **Sensor** – `sensor.tylo_sauna_programs`
  - Displays scheduled programs from the Tylo calendar tab
  - Format: `09:00–12:00 Bath 88°C | 15:00–18:00 Standby FAV111`
  - Shows "No programs" when the schedule is empty
  - Attributes: `program_count`, `programs` (structured list with slot, timestamps, mode, temperature or favorite name)

### Humidity (Combi/Steam only)

- `sensor.tylo_sauna_humidity` – current humidity %
- `sensor.tylo_sauna_humidity_setpoint` – humidity setpoint %

> Humidity sensors are available for Combi and Steam setups. Saunas without humidity capability will show 0%.

### Door safety / faults (diagnostics)

- `sensor.tylo_sauna_fault_code` – last fault code (e.g. door cancellation)
- `sensor.tylo_sauna_fault_message` – last fault message
- The integration prevents “silent start failures” by blocking heat start when the controller requires acknowledgement.

### Connectivity & diagnostics (no Activity spam)

- **Binary sensor** – `binary_sensor.tylo_sauna_online` (Diagnostic)
  - Always available, even when the sauna is offline.
  - Includes diagnostic attributes:
    - `last_seen` / `seconds_ago` (as attributes, not a separate sensor)
    - configured host (user preference)
    - effective telemetry source (useful in multi-node setups)
    - effective control port (`control_port`)
    - rx/tx counters

---

## Requirements

- Tylo Elite / Elite WiFi controller reachable on the LAN
- Home Assistant and the controller must be able to exchange UDP packets

### Ports (observed)

- Discovery uses UDP **54377 / 54378** (same as the official app)
- Control/telemetry uses a **dynamic, session-specific UDP port** (chosen by the controller) and it may change after reboot.
  **This integration auto-detects the effective port from discovery/announce and learns it from incoming telemetry.**

---

## Installation

### Via HACS (recommended)

1. HACS → Integrations → Custom repositories
2. Add:
   - URL: `https://github.com/skyer/home-assistant-tylo-sauna`
   - Category: Integration

3. Install **Tylo Sauna**
4. Restart Home Assistant
5. Settings → Devices & Services → Add Integration → **Tylo Sauna**

#### Updates via HACS (important)

HACS update notifications are typically driven by **GitHub Releases/Tags**.
If you installed this as a custom repository and do not see update prompts, make sure the repository has a newer GitHub release (e.g. tag `v0.3.2`).
In HACS you can also force a refresh: open the integration in HACS → menu (⋮) → **Reload** / **Update information** (wording depends on HACS version).

### Manual

Copy `custom_components/tylo_sauna/` into your HA `config/custom_components/` and restart HA.

---

## Setup (config flow)

The integration uses a **two-step** setup:

1. **Select discovered device** (if discovery works) or choose **Manual**.
2. **Confirm settings**:
   - Name (default is the controller’s advertised name if available)
   - “Allow telemetry from other IPs (recommended)”
     Enable this if your system sends telemetry from a different node/IP (common for sauna + steam systems).
   - If discovery found devices but you chose **Manual**, the host/port fields are pre-filled from discovery to avoid guessing the control port.

### Docker note

If you run HA in Docker with bridge networking, UDP broadcast discovery may not work.
Manual setup still works; the integration can learn the effective control port from incoming packets.
Newer versions also keep a runtime discovery listener and perform lightweight offline probes to recover if the controller changes its port after reboot.
The integration also caches the last-known endpoint per controller GUID to improve recovery across restarts.

---

## Changing IP / port later

Use **Settings → Devices & Services → Tylo Sauna → Configure** (Options flow) to change:

- host/IP
- UDP port
- name
- “Allow telemetry from other IPs”

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

## Maintainers: releasing a new version

1. Bump `custom_components/tylo_sauna/manifest.json` `"version"` (use semantic versioning).
2. Update `CHANGELOG.md`.
3. Create and push a tag `vX.Y.Z` (for example `v0.3.3`).
4. GitHub Actions will publish a GitHub Release with `tylo_sauna.zip` attached.
   HACS will then detect the new version and offer an update.

---

## Troubleshooting

If the integration discovers your sauna but entities show no data or incorrect state:

1. **Enable debug recording:** Settings → Integrations → Tylo Sauna → Configure → check "Debug recording"
2. **Reproduce the issue** — wait for the problem to occur (e.g., standby transition, connection loss)
3. **Download diagnostics:** Settings → Integrations → Tylo Sauna → "..." → Download Diagnostics
4. **Attach the JSON** to your [GitHub issue](https://github.com/skyer/home-assistant-tylo-sauna/issues)

The diagnostics file includes configuration, controller state, and a buffer of the last ~2000 UDP packets (about 1.5-2 hours of traffic). IP addresses in the packet buffer are NOT redacted (needed for network debugging); top-level config fields are redacted.

### "Discovered but no data / all entities N/A"

Most common causes:

- Controller uses a **different control port** than expected (fixed via discovery parsing + port learning; some setups change ports after reboot).
- Home Assistant cannot receive UDP replies (firewall, VLAN isolation, guest Wi-Fi, etc.).
- Telemetry arrives from a different node/IP → enable “Allow telemetry from other IPs”.

Check `binary_sensor.tylo_sauna_online`:

- If `off`, inspect its attributes:
  - `effective_telemetry_host`
  - `control_port`
  - `seconds_ago`

### Enable debug logging

Add to `configuration.yaml` temporarily:

```yaml
logger:
  default: info
  logs:
    custom_components.tylo_sauna: debug
```

Restart HA and reproduce the issue.

---

## Standby mode

Standby mode is an **optional feature** configured on the physical Tylo panel (not via the app or this integration).

When enabled on the panel:

1. The controller advertises standby as available (`standby_enabled: true`)
2. The integration exposes three HVAC modes:
   - `off` – sauna is off
   - `heat_cool` – **standby mode** (reduced temperature heating)
   - `heat` – full heating
3. The temperature reduction is shown in the `standby_delta_c` attribute (e.g., 18°C means the sauna heats to `target - 18°C` in standby)

**Note:** Home Assistant's climate entity uses `heat_cool` as the closest standard mode for standby. You can customize the display name in your dashboard using a custom card or template.

### UI customization example

To display "Standby" instead of "Heat/Cool" in a Lovelace card, use a custom button or conditional card:

```yaml
type: horizontal-stack
cards:
  - type: button
    name: "Off"
    tap_action:
      action: call-service
      service: climate.set_hvac_mode
      service_data:
        entity_id: climate.tylo_sauna
        hvac_mode: "off"
  - type: button
    name: "Standby"
    tap_action:
      action: call-service
      service: climate.set_hvac_mode
      service_data:
        entity_id: climate.tylo_sauna
        hvac_mode: "heat_cool"
  - type: button
    name: "Heat"
    tap_action:
      action: call-service
      service: climate.set_hvac_mode
      service_data:
        entity_id: climate.tylo_sauna
        hvac_mode: "heat"
```

---

## Experimental: Steam Aroma (Eucalyptus)

> ⚠️ **Experimental feature** — needs testing from users with Tylo Steam controllers.

For **Tylo Steam** controllers with an aroma pump (e.g., Eucalyptus), there is an experimental option to expose ON/OFF buttons:

1. Settings → Devices & Services → **Tylo Sauna** → Configure
2. Enable **"Experimental aroma buttons"**
3. Reload the integration

This creates two button entities:

- `button.tylo_sauna_aroma_eucalyptus_on`
- `button.tylo_sauna_aroma_eucalyptus_off`

**Why experimental?** The aroma protocol was reverse-engineered from a single capture and has not been tested on real hardware. If you have a Steam controller with aroma pump, please test and report results in [GitHub Issues](https://github.com/skyer/home-assistant-tylo-sauna/issues).

---

## Helping improve this integration

This integration is developed and tested on a single Tylo Elite controller. The author does not have access to other Tylo models (different Elite variants, Steam, Sense Pure, etc.), so **expanding support to other hardware depends on community contributions**.

You don't need to write code — a diagnostic capture is enough to help reverse-engineer protocol differences. Here's how:

1. **Enable debug recording:** Settings → Integrations → Tylo Sauna → Configure → check "Debug recording"
2. **Use the feature** you'd like to see supported (e.g., change temperature, toggle a mode, set a schedule in the official Tylo app) — the integration will capture all UDP traffic in the background
3. **Download diagnostics:** Settings → Integrations → Tylo Sauna → "⋮" → Download Diagnostics
4. **Disable debug recording** (optional, but recommended — the buffer uses memory)
5. **Open a [GitHub issue](https://github.com/skyer/home-assistant-tylo-sauna/issues)** describing what you did and attach the downloaded JSON file

The diagnostics file contains a ring buffer of the last ~2000 UDP packets — enough to reverse-engineer new features and protocol variations.

---

## Notes & limitations

- Reverse engineered protocol; firmware updates may change behavior.
- This project targets Tylo Elite local LAN mode; other models may differ.

---

## Disclaimer

This project is a personal reverse-engineering effort and is not endorsed by Tylo / TylöHelo / Helo.
Saunas are high-power devices — follow manufacturer safety guidelines and local regulations.
