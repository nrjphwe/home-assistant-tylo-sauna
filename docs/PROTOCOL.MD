# Tylo Elite UDP Protocol (Reverse Engineering Notes)

## Scope
This document describes the **observed** local LAN protocol for **Tylo Elite / Elite WiFi** controllers, derived from packet captures (Wireshark/tcpdump) and behavioral experiments with official apps (iOS/macOS).  
It is not official documentation. Only information confirmed by captures is included.

## High-level model
- Communication is **UDP** on the local network.
- There is a controller/panel (“sauna”) and one or more clients (official apps, Home Assistant).
- There is:
  - **Discovery/announce** traffic (broadcast / discovery ports).
  - A **control + telemetry session** using a **separate UDP port** (often **42156**, but not guaranteed).
- Payloads are **protobuf-like**: varints, field numbers, length-delimited submessages.
  - We do not have the `.proto`, so we parse using generic protobuf rules.

## Network + ports
### Discovery
- Tylo documentation mentions UDP broadcast ports **54377** (primary) and **54378** (fallback) for app connection/discovery (LAN).  
- In captures: the sauna emits broadcast announcements and/or responds to discovery listening sockets.

### Control / Telemetry port
- In real sessions, the sauna uses a **dedicated UDP port** as source for telemetry and receiver for commands.
- Observed commonly: sauna → client uses **source port 42156** and clients send commands back to that port.
- However, captures and field reports show the **effective control/telemetry port is not fixed** and may differ by firmware/setup.
- Therefore, for reliability:
  - UI “port” is **not always** the correct control port.
  - The integration should be able to **learn the effective control port** from incoming packets (`src_port`) that look like Tylo telemetry/config/events.
  - If an announce/broadcast payload advertises a port (protobuf field), that value should be preferred as an initial guess, with `src_port` learning as a runtime fallback.

## Session behavior (observed)
Typical official client behavior:
1) Sends a short **HELLO** packet multiple times.
2) Sends an **INIT** packet.
3) Sends **KEEPALIVE** periodically (every ~15 seconds).
4) Receives telemetry asynchronously.
5) Sends commands (light, temp, timers, ack faults, presets).

Home Assistant mimics this by:
- Creating a UDP socket (DatagramProtocol)
- Sending HELLO/INIT
- Sending keepalive periodically
- Listening for telemetry/events

## Message identification (prefixes / major types)
Payloads can be identified by leading bytes:

### Status / telemetry (KV-like)
- `d2 7d ...`  
  Large status packet containing repeated KV entries: `08 <id> 10 <value>` style nested messages.

### Flags / boolean status
- `da 7d ...`  
  Similar KV entries but values are typically 0/1 (bit/flag style).

### Favorites snapshot (presets)
- `c2 7f ...`  
  Snapshot/config packet that includes favorites (“Favourites”) entries and other config data.
  **Important:** favorites snapshots can arrive even when the app is NOT on the presets screen (periodic/config push).

### Fault / event (door cancel etc.)
- `f2 83 01 ...`  
  Fault/event packet containing error code, state, and message text.

### Fault ACK (client → sauna)
- `82 46 ...`  
  Client’s acknowledgment of fault popup (“OK” button behavior).

### Favorites set / update (client → sauna)
- `f2 42 ...`  
  Client sends favorites list/update (e.g., after editing presets).

## Scaling and units
### Temperature scaling
Observed: `temp_celsius = raw / 9.0`
Examples:
- 990 → 110°C
- 900 → 100°C
- 810 → 90°C
- 378 → 42°C

This scaling is used consistently across status packets and preset entries.

### Stop-after timer
- “Stop after” is configured in **minutes**.
- There is also a “remaining time until off” in minutes.

## Key telemetry fields (observed)
### Stop-after configuration and remaining
- Stop-after configured minutes: **field id 0x11 (17)**
- Remaining minutes until off: **field id 0x16 (22)**

### Temperature fields in KV telemetry
Patterns observed in `d2 7d` packets:
- Target temperature: `... 08 0a 10 <varint>` (raw/9)
- Current temperature: `... 08 0c 10 <varint>` (raw/9)

### Light state flag
Observed as a boolean flag in `da 7d` packets:
- pattern: `da 7d 04 08 0a 10 <0|1>` (light off/on)

## Commands (observed)
### Set “Stop after” minutes
Two-part command sequence:
1) `d2 41 05 08 0e 10 <varint(minutes)>`
2) `d2 3e 02 08 01` (aux/confirm)

### Set target temperature
Prefix + varint raw value (raw = round(temp_c*9)):
- `d2 41 05 08 0a 10 <varint(raw)>`

### Light on/off
Observed simple commands:
- Light ON:  `a2 42 04 08 0a 10 01`
- Light OFF: `a2 42 04 08 0a 10 00`

### Heater on/off
Observed commands:
- Heat ON:  `c2 43 02 50 0b`
- Heat OFF: `c2 43 02 50 0a`
- Plus an “aux” packet often sent with heat actions:
  - `d2 3e 02 08 1f`

## Door safety: fault/event model
The system uses faults rather than a continuously published “door open/closed” boolean.

### Fault codes
Based on official manual and observed behavior:
- **19**: Door open longer than permitted time; ongoing session cancelled.
- **20**: Door switch open longer than 60 seconds since previous session.

### Fault event packet (`f2 83 01 ...`)
Contains:
- field1: `code` (19/20/...)
- field2: `state` (important!)
  - **13** = pending acknowledgement (popup requires OK)
  - **10** = acknowledged (OK already pressed)
- field5: message text encoded as repeated varint bytes (ASCII/UTF-8 bytes)

### Fault acknowledgement (`82 46 ...`)
Client sends an ACK packet matching the fault, similar to:
- field1: code
- field2: 13 (as used by app when acknowledging)
- field3: observed constant (e.g., 21)
- field5: message bytes (repeated)
- field6: 0

### Synchronization across devices
When OK is pressed on iPhone, other clients (Mac) receive a fault event with `state=10`.  
Therefore Home Assistant should determine “pending/cleared” from telemetry (`state 13/10`), not from “we sent ack”.

## Favorites / presets (Favourites)
Favorites are represented as entries inside `c2 7f` snapshots and can be edited via `f2 42` messages.

### Favorites snapshot (`c2 7f` / field2040)
Each favorite entry includes:
- field1: slot (0..7)
- field2: enabled (1/0)
- field3: name as repeated varint bytes (ASCII/UTF-8)
- field5: target temperature scaled ×9
- field6: stop-after minutes
- field7: light flag
  - `1` = light ON
  - `0` = light OFF
- field8: reserved/unknown (often repeated zeros)

### Favorites request
Official app sends a small request often observed as:
- `d2 3e 03 08 93 03` (request id 403)  
Sauna replies with ACK and/or snapshot.

### Favorites update (`f2 42` / field1070)
When saving preset changes, the app sends an update message containing favorites entries; sauna responds/echoes confirmation.

## Capture methodology (recommended)
To reverse new fields:
1) Start capture on the machine that receives traffic (HA host is best).
   If capturing on a desktop client (Mac/PC), make sure it is an **endpoint** of the UDP session:
   open the official Tylo app on that machine and confirm it discovers the controller (otherwise you may not see unicast traffic without port mirroring / monitor mode).
2) Perform one isolated UI action in the app.
3) Compare payloads before/after.
4) Repeat in both directions (toggle on/off) to validate mapping.

Useful Wireshark patterns:
- filter by IPs: `ip.addr == <sauna_ip> && udp`
- use `frame contains d2:7d` to find status packets
- export selected packets (pcapng) for diffing
