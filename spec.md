## Tylo Sauna (Home Assistant integration) — Specification

This document is a single, practical spec for the `tylo_sauna` custom integration.
It describes intended behavior (UX) and the implementation contract (entities, networking, diagnostics).

---

### Goals

- Provide **local-only** Home Assistant integration for **Tylo Elite / Elite WiFi** (reverse-engineered UDP protocol).
- Provide reliable control and state sync even in “non-ideal” networks (Docker, VLANs, multi-node sauna+steam).
- Avoid UI/log spam: keep diagnostics in diagnostic entities/attributes.

---

### Architecture overview

- **Controller**: UDP client+listener using `asyncio.DatagramProtocol`.
  - Sends init sequence and keepalives.
  - Receives telemetry asynchronously and updates internal state.
  - Notifies entities via callbacks.
- **Discovery**:
  - Config flow listens on discovery ports for a short period.
  - Runtime listener (shared) can listen continuously for port changes (when possible).
- **Diagnostics**:
  - One diagnostic online binary_sensor holds network/telemetry details.

---

### Networking and ports

- Discovery/announce ports: UDP **54377 / 54378**.
- Control/telemetry port: **dynamic / session-specific** (chosen by the controller) and may change after reboot.
  - The integration must not assume a fixed control port or a stable value across sessions.

#### Identity (GUID) and endpoint caching (discovery-first)

- **GUID** (UUID) is treated as the primary device identity (stable across sessions).
- `host:port` is treated as a **runtime endpoint** that may change after controller reboot (DHCP + port changes).
- The integration maintains a persistent **GUID → last-known endpoint** cache to survive restarts and temporary loss of discovery traffic.
- User-provided `host:port` remains available as a **soft preference** (fallback), but the integration may switch to a newer endpoint when it observes valid announce/telemetry for the same GUID.

#### Control port learning (effective control port)

The integration maintains two concepts:
- **control_port**: effective runtime port used for keepalive/commands.

Learning sources:
- **Announce/broadcast payload** may advertise a port (protobuf field); use it as an initial guess.
- **Incoming Tylo packets**: learn from `src_port` for packets that look like valid Tylo telemetry/config/events.
  - If controller GUID is known, require GUID match before accepting a port update.

#### Runtime recovery (when the controller changes ports after reboot)

To avoid “online disconnected / state unknown” after controller reboot:
- Keep a **shared runtime discovery listener** on UDP 54377/54378 (single bind per HA instance).
- When offline, periodically send a lightweight **INIT probe** to a small set of likely ports
  (last learned control_port, discovery ports, and a small set of legacy observed candidates).
- When a port change is detected, optionally send an immediate INIT to the new port to accelerate recovery.

---

### Entities

#### Core control entities

1) `climate.tylo_sauna`
- HVAC modes: `heat`, `off`
- target temperature, current temperature
- Attributes (minimal):
  - `stop_after_min`
  - `stop_remaining_min`
  - `door_fault_pending`
- Must not expose verbose network diagnostics.

2) `light.tylo_sauna_light`
- Simple on/off

3) `number.tylo_sauna_stop_time`
- Configure stop-after minutes (default range 0..600)

4) `sensor.tylo_sauna_time_to_off`
- Remaining minutes until auto-off

#### Favorites/presets

5) `select.tylo_sauna_favorite`
- Options: enabled favorites (slot + name)
- Selecting applies the favorite as a “scene” (temp + stop-after + light)
- **Must not implicitly start heating** (heating is controlled via the climate entity)

#### Diagnostics

6) `binary_sensor.tylo_sauna_online` (Diagnostic)
- Must always be `available=True`
- `is_on`: online status derived from last received telemetry timestamp
- Attributes (diagnostic payload):
  - `guid`
  - `effective_host`
  - `control_port`
  - `endpoint_source` (announce/telemetry/cache/config/manual)
  - `relaxed_telemetry`
  - `last_seen`, `seconds_ago`
  - `last_rx_ip`, `last_rx_port`
  - `pinned_telemetry_host`, `effective_telemetry_host`
  - `rx_packets`, `tx_packets`

7) `sensor.tylo_sauna_fault_code` (Diagnostic, always available)
8) `sensor.tylo_sauna_fault_message` (Diagnostic, always available)

---

### UX rules

- **Offline behavior**:
  - Control entities should become unavailable when the controller is offline.
  - The diagnostic online entity remains available and contains “what we know” (last_seen, configured endpoint).
- **Avoid Activity log spam**:
  - Prefer diagnostic attributes over separate “last_seen” sensors.
- **Relaxed telemetry**:
  - Some setups send telemetry from a different IP than the configured host.
  - When enabled, accept telemetry from another IP after validating packet structure (and GUID when known),
    and pin `telemetry_host` for subsequent filtering.

---

### Door safety / faults

- Fault/event packets include `code` and `state`.
- For door cancel codes (19/20):
  - `state=13` means “pending acknowledgement” → set `door_fault_pending=True`.
  - `state=10` means acknowledged → clear pending state (even if acknowledged on another device).
- When `door_fault_pending=True`, starting heat must be blocked with a clear user-facing error.

---

### Configuration

#### Config flow

- Two-step flow:
  1) Choose a discovered device or Manual
  2) Confirm settings (and the “Allow telemetry from other IPs” option)
- If discovery found devices but user selects Manual, pre-fill host/port from discovery to avoid guessing.

#### Options flow

- Allow changing host/port/name/relaxed telemetry without removing the integration.
- Store in `entry.options` and reload on update.
- Device/entity identifiers must remain stable across host changes (use `entry.entry_id`-based identifiers).

---

### Troubleshooting checklist (what to request in issues)

1) `binary_sensor.tylo_sauna_online` state + all attributes (especially `guid`, `effective_host`, `control_port`, `endpoint_source`, `effective_telemetry_host`, `seconds_ago`)
2) Network topology: Docker vs HA OS, VLAN/guest Wi‑Fi isolation, firewall rules
3) Short UDP capture (`.pcapng`) from HA host:
   - `sudo tcpdump -i any -nn -s0 -w tylo_capture.pcapng 'udp and host <SAUNA_IP>'`

Alternative: capture on a desktop client (Mac/PC) with Wireshark, but ensure that machine is an **endpoint** of the UDP session
(e.g., open the official Tylo app there and confirm it discovers the controller) or use port mirroring/monitor mode.


