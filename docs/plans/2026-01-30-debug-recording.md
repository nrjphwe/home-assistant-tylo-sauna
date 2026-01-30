# Debug Recording & Diagnostics Download — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a "Debug recording" toggle that captures all UDP traffic into a ring buffer, and a standard HA diagnostics platform so users can download a JSON file with full diagnostic data (config, state, captured packets) via one click.

**Architecture:** Ring buffer (collections.deque, maxlen=2000) in SaunaController stores every TX/RX/RX_filtered packet as a dict. Toggle via OptionsFlow (`debug_recording`). New `diagnostics.py` platform produces the JSON for HA's "Download Diagnostics" button. No new dependencies.

**Tech Stack:** Python 3.12, Home Assistant 2024.x+ diagnostics platform, collections.deque

---

### Task 1: Add `debug_recording` constant and config option

**Files:**
- Modify: `custom_components/tylo_sauna/const.py`
- Modify: `custom_components/tylo_sauna/strings.json`
- Modify: `custom_components/tylo_sauna/translations/en.json` (same content as strings.json)

**Step 1: Add constant to const.py**

After line 14 (`CONF_EXPERIMENTAL_AROMA`), add:

```python
CONF_DEBUG_RECORDING = "debug_recording"
```

**Step 2: Add UI label to strings.json**

In `"options" → "step" → "init" → "data"`, add after `"experimental_aroma"`:

```json
"debug_recording": "Debug recording (captures UDP traffic for troubleshooting)"
```

**Step 3: Copy the same to translations/en.json**

Same change as strings.json (HA uses strings.json as source, en.json as fallback — keep both in sync).

**Step 4: Commit**

```
feat: add debug_recording config constant and UI label
```

---

### Task 2: Wire `debug_recording` through config flow and __init__

**Files:**
- Modify: `custom_components/tylo_sauna/config_flow.py`
- Modify: `custom_components/tylo_sauna/__init__.py`

**Step 1: Add toggle to OptionsFlow (config_flow.py)**

In `config_flow.py`, add import of `CONF_DEBUG_RECORDING` from `.const`.

In `TyloSaunaOptionsFlowHandler.async_step_init`:

A) After line 409 (`experimental_aroma = ...`), add:
```python
debug_recording = bool(user_input.get(CONF_DEBUG_RECORDING, False))
```

B) In the `self.async_create_entry(data={...})` dict, add:
```python
CONF_DEBUG_RECORDING: debug_recording,
```

C) In the `vol.Schema({...})`, add after the `experimental_aroma` entry:
```python
vol.Optional(
    CONF_DEBUG_RECORDING,
    default=bool(current.get(CONF_DEBUG_RECORDING, False)),
): bool,
```

**Step 2: Pass to controller in __init__.py**

A) Add `CONF_DEBUG_RECORDING` to the import from `.const`.

B) After line 56 (`experimental_aroma = ...`), add:
```python
debug_recording = bool(cfg.get(CONF_DEBUG_RECORDING, False))
```

C) Add to `SaunaController(...)` constructor call:
```python
debug_recording=debug_recording,
```

**Step 3: Commit**

```
feat: wire debug_recording toggle through config flow and init
```

---

### Task 3: Add ring buffer to SaunaController

**Files:**
- Modify: `custom_components/tylo_sauna/controller.py`

**Step 1: Add import**

At top of file, add:
```python
from collections import deque
```

**Step 2: Add constructor parameter and buffer**

A) Add `debug_recording: bool = False` to `__init__` signature (after `experimental_aroma`).

B) In `__init__`, after line 464 (`self.experimental_aroma = ...`), add:
```python
self.debug_recording = bool(debug_recording)
self._debug_buffer: deque[dict] = deque(maxlen=2000)
```

**Step 3: Add helper method to record packets**

Add method after `_send` (after line ~738):

```python
def _debug_record(self, direction: str, addr: tuple, data: bytes, note: str = "") -> None:
    """Append a packet record to the debug ring buffer."""
    if not self.debug_recording:
        return
    self._debug_buffer.append({
        "ts": dt_util.utcnow().isoformat(),
        "dir": direction,
        "addr": f"{addr[0]}:{addr[1]}",
        "len": len(data),
        "hex": data.hex(),
        "note": note,
    })
```

