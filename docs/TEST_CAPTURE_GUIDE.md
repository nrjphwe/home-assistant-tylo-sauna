# Tylo Elite Capture & Reverse-Engineering Guide (Wireshark / tcpdump)

This guide is a practical checklist for capturing and validating protocol behavior.

## Preferred capture location
### Best: capture on the Home Assistant host
Capturing on the HA machine avoids “I can’t see the traffic” issues caused by switch behavior or Wi‑Fi isolation.

Example (HA host / Linux):
```bash
tcpdump -i any -s 0 -w tylo_capture.pcapng 'udp and host <SAUNA_IP>'
```

Then open the `.pcapng` in Wireshark.

### OK: capture on a client (Mac) while using the app
Works as long as the client is one endpoint of the UDP session (it is receiving unicast traffic).
Practical rule:
- install/open the **official Tylo Elite app** on that Mac/PC,
- confirm the app **discovers the controller and shows live data**,
- then capture on the same machine.

If the desktop is not an endpoint (and you don’t have port mirroring / monitor mode), the sauna’s unicast UDP traffic may not be visible from that machine even though the system is working.

## Useful Wireshark filters
- Limit to device + UDP:
  - `ip.addr == <SAUNA_IP> && udp`
- Narrow to one stream:
  - `udp.stream eq N`
- Find key message types by bytes:
  - `frame contains c2:7f` (favorites snapshot)
  - `frame contains f2:83:01` (fault event)
  - `frame contains d2:7d` (status KV)

## How to export evidence for analysis
### Export small, relevant packet sets
- Select packets around the event (Shift-click range)
- `File → Export Specified Packets… → Selected packets only → pcapng`

### Share payload as hex (when needed)
- Packet → `User Datagram Protocol → Data`
- Right click → `Copy → Bytes → Hex Stream`

## Baseline “session capture”
Purpose: confirm the integration behaves like official app (HELLO/INIT/KEEPALIVE + telemetry).
Steps:
1) Start capture.
2) Open Tylo app and wait 10–20 seconds.
3) Stop capture.
Expected:
- periodic keepalive (~15s)
- telemetry packets with prefixes: `d2 7d`, `da 7d`, and possibly `c2 7f`.

## Temperature setpoint capture
Purpose: confirm set temp command and scaling /9.
Steps:
1) Start capture.
2) In app, change target temp by +1°C, then back.
3) Stop capture.
Expected:
- command packet with prefix `d2 41 ... 08 0a 10 <varint>` (raw = temp*9)
- subsequent telemetry reflects new `t_set`.

## Current temperature capture
Purpose: locate and verify `t_cur` field.
Steps:
1) Capture for 30–60 seconds while sauna is warming or cooling.
2) Extract consecutive `d2 7d` messages.
Expected:
- a KV field that changes steadily; raw/9 matches app temperature.

## Stop-after capture
Purpose: confirm the two-part stop-after command.
Steps:
1) Start capture.
2) In app, change Stop-after minutes.
3) Stop capture.
Expected:
- `d2 41 05 08 0e 10 <minutes>` followed by `d2 3e 02 08 01`
- telemetry updates for stop cfg (id 0x11) and remaining (id 0x16).

## Light toggle capture
Purpose: confirm light on/off commands and telemetry flag.
Steps:
1) Start capture.
2) Toggle light ON then OFF.
3) Stop capture.
Expected:
- light command packets `a2 42 ... 08 0a 10 01/00`
- telemetry flag in `da 7d ... 08 0a 10 <0|1>`.

## Favorites snapshot capture
Purpose: confirm favorites fields (name/temp/stop/light).
Steps:
1) Start capture.
2) Open the app (no need to go to favorites screen).
3) Stop capture after 30–60 seconds.
Expected:
- `c2 7f ...` snapshots with entries:
  - slot, enabled, name (bytes), temp_scaled (/9), stop_after, light flag.

## Favorites edit capture
Purpose: identify favorites update message.
Steps:
1) Start capture.
2) Edit a favorite name to a unique value (e.g., `ZZZ_TEST_123`) and save.
3) Stop capture.
Expected:
- client→sauna update packet `f2 42 ...` (favorites set/update)
- sauna may echo/confirm via `c2 7f` snapshot.

## Door cancel fault capture (Error 19/20)
Purpose: capture fault event, pending state, and ack synchronization.
Steps:
1) Start capture on a client (Mac) and keep the app open.
2) Start sauna and open door long enough to trigger cancellation.
3) Observe popup in app; do NOT press OK immediately.
Expected:
- fault event `f2 83 01 ...` with code=19 (or 20) and state=13 (pending ack)
- status updates may show remaining=0.

## Fault acknowledgement capture (OK button)
### Press OK on the same device
Steps:
1) With popup shown, press OK on the app.
Expected:
- client→sauna ACK: `82 46 ...`
- sauna→client event reflecting acknowledged state (state=10).

### Press OK on a different device (sync test)
Steps:
1) Keep Mac capture running; popup visible on Mac.
2) Press OK on iPhone.
Expected:
- Mac receives fault event with state=10 (acknowledged), even though Mac did not send ACK.
Implication:
- Home Assistant should clear pending state based on telemetry, not “we sent ack”.

## Docker/dev environment notes
- Docker bridge mode will not see broadcast discovery packets; manual IP must work.
- Host networking on Docker Desktop (macOS/Windows) can produce odd source IP behavior (e.g., 127.0.0.1).
- For reliability, integration should learn `control_port` from incoming telemetry `src_port`.
