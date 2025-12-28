# Tylo Sauna Home Assistant Integration (tylo_sauna) — Engineering Spec

## Goals
Implement a reliable, local-only Home Assistant integration for **Tylo Elite / Elite WiFi** using reverse-engineered UDP protocol.

Key user-facing features:
1) Control heater on/off, temperature setpoint, current temperature.
2) Control light on/off.
3) Configure “Stop after” (auto-off minutes) and show remaining time.
4) Support favorites/presets:
   - Auto-refresh favorites
   - Apply favorite as “scene” (temp + stop-after + light); by default do NOT start heating implicitly
   - Edit favorites from HA (future/optional)
5) Handle door safety faults:
   - Show door cancel fault (code 19/20)
   - Block start while “pending acknowledgement”
   - Provide ACK action
   - Sync acknowledgement if pressed on other device
6) Provide diagnostics without UI spam:
   - One diagnostic entity with network/telemetry details
   - Avoid state changes that flood the Activity log

## Architecture
### Controller
- UDP client+listener using `asyncio.DatagramProtocol`.
- Sends initialization packets (HELLO/INIT), then keepalive.
- Receives and parses UDP payloads; updates internal state fields.
- Calls registered callbacks to trigger entity state updates.

### Discovery
- Config flow tries to listen on discovery ports (54377/54378) for a short period.
- In Docker bridge mode discovery may not work (broadcast); manual entry must work.

### “Relaxed telemetry” mode
- Some setups send telemetry from a different IP than configured “panel IP”.
- When `relaxed_telemetry=True`, controller learns and pins `telemetry_host` when it sees valid Tylo packets.
- Controller should also learn effective `control_port` from incoming packets (`src_port`) to help Docker users who entered discovery port.

### Avoiding HA startup blocking
- Do not run infinite background tasks that block HA bootstrap.
- Prefer `async_track_time_interval` timers for periodic work (keepalive + online watchdog).
- Ensure `async_stop()` unsubscribes timers and closes transport.
- Ensure entry unload stops controller cleanly.

## Entities and services
### Core entities
1) `climate.tylo_sauna`
- hvac modes: `heat`, `off`
- target temperature, current temperature
- additional attributes: stop-after/remaining, door fault pending (minimal)
- Should NOT contain verbose network diagnostics.

2) `light.tylo_sauna_light`
- on/off

3) `number.tylo_sauna_stop_time`
- set stop-after minutes (0..600)

4) `sensor.tylo_sauna_time_to_off`
- remaining minutes

### Diagnostics entities
5) `binary_sensor.tylo_sauna_online` (connectivity)
- Must always be `available=True`
- `is_on` = controller online status (based on last_rx timestamp)
- Stores diagnostics attributes:
  - configured_host/port, relaxed_telemetry
  - last_seen + seconds_ago
  - last_rx_ip/port
  - pinned_telemetry_host + effective_telemetry_host
  - control_port (learned)
  - rx/tx counters
- Should be `EntityCategory.DIAGNOSTIC`.

Fault sensors can be separate diagnostic sensors:
6) `sensor.tylo_sauna_fault_code` (diagnostic, always available)
7) `sensor.tylo_sauna_fault_message` (diagnostic, always available)

### Favorites
8) `select.tylo_sauna_favorite`
- options = active favorites (slot + name)
- selecting option applies favorite as scene (temp + stop-after + light), no implicit start by default (start heating is a separate action via `climate`).

### Fault ACK (optional entity)
9) `button.tylo_ack_fault` or service
- Acknowledge last fault (send ACK packet)

## UX rules
### Offline / wrong IP behavior
- If controller receives no telemetry, most control entities should become `Unavailable` (via `available` property).
- Diagnostic online entity remains available and shows last_seen and configured endpoint.

### Do not spam Activity log
- Avoid separate `last_seen` sensor. Prefer it as attribute on `binary_sensor.tylo_sauna_online`.
- If any periodic updates are needed, throttle them and avoid frequent state writes.

### Door fault handling
- When fault code 19/20 arrives with state=13, set `door_fault_pending=True`.
- While pending:
  - block heat start and present clear error (no silent failure).
- When state=10 arrives: pending cleared (even if acknowledged on iPhone).

## Config + Options
### Config flow (initial setup)
- Try discovery; offer manual host/port.
- Store config in `entry.data`.

### Options flow (post-setup changes)
- Allow changing host/port/name/relaxed_telemetry without removing integration.
- Store changes in `entry.options`.
- On options update: reload entry.
- Device/entity identifiers must be stable across host changes:
  - Use `entry.entry_id` as `device_id` for `DeviceInfo.identifiers` and unique_id prefix.

## Reliability improvements for Docker users
- Auto-learn `control_port` from `src_port` of valid incoming packets.
- Probe a small set of ports on init when configured port is a discovery port (54377/54378).
- Keepalive must not spam warnings if transport is not ready.

## Testing plan
### Basic control
- Set temp: confirm controller receives and sauna follows.
- Stop-after set: confirm telemetry shows cfg and remaining.
- Light on/off: confirm toggles.

### Offline UX
- Configure wrong IP: verify controls become unavailable and online binary_sensor shows offline/last_seen None.

### Door fault
- Start sauna, open door until cancel.
- Verify fault code/message and pending state.
- Press OK on iPhone; verify pending clears in HA.

### Favorites
- Verify favorites appear from snapshot.
- Change a preset in app; verify HA select options update.
- Select favorite in HA; verify temp/stop/light update.

## Logging
- Keep debug logs helpful but avoid spamming warnings on normal offline states.
- Network diagnostics belong in online binary sensor attributes.