**Step 4: Record TX packets**

In `_send` method (line ~728), after `self.tx_packets += 1` (line 735), add:

```python
self._debug_record("tx", (self.host, dst_port), payload, note=desc)
```

**Step 5: Record RX accepted packets**

In `datagram_received` (line ~758), after `self.rx_packets += 1` (line 845), add:

```python
self._debug_record("rx", addr, data)
```

**Step 6: Record RX filtered packets**

There are 3 filter points in `datagram_received` where packets are dropped. Add recording before each `return`:

A) Strict mode filter (line ~800-801):
```python
if src_ip != self.host:
    self._debug_record("rx_filtered", addr, data, note=f"strict: expected {self.host}")
    return
```

B) Relaxed mode — pinned host mismatch (line ~805-810):
```python
if src_ip != self.telemetry_host:
    self._debug_record("rx_filtered", addr, data, note=f"pinned: expected {self.telemetry_host}")
    _LOGGER.debug(...)
    return
```

C) Relaxed mode — non-telemetry (line ~817-821):
```python
if not _looks_like_tylo_telemetry(data):
    self._debug_record("rx_filtered", addr, data, note="not telemetry-like")
    _LOGGER.debug(...)
    return
```

D) Relaxed mode — GUID mismatch (line ~824-829):
```python
if self.guid and pkt_guid and pkt_guid != self.guid:
    self._debug_record("rx_filtered", addr, data, note=f"guid mismatch: pkt={pkt_guid} entry={self.guid}")
    _LOGGER.warning(...)
    return
```

**Step 7: Add note annotations for key RX events**

After recording accepted RX (step 5), the packet flows through parsers. Add annotations by updating the `_debug_record` call to be more specific. Instead of a single record at line 845, record at line 845 with no note, then add notes when key events are parsed:

Actually — simpler approach: record once at line 845 with empty note. The hex dump is enough — we can parse it offline. Adding per-event notes would require restructuring the parsing flow. Keep it simple.

**Step 8: Commit**

```
feat: add debug ring buffer to controller (2000 packets, TX/RX/filtered)
```

---

### Task 4: Create diagnostics.py platform

**Files:**
- Create: `custom_components/tylo_sauna/diagnostics.py`

**Step 1: Create diagnostics.py**

```python
"""Diagnostics support for Tylo Sauna."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

REDACT_KEYS = {"host", "guid"}


def _redact(data: dict, keys: set[str] | None = None) -> dict:
    """Redact sensitive keys from a dict."""
    if keys is None:
        keys = REDACT_KEYS
    return {k: ("**REDACTED**" if k in keys else v) for k, v in data.items()}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    entry_data = domain_data.get(entry.entry_id, {})
    controller = entry_data.get("controller")

    result: dict[str, Any] = {
        "config_entry": {
            "data": _redact(dict(entry.data)),
            "options": _redact(dict(entry.options)),
        },
    }

    if controller is None:
        result["controller"] = "not running"
        return result

    result["controller"] = {
        "host": "**REDACTED**",
        "configured_host": "**REDACTED**",
        "configured_port": controller.configured_port,
        "control_port": controller.control_port,
        "endpoint_source": controller.endpoint_source,
        "guid": "**REDACTED**" if controller.guid else None,
        "relaxed_telemetry": controller.relaxed_telemetry,
        "debug_recording": controller.debug_recording,
        "experimental_aroma": controller.experimental_aroma,
        # State
        "light": controller.light,
        "heat": controller.heat,
        "current_mode": controller.current_mode,
        "t_set_c": controller.t_set_c,
        "t_cur_c": controller.t_cur_c,
        "stop_cfg_min": controller.stop_cfg_min,
        "stop_rem_min": controller.stop_rem_min,
        "standby_enabled": controller.standby_enabled,
        "standby_delta_c": controller.standby_delta_c,
        # Diagnostics
        "rx_packets": controller.rx_packets,
        "tx_packets": controller.tx_packets,
        "last_rx_dt": str(controller.last_rx_dt) if controller.last_rx_dt else None,
        "last_rx_ip": "**REDACTED**",
        "last_rx_port": controller.last_rx_port,
        "telemetry_host": "**REDACTED**" if controller.telemetry_host else None,
        "online": controller.is_online,
        # Faults
        "door_fault_pending": controller.door_fault_pending,
        "last_fault": {
            "code": controller.last_fault.code,
            "message": controller.last_fault.message,
        } if controller.last_fault else None,
    }

    # Debug recording buffer
    result["debug_buffer"] = {
        "enabled": controller.debug_recording,
        "count": len(controller._debug_buffer),
        "max_size": controller._debug_buffer.maxlen,
        "packets": list(controller._debug_buffer),
    }

    return result
```

