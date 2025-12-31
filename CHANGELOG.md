# Changelog

## [0.3.2] - 2025-12-31

### Fixed

* More correct light state synchronization (prevents false “OFF” updates while the light is still on).

## [0.3.1] - 2025-12-30

### Changed

* **Multi-device setup UX (autodiscovery)**: adding multiple controllers in a row now works more reliably.
* **Translations**: added UI translations for multiple languages (`da`, `de`, `es`, `fi`, `fr`, `it`, `nb`, `nl`, `pl`, `ru`, `sv`).

### Fixed

* Cases where adding the 2nd/3rd controller required manual setup.

## [0.3.0] - 2025-12-29

### Changed

* **Discovery-first identity (GUID-first)**: the integration now treats the controller GUID as the primary identity and tracks the runtime endpoint (`host:port`) similarly to the official app.
  This makes it significantly more resilient to sauna reboots, DHCP IP changes, and firmware variants where the effective control port changes between sessions.
* **Runtime endpoint tracking**:
  * Control port changes detected via announce now trigger a fresh HELLO/INIT handshake on the new port (some firmwares ignore commands until re-initialized).
  * Options flow now defaults the “port” field to the **current effective `control_port`** when available (less confusing in dynamic-port setups).
* **Docker/macOS robustness**: never switches the effective control host to loopback/unspecified addresses (prevents `effective_host=127.0.0.1` breaking connectivity in some Docker/network setups).

### Fixed

* Cases where the integration stayed “offline” after Home Assistant restart until the user manually corrected the port.

### Notes (migration)

* This release is designed to be **backwards compatible** for existing entries (no remove/re-add required).
* If you upgrade from older versions and see duplicated entities (for example `*_2`) or stale `restored/unavailable` entities, the cleanest path is still to remove and re-add the integration
  (see the migration note in `0.2.1`).

## [0.2.3] - 2025-12-29

### Added

* Runtime discovery listener on UDP **54377/54378** (shared across entries) to adapt to controllers that change their effective control port after reboot.
* Offline recovery probe: when offline, Home Assistant periodically sends a lightweight INIT probe to a small set of likely ports to re-establish telemetry even when broadcasts are not visible.

### Changed

* Config flow UX: if discovery found devices but the user selects **Manual**, the manual form is pre-filled with the discovered host/port (instead of defaulting to a placeholder port).

## [0.2.2] - 2025-12-29

### Fixed

* Restored runtime UI translations for the config flow/options flow by shipping `custom_components/tylo_sauna/translations/en.json`.
  Without `translations/*.json`, Home Assistant may display raw field keys (e.g. `allow_telemetry_from_other_ips`) instead of labels.

## [0.2.1] - 2025-12-29

### Added

* **Favorites (presets) support**:

  * Auto-refresh favorites from controller snapshots.
  * New entity: `select.tylo_sauna_favorite` to apply a preset as a “scene” (temperature + stop-after + light).
* **Door safety / fault telemetry (reverse-engineered)**:

  * Parses controller fault/events (e.g. door-open cancellation codes **19/20**).
  * Exposes diagnostic sensors:

    * `sensor.tylo_sauna_fault_code`
    * `sensor.tylo_sauna_fault_message`
  * `climate` blocks starting heating when the controller requires acknowledgement (prevents “silent fail”).
* **Diagnostics & connectivity UX**:

  * New diagnostic entity: `binary_sensor.tylo_sauna_online` (always available) with:

    * online/offline state
    * `last_seen` + `seconds_ago` (as attributes)
    * configured endpoint (host/port), effective telemetry source, learned control port, rx/tx counters
* **Options flow**:

  * Update host/port/name and “Allow telemetry from other IPs” without removing the integration.
* **Improved config flow UX**:

  * Two-step setup flow (pick device → confirm settings).
  * Better naming defaults (uses controller name from discovery when available).
  * Clearer user-facing label for relaxed telemetry: **“Allow telemetry from other IPs (recommended)”**.
  * English translations for config/option forms.

### Changed

* **Discovery now reads the controller’s advertised control port** from broadcast announces
  (fixes setups where the control port is not a fixed value).
* **Control port is learned automatically** from incoming Tylo packets (helps Docker and firmware variants).
* **Multi-device on same host** (sauna + steam) is supported in discovery and manual mode.
* **Device identity is stable across host changes** (uses config entry id for device identifiers).

### Fixed

* “Discovered but no data” cases where the integration sent keepalives/commands to the wrong UDP port.
* Reduced startup issues by avoiding long-running bootstrap-blocking loops; periodic jobs are handled safely.
* Improved offline UX: control entities become unavailable when the controller is offline; diagnostic entity remains available.

### Notes (migration)

* **Recommended upgrade path (best experience): remove and re-add the integration.**
  This release changes entity/device identifiers for stability (e.g., host/IP changes), which can cause duplicated entities (`*_2`) in the entity registry after an upgrade.
  The cleanest path is:
  1) Remove the **Tylo Sauna** integration (Devices & Services)
  2) Restart Home Assistant
  3) Add the integration again
* Alternative: manually remove old `restored/unavailable` entities from the entity registry if you prefer not to re-add.

## [0.1.1] - 2025-12-21

### Added

* Relaxed telemetry source filtering (optional):

  * Allows telemetry to be received from a different IP/node than the discovered control host.
  * Pins `telemetry_host` after the first valid telemetry packet.
  * Logs GUID mismatches when a GUID is present in the payload.
* Diagnostics:

  * `telemetry_host`, `rx_packets`, `tx_packets` exposed as climate extra attributes.
  * Additional debug logs around telemetry source filtering.

### Fixed

* Improved support for multi-node setups (e.g. sauna + steam) where telemetry may originate from a different node/IP.

## [0.1.0] - 2025-12-08

### Added

* Initial release of the Tylo Sauna integration for Home Assistant.
* Climate entity:

  * Heating on/off (`heat` / `off` HVAC modes)
  * Target & current temperature in °C
  * Attributes:

    * `stop_after_min` – configured *Stop after* timer (minutes)
    * `stop_remaining_min` – remaining countdown to auto-off (minutes)
* Light entity for sauna light (on/off).
* Number entity for *Stop after* timer configuration (minutes).
* Sensor entity for remaining time to auto-off (minutes).
* Local UDP protocol implementation (no cloud required).
* Basic UDP discovery in the config flow (same mechanism as the official app).