Note: IP addresses are redacted by default. This is standard HA practice. Users can choose to share unredacted data manually if needed. However — for our use case, **we need the IPs** to diagnose networking issues. Let's add a note in the `debug_buffer` section:

Actually, let's NOT redact IPs inside the debug buffer packets — the `addr` field in the packet records is essential for diagnosing the exact problem (packets from unexpected IPs). The top-level controller fields are redacted for general privacy, but the debug buffer is explicitly opt-in (user enables debug_recording) so they consent to detailed capture.

Update: keep `addr` in debug_buffer packets unredacted. Only redact top-level config/controller host/guid.

**Step 2: Commit**

```
feat: add diagnostics.py platform (Download Diagnostics button)
```

---

### Task 5: Test locally on dev HA

**Precondition:** Docker Desktop running, `ha-dev` container available.

**Step 1: Restart dev HA to load changes**

```bash
docker restart ha-dev
```

**Step 2: Verify integration loads**

Open http://localhost:8123, check Tylo Sauna integration is present and online.

**Step 3: Enable debug recording**

Settings → Integrations → Tylo Sauna → Configure → enable "Debug recording" → Save.
Integration will reload.

**Step 4: Verify debug recording works**

Wait ~60 seconds for some keepalive cycles. Then:

Settings → Integrations → Tylo Sauna → "⋮" → Download Diagnostics.

Open the downloaded JSON. Verify:
- `config_entry` section present with redacted host/guid
- `controller` section present with state fields
- `debug_buffer.enabled` = true
- `debug_buffer.count` > 0
- `debug_buffer.packets` contains TX (keepalive) and RX entries
- Each packet has: ts, dir, addr, len, hex, note

**Step 5: Test mode transitions**

From HA climate entity, switch between OFF → HEAT → STANDBY → OFF.
Download diagnostics again. Verify mode change packets appear in buffer.

**Step 6: Test with debug recording OFF**

Disable "Debug recording" in options. Download diagnostics.
Verify `debug_buffer.enabled` = false, `debug_buffer.count` = 0.

**Step 7: Commit**

```
test: verify debug recording and diagnostics download on dev HA
```

---

### Task 6: Update version, changelog, README

**Files:**
- Modify: `custom_components/tylo_sauna/manifest.json` — bump version to `0.4.0`
- Modify: `CHANGELOG.md` — add entry
- Modify: `README.md` — add Troubleshooting section about debug recording

**Step 1: Bump version in manifest.json**

```json
"version": "0.4.0"
```

**Step 2: Add changelog entry**

```markdown
## [0.4.0] - 2026-01-30

### Added

* **Debug recording mode** — toggle in integration options captures all UDP traffic (TX, RX, filtered RX) into a 2000-packet ring buffer for remote troubleshooting.
* **Download Diagnostics** — standard Home Assistant diagnostics platform. One-click download of full diagnostic JSON (config, controller state, captured packets) via Settings → Integrations → Tylo Sauna → "⋮" → Download Diagnostics.
```

**Step 3: Add troubleshooting section to README**

Add a "Troubleshooting" section explaining:
1. Go to Configure → enable Debug recording
2. Reproduce the issue (wait for standby, etc.)
3. Download Diagnostics → attach JSON to GitHub issue

**Step 4: Commit**

```
docs: add changelog and README for v0.4.0 debug recording
```
