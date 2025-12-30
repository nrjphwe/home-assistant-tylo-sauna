import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any
import time
from homeassistant.util import dt as dt_util
from datetime import timedelta
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DEFAULT_CONTROL_PORT,
    DOMAIN,
    KEEPALIVE_INTERVAL,
    ONLINE_TIMEOUT_S,
    UDP_DISCOVERY_PORTS,
)


_LOGGER = logging.getLogger(__name__)

# HELLO / INIT packets reverse engineered from the official app
HELLO_PAYLOAD = bytes.fromhex(
    "c23e33081412043030303028542879286c28f601282028722865286d286f28"
    "74286528202863286f286e28742872286f286c3a025001"
)
INIT_SHORT = bytes.fromhex("8241020802")

# Light commands
LIGHT_OFF_PAYLOAD = bytes.fromhex("a24204080a1000")
LIGHT_ON_PAYLOAD  = bytes.fromhex("a24204080a1001")

# Heating commands
HEAT_ON_PAYLOAD  = bytes.fromhex("c24302500b")
HEAT_OFF_PAYLOAD = bytes.fromhex("c24302500a")
HEAT_AUX_PAYLOAD = bytes.fromhex("d23e02081f")  # extra packet sent by the app for HEAT

UUID_RE = re.compile(
    rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _decode_varint(data: bytes, start: int):
    """Simple protobuf varint decoder."""
    result = 0
    shift = 0
    i = start
    while i < len(data):
        b = data[i]
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i + 1
        shift += 7
        i += 1
    return None, start


def _encode_varint(value: int) -> bytes:
    """Encode an integer as protobuf varint."""
    out = bytearray()
    v = int(value)
    if v < 0:
        raise ValueError("varint only supports non-negative integers")
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _parse_varint_after(data: bytes, pattern_hex: str):
    """Find varint immediately after a given hex pattern."""
    pattern = bytes.fromhex(pattern_hex)
    idx = data.find(pattern)
    if idx == -1:
        return None
    val, _ = _decode_varint(data, idx + len(pattern))
    return val


def _extract_guid_from_payload(data: bytes) -> str | None:
    """Try to extract a GUID/UUID from payload as a hint."""
    m = UUID_RE.search(data)
    if not m:
        return None
    try:
        return m.group(0).decode("ascii")
    except Exception:  # noqa: BLE001
        return None


# Favorites / presets (Favourites in the Tylo app)
FAVORITES_REQ = bytes.fromhex("d23e03089303")  # request favorites list (app uses request_id=403)
FAVORITES_SNAPSHOT_FIELD = 2040  # c2 7f

# Fault events (door cancel etc.)
FAULT_EVENT_FIELD = 2110  # f2 83 01
FAULT_ACK_FIELD = 1120    # 82 46

DOOR_FAULT_CODES = {19, 20}
STATE_PENDING_ACK = 13
STATE_ACKED = 10

TEMP_SCALE = 9  # protocol uses temp_c * 9


@dataclass(frozen=True)
class Favorite:
    slot: int
    enabled: bool
    name: str = ""
    target_temp_c: float | None = None
    stop_after_min: int | None = None
    light_on: bool | None = None


@dataclass(frozen=True)
class FaultEvent:
    code: int
    state: int
    detail: int | None
    message: str
    message_bytes: bytes


def _pb_iter_fields(buf: bytes):
    """Iterate protobuf fields: yields (field_no, wire_type, value)."""
    i = 0
    while i < len(buf):
        key, i = _decode_varint(buf, i)
        if key is None:
            return
        field_no = int(key) >> 3
        wt = int(key) & 7

        if wt == 0:  # varint
            v, i = _decode_varint(buf, i)
            if v is None:
                return
            yield field_no, wt, int(v)
        elif wt == 2:  # length-delimited
            ln, i = _decode_varint(buf, i)
            if ln is None:
                return
            ln = int(ln)
            raw = buf[i : i + ln]
            i += ln
            yield field_no, wt, raw
        elif wt == 5:  # 32-bit
            raw = buf[i : i + 4]
            i += 4
            yield field_no, wt, raw
        elif wt == 1:  # 64-bit
            raw = buf[i : i + 8]
            i += 8
            yield field_no, wt, raw
        else:
            # Unsupported
            return


def _pb_collect(buf: bytes) -> dict[int, list[tuple[int, Any]]]:
    out: dict[int, list[tuple[int, Any]]] = {}
    for f, wt, v in _pb_iter_fields(buf):
        out.setdefault(f, []).append((wt, v))
    return out


def _pb_first_varint(msg: dict[int, list[tuple[int, Any]]], n: int) -> int | None:
    for wt, v in msg.get(n, []):
        if wt == 0:
            return int(v)
    return None


def _pb_all_varints(msg: dict[int, list[tuple[int, Any]]], n: int) -> list[int]:
    res: list[int] = []
    for wt, v in msg.get(n, []):
        if wt == 0:
            res.append(int(v))
    return res


def parse_favorites_snapshot(payload: bytes) -> dict[int, Favorite]:
    """Parse favorites snapshot (field 2040, c2 7f...)."""
    top = _pb_collect(payload)
    out: dict[int, Favorite] = {}

    for wt, raw in top.get(FAVORITES_SNAPSHOT_FIELD, []):
        if wt != 2:
            continue
        entry = _pb_collect(raw)
        slot = _pb_first_varint(entry, 1)
        enabled = _pb_first_varint(entry, 2)

        if slot is None:
            continue

        if enabled != 1:
            out[int(slot)] = Favorite(slot=int(slot), enabled=False)
            continue

        name = bytes(_pb_all_varints(entry, 3)).decode("utf-8", errors="replace")
        temp_scaled = _pb_first_varint(entry, 5)
        stop_after = _pb_first_varint(entry, 6)
        light = _pb_first_varint(entry, 7)

        out[int(slot)] = Favorite(
            slot=int(slot),
            enabled=True,
            name=name,
            target_temp_c=(float(temp_scaled) / TEMP_SCALE) if temp_scaled is not None else None,
            stop_after_min=int(stop_after) if stop_after is not None else None,
            light_on=(bool(light) if light is not None else None),
        )

    return out


def parse_fault_event(payload: bytes) -> FaultEvent | None:
    """Parse fault event (field 2110, f2 83 01...)."""
    top = _pb_collect(payload)
    items = top.get(FAULT_EVENT_FIELD, [])
    if not items:
        return None

    wt, raw = items[0]
    if wt != 2:
        return None

    msg = _pb_collect(raw)
    code = _pb_first_varint(msg, 1)
    state = _pb_first_varint(msg, 2)
    detail = _pb_first_varint(msg, 3)

    if code is None or state is None:
        return None

    msg_bytes = bytes(_pb_all_varints(msg, 5))
    message = msg_bytes.decode("utf-8", errors="replace")

    return FaultEvent(
        code=int(code),
        state=int(state),
        detail=int(detail) if detail is not None else None,
        message=message,
        message_bytes=msg_bytes,
    )


def _pb_encode_key(field_no: int, wire_type: int) -> bytes:
    return _encode_varint((int(field_no) << 3) | int(wire_type))


def _pb_encode_varint_field(field_no: int, value: int) -> bytes:
    return _pb_encode_key(field_no, 0) + _encode_varint(int(value))


def _pb_encode_bytes_field(field_no: int, raw: bytes) -> bytes:
    return _pb_encode_key(field_no, 2) + _encode_varint(len(raw)) + raw


def encode_fault_ack(fault: FaultEvent) -> bytes:
    """Encode ACK packet like the official app (field1120)."""
    body = bytearray()
    body += _pb_encode_varint_field(1, fault.code)
    body += _pb_encode_varint_field(2, STATE_PENDING_ACK)  # app uses 13 in ack request
    body += _pb_encode_varint_field(3, 21)                 # observed in app ack
    for ch in (fault.message_bytes or fault.message.encode("utf-8", errors="ignore")):
        body += _pb_encode_varint_field(5, int(ch))
    body += _pb_encode_varint_field(6, 0)
    return _pb_encode_bytes_field(FAULT_ACK_FIELD, bytes(body))


def _looks_like_tylo_telemetry(data: bytes) -> bool:
    """
    Heuristic check to avoid accepting random UDP noise when relaxed mode is enabled.
    """
    # Allow known non-telemetry packets that we still want to accept in relaxed mode
    if data.startswith(b"\xc2\x7f"):  # favorites snapshot (field 2040)
        return True
    if data.startswith(b"\xf2\x83\x01"):  # fault event (field 2110)
        return True

    markers = (
        b"\xd2\x7d\x05\x08\x0a\x10",  # Tset
        b"\xd2\x7d\x05\x08\x0c\x10",  # Tcur
        b"\xd2\x7d\x04\x08\x11\x10",  # StopCfg alt
        b"\xd2\x7d\x05\x08\x11\x10",  # StopCfg
        b"\xd2\x7d\x04\x08\x16\x10",  # StopRem alt
        b"\xd2\x7d\x05\x08\x16\x10",  # StopRem
        b"\xda\x7d\x04\x08\x0a\x10",  # Light flag
    )
    return any(m in data for m in markers)


class SaunaProtocol(asyncio.DatagramProtocol):
    """Asyncio protocol used by SaunaController."""

    def __init__(self, controller: "SaunaController"):
        self.controller = controller
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self.controller.connection_made(self.transport)  # type: ignore[arg-type]

    def datagram_received(self, data: bytes, addr) -> None:
        self.controller.datagram_received(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.warning("Tylo Sauna UDP error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        _LOGGER.info("Tylo Sauna UDP connection lost: %s", exc)
        self.controller.connection_lost(exc)


class SaunaController:
    """
    Local UDP controller for Tylo Elite.

    The official app sends HELLO 3 times, INIT_SHORT, then periodic KEEPALIVE (same INIT_SHORT).
    Telemetry packets are received asynchronously.
    """

    def __init__(
        self,
        hass,
        name: str,
        host: str,
        port: int = 54377,
        guid: str | None = None,
        relaxed_telemetry: bool = False,
    ):
        self._hass = hass
        self.name = name
        self.host = host
        self.port = port
        # Configured port (from UI). Actual control port may be learned from src_port.
        self.configured_port = int(port)
        self.control_port = int(port)
        # Last seen telemetry (for online/last_seen sensors)
        self.last_rx_dt = None  # datetime in UTC

        self.last_rx_ip: str | None = None
        self.last_rx_port: int | None = None

        self._unsub_watchdog = None
        self._unsub_keepalive = None
        self._online_cached = False
        self._last_publish_monotonic = time.monotonic()
        self._last_offline_probe_monotonic = 0.0

        # If user configured a discovery port, also probe the legacy observed control-port candidate.
        self._probe_ports: set[int] = {self.control_port}
        if self.control_port in UDP_DISCOVERY_PORTS:
            self._probe_ports |= {DEFAULT_CONTROL_PORT, *UDP_DISCOVERY_PORTS}

        self.guid = guid
        self.relaxed_telemetry = relaxed_telemetry
        self.endpoint_source: str = "config"

        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: SaunaProtocol | None = None

        # Learned telemetry sender (may differ from configured host)
        self.telemetry_host: str | None = None

        # Sauna state (mirrored from telemetry)
        self.light: bool | None = None
        self.heat: bool | None = None
        self.t_set_c: float | None = None
        self.t_cur_c: float | None = None
        self.stop_cfg_min: int | None = None   # configured Stop after (minutes)
        self.stop_rem_min: int | None = None   # remaining time to auto-off (minutes)

        # Faults / safety events
        self.last_fault: FaultEvent | None = None
        self.door_fault_pending: bool = False

        # Favorites / presets
        self.favorites: dict[int, Favorite] = {}
        self.last_selected_favorite_slot: int | None = None

        # Diagnostics
        self.rx_packets = 0
        self.tx_packets = 0
        self.last_rx_monotonic: float | None = None

        # Callbacks to notify entities
        self._callbacks: list[callable] = []

    def maybe_update_host(self, host: str, source: str = "unknown", guid: str | None = None) -> None:
        """Update effective host when we learn a better value (announce/cache)."""
        new_host = str(host).strip()
        if not new_host:
            return

        # Never switch the effective endpoint to loopback/unspecified addresses.
        # In some Docker/macOS setups the sauna announce may appear to come from 127.0.0.1,
        # but that is not a routable endpoint for UDP control.
        try:
            import ipaddress

            ip = ipaddress.ip_address(new_host)
            if ip.is_loopback or ip.is_unspecified:
                return
        except Exception:  # noqa: BLE001
            # If parsing fails, be conservative: only reject obvious loopback literals.
            if new_host in {"127.0.0.1", "0.0.0.0", "::1", "::"}:
                return

        if str(getattr(self, "host", "")).strip() == new_host:
            return

        old = str(getattr(self, "host", "")).strip()
        self.host = new_host
        self.endpoint_source = str(source)

        # Reset pinned telemetry host so we can accept packets from the new endpoint.
        self.telemetry_host = None
        self.last_rx_ip = None
        self.last_rx_port = None
        self.last_rx_monotonic = None

        _LOGGER.info(
            "Tylo Sauna: updated host=%s (was %s) via %s (guid=%s)",
            self.host,
            old,
            source,
            guid or getattr(self, "guid", None) or "n/a",
        )

    def maybe_update_control_port(self, port: int, source: str = "unknown", src_ip: str | None = None, guid: str | None = None) -> None:
        """Update effective control_port when we learn a better value (announce/telemetry)."""
        try:
            new_port = int(port)
        except Exception:  # noqa: BLE001
            return

        if new_port <= 0 or new_port > 65535:
            return

        if new_port == int(getattr(self, "control_port", 0)):
            return

        old = int(getattr(self, "control_port", 0))
        self.control_port = new_port
        self._probe_ports = {self.control_port}
        self.endpoint_source = str(source)
        _LOGGER.info(
            "Tylo Sauna: updated control_port=%s (was %s) via %s from %s (guid=%s)",
            self.control_port,
            old,
            source,
            src_ip or "n/a",
            guid or getattr(self, "guid", None) or "n/a",
        )

        # Fast recovery: after port change some firmwares require a fresh HELLO/INIT handshake
        # before accepting commands on the new port (telemetry may still arrive).
        if self._transport:
            try:
                self._hass.async_create_task(self._async_port_switch_handshake(int(self.control_port)))
            except Exception:  # noqa: BLE001
                # fallback: at least send INIT once
                try:
                    self._send(INIT_SHORT, desc="PORT_SWITCH_INIT", port=int(self.control_port))
                except Exception:  # noqa: BLE001
                    pass

    async def _async_port_switch_handshake(self, port: int) -> None:
        """Send a short HELLO/INIT sequence to the specified port to re-establish control."""
        try:
            p = int(port)
        except Exception:  # noqa: BLE001
            return
        if not (0 < p <= 65535):
            return
        # Reuse the same pattern as the initial sequence (small delays are intentional).
        self._send(HELLO_PAYLOAD, "PORT_SWITCH_HELLO 1", port=p)
        await asyncio.sleep(0.1)
        self._send(HELLO_PAYLOAD, "PORT_SWITCH_HELLO 2", port=p)
        await asyncio.sleep(0.1)
        self._send(HELLO_PAYLOAD, "PORT_SWITCH_HELLO 3", port=p)
        await asyncio.sleep(0.1)
        self._send(INIT_SHORT, "PORT_SWITCH_INIT", port=p)

    async def async_start(self) -> None:
        """Create UDP socket and send initial HELLO/INIT sequence."""
        loop = self._hass.loop
        _LOGGER.info("Tylo Sauna: creating UDP endpoint for %s:%s", self.host, self.port)

        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: SaunaProtocol(self),
            local_addr=("0.0.0.0", 0),
        )
        self.start_watchdog()
        self._hass.create_task(self._async_init_sequence())

    async def _async_init_sequence(self) -> None:
        self._send_probe(HELLO_PAYLOAD, "HELLO 1")
        await asyncio.sleep(0.1)
        self._send_probe(HELLO_PAYLOAD, "HELLO 2")
        await asyncio.sleep(0.1)
        self._send_probe(HELLO_PAYLOAD, "HELLO 3")
        await asyncio.sleep(0.1)
        self._send_probe(INIT_SHORT, "INIT_SHORT")
        await asyncio.sleep(0.1)
        self.request_favorites()

    def is_online(self) -> bool:
        last = getattr(self, "last_rx_monotonic", None)
        if last is None:
            return False
        return (time.monotonic() - float(last)) <= ONLINE_TIMEOUT_S


    def start_watchdog(self) -> None:
        if self._unsub_watchdog is not None:
            return

        async def _tick(_now):
            online = self.is_online()
            now_m = time.monotonic()

            publish = False
            if online != self._online_cached:
                self._online_cached = online
                publish = True

            # once per minute publish a "heartbeat" so online/offline can update on its own
            if (now_m - self._last_publish_monotonic) >= 60:
                publish = True

            if publish:
                self._last_publish_monotonic = now_m
                self._notify_listeners()

            # If we're offline, periodically probe a set of candidate ports.
            # This helps when the controller changes its effective control port after reboot
            # and broadcasts are not visible to Home Assistant (common in some Docker setups).
            if not online:
                # Probe at most once per 30s to avoid spamming.
                if (now_m - float(self._last_offline_probe_monotonic)) >= 30:
                    self._last_offline_probe_monotonic = now_m
                    self._send_offline_probe()

        self._unsub_watchdog = async_track_time_interval(
            self._hass, _tick, timedelta(seconds=10)
        )

    def _send_offline_probe(self) -> None:
        """Send a lightweight handshake probe to a set of likely ports when offline.

        Important: some controllers appear to require a HELLO before INIT/telemetry starts,
        so we probe with HELLO + INIT_SHORT (very low frequency) to recover after reboot.
        """
        # Prefer the currently known ports first.
        ports = [
            int(getattr(self, "control_port", 0) or 0),
            int(getattr(self, "configured_port", 0) or 0),
            int(DEFAULT_CONTROL_PORT),
            *[int(p) for p in UDP_DISCOVERY_PORTS],
        ]
        # De-dup and remove invalid values.
        seen: set[int] = set()
        uniq: list[int] = []
        for p in ports:
            if p <= 0 or p > 65535:
                continue
            if p in seen:
                continue
            seen.add(p)
            uniq.append(p)

        # No transport yet -> nothing to do.
        if not self._transport:
            return

        for p in uniq:
            # Probe like the official app: HELLO then INIT.
            self._send(HELLO_PAYLOAD, desc=f"OFFLINE_PROBE_HELLO (port {p})", port=p)
            self._send(INIT_SHORT, desc=f"OFFLINE_PROBE_INIT (port {p})", port=p)

    def start_keepalive(self) -> None:
        if self._unsub_keepalive is not None:
            return

        async def _tick(_now):
            if not self._transport:
                return
            self._send(INIT_SHORT, "KEEPALIVE")

        self._unsub_keepalive = async_track_time_interval(
            self._hass, _tick, timedelta(seconds=KEEPALIVE_INTERVAL)
        )

    async def async_stop(self) -> None:
        if self._unsub_keepalive:
            self._unsub_keepalive()
            self._unsub_keepalive = None

        if self._unsub_watchdog:
            self._unsub_watchdog()
            self._unsub_watchdog = None

        if self._transport:
            self._transport.close()
            self._transport = None



    def _send(self, payload: bytes, desc: str = "", port: int | None = None) -> None:
        if not self._transport:
            _LOGGER.warning("Tylo Sauna: transport not ready, cannot send %s", desc or "")
            return

        dst_port = int(port) if port is not None else int(self.control_port)
        self._transport.sendto(payload, (self.host, dst_port))
        self.tx_packets += 1
        if desc:
            _LOGGER.debug("Tylo Sauna: send %s (%d bytes) -> %s:%s", desc, len(payload), self.host, dst_port)

    def _send_probe(self, payload: bytes, desc: str = "") -> None:
        """Send init packets to a small set of candidate ports when port is uncertain."""
        ports = sorted(self._probe_ports) if self._probe_ports else [self.control_port]
        for p in ports:
            suffix = f" (port {p})" if len(ports) > 1 else ""
            self._send(payload, desc + suffix, port=p)

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        sockname = transport.get_extra_info("sockname")
        _LOGGER.info("Tylo Sauna: UDP socket bound on %s", sockname)

        # Start periodic jobs once transport exists
        self.start_keepalive()
        self.start_watchdog()


    def connection_lost(self, exc: Exception | None) -> None:
        _LOGGER.info("Tylo Sauna: connection lost: %s", exc)

    def datagram_received(self, data: bytes, addr) -> None:
        src_ip, src_port = addr

        # If GUID is not known (manual setup), try to learn it from incoming Tylo packets.
        # Not all firmwares include it in unicast telemetry, but when present it helps discovery-first caching.
        if getattr(self, "guid", None) is None:
            pkt_guid = _extract_guid_from_payload(data)
            if pkt_guid:
                try:
                    self.guid = pkt_guid
                    # Persist GUID into the config entry if available (migration without re-add).
                    entry_id = getattr(self, "entry_id", None)
                    domain_data = getattr(self._hass, "data", {}).get(DOMAIN, {})
                    entry = None
                    if entry_id and isinstance(domain_data, dict):
                        entry = domain_data.get(str(entry_id), {}).get("entry")
                    if entry and not entry.data.get("guid"):
                        new_data = {**entry.data, "guid": str(pkt_guid)}
                        self._hass.config_entries.async_update_entry(entry, data=new_data)
                    # Persist endpoint cache if store exists.
                    store = domain_data.get("_endpoint_store") if isinstance(domain_data, dict) else None
                    if store:
                        try:
                            self._hass.async_create_task(
                                store.set(
                                    guid=str(pkt_guid),
                                    # Кэшируем endpoint из фактического источника пакета, а не из текущего self.host
                                    # (на Docker/macOS self.host может быть неверно переписан в 127.0.0.1).
                                    host=str(src_ip),
                                    port=int(src_port),
                                    source="telemetry",
                                )
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    _LOGGER.info("Tylo Sauna: learned guid=%s from telemetry", pkt_guid)
                except Exception:  # noqa: BLE001
                    pass


        if not self.relaxed_telemetry:
            # Strict mode: only accept telemetry from configured host
            if src_ip != self.host:
                return
        else:
            # Relaxed mode: accept telemetry from pinned telemetry_host OR learn it
            if self.telemetry_host is not None:
                if src_ip != self.telemetry_host:
                    _LOGGER.debug(
                        "Tylo Sauna: ignoring telemetry from %s (pinned telemetry_host=%s)",
                        src_ip, self.telemetry_host
                    )
                    return
            else:
                if src_ip == self.host:
                    # OK, accept packets from configured host
                    pass
                else:
                    # Not from configured host
                    if not _looks_like_tylo_telemetry(data):
                        _LOGGER.debug(
                            "Tylo Sauna: ignoring non-telemetry UDP packet from %s", src_ip
                        )
                        return

                    pkt_guid = _extract_guid_from_payload(data)
                    if self.guid and pkt_guid and pkt_guid != self.guid:
                        _LOGGER.warning(
                            "Tylo Sauna: telemetry GUID mismatch from %s: packet_guid=%s, entry_guid=%s. Ignoring.",
                            src_ip, pkt_guid, self.guid
                        )
                        return

                    # Accept & pin
                    self.telemetry_host = src_ip
                    _LOGGER.warning(
                        "Tylo Sauna: telemetry received from %s (configured host=%s). "
                        "Pinning telemetry_host=%s (guid_hint=%s).",
                        src_ip, self.host, src_ip, pkt_guid or "n/a"
                    )

        # Learn actual control port from incoming Tylo packets (helps Docker users who kept discovery port).
        self._maybe_learn_control_port(src_port, data)

        self.last_rx_ip = src_ip
        self.last_rx_port = int(src_port)

        self.rx_packets += 1
        self.last_rx_monotonic = time.monotonic()
        self.last_rx_dt = dt_util.utcnow()


        # --- Fault events (door cancel etc.) ---
        if data.startswith(b"\xf2\x83\x01") or b"\xf2\x83\x01" in data:
            fault = parse_fault_event(data)
            if fault is not None:
                prev_pending = self.door_fault_pending
                prev_fault = self.last_fault
                self.last_fault = fault
                self.door_fault_pending = (
                    fault.code in DOOR_FAULT_CODES and fault.state == STATE_PENDING_ACK
                )
                if prev_pending != self.door_fault_pending or prev_fault != self.last_fault:
                    _LOGGER.info(
                        "Tylo Sauna fault: code=%s state=%s pending_ack=%s msg=%s",
                        fault.code,
                        fault.state,
                        self.door_fault_pending,
                        fault.message,
                    )
                    self._notify_listeners()
                return

        # --- Favorites snapshot ---
        if data.startswith(b"\xc2\x7f"):
            favs = parse_favorites_snapshot(data)
            if favs and favs != self.favorites:
                self.favorites = favs
                _LOGGER.info("Tylo Sauna: favorites updated (%d slots)", len(favs))
                self._notify_listeners()
            return

        self._handle_telemetry(data)

    def _maybe_learn_control_port(self, src_port: int, data: bytes) -> None:
        # Only learn from packets that look like Tylo telemetry/config/events.
        if not _looks_like_tylo_telemetry(data):
            return

        # If we know the GUID, require it to match before accepting a port update.
        pkt_guid = _extract_guid_from_payload(data)
        if self.guid and pkt_guid and pkt_guid != self.guid:
            return

        src_port = int(src_port)
        self.maybe_update_control_port(src_port, source="telemetry", src_ip=str(getattr(self, "last_rx_ip", None) or self.host), guid=getattr(self, "guid", None))

    # === Telemetry parsing ===

    def _handle_telemetry(self, data: bytes) -> None:
        changed = False

        light = self._parse_light(data)
        if light is not None and light != self.light:
            self.light = light
            changed = True

        stop_cfg = self._parse_stop_cfg(data)
        if stop_cfg is not None and stop_cfg != self.stop_cfg_min:
            self.stop_cfg_min = stop_cfg
            changed = True

        stop_rem = self._parse_stop_rem(data)
        if stop_rem is not None and stop_rem != self.stop_rem_min:
            self.stop_rem_min = stop_rem
            changed = True

        new_heat = None
        if self.stop_rem_min is not None:
            new_heat = self.stop_rem_min > 0
        if new_heat is not None and new_heat != self.heat:
            self.heat = new_heat
            changed = True

        t_set_c = self._parse_temp_set(data)
        if t_set_c is not None and t_set_c != self.t_set_c:
            self.t_set_c = t_set_c
            changed = True

        t_cur_c = self._parse_temp_cur(data)
        if t_cur_c is not None and t_cur_c != self.t_cur_c:
            self.t_cur_c = t_cur_c
            changed = True

        if changed:
            telemetry_src = self.telemetry_host or self.host
            _LOGGER.info(
                "Tylo Sauna state: LIGHT=%s, HEAT=%s, Tset=%s°C, Tcur=%s°C, StopCfg=%s, StopRem=%s "
                "(telemetry_host=%s, rx=%d, tx=%d)",
                self.light,
                self.heat,
                f"{self.t_set_c:.1f}" if self.t_set_c is not None else "?",
                f"{self.t_cur_c:.1f}" if self.t_cur_c is not None else "?",
                self.stop_cfg_min if self.stop_cfg_min is not None else "?",
                self.stop_rem_min if self.stop_rem_min is not None else "?",
                telemetry_src,
                self.rx_packets,
                self.tx_packets,
            )
            self._notify_listeners()

    def _parse_light(self, data: bytes) -> bool | None:
        pattern = bytes.fromhex("da7d04080a10")
        idx = data.find(pattern)
        if idx == -1 or idx + len(pattern) >= len(data):
            return None
        val = data[idx + len(pattern)]
        if val == 1:
            return True
        if val == 0:
            return False
        return None

    def _parse_stop_cfg(self, data: bytes) -> int | None:
        for prefix_hex in ("d27d05081110", "d27d04081110"):
            val = _parse_varint_after(data, prefix_hex)
            if val is not None:
                return val
        return None

    def _parse_stop_rem(self, data: bytes) -> int | None:
        for prefix_hex in ("d27d05081610", "d27d04081610"):
            val = _parse_varint_after(data, prefix_hex)
            if val is not None:
                return val
        return None

    def _parse_temp_set(self, data: bytes) -> float | None:
        raw = _parse_varint_after(data, "d27d05080a10")
        if raw is None:
            return None
        return raw / 9.0

    def _parse_temp_cur(self, data: bytes) -> float | None:
        raw = _parse_varint_after(data, "d27d05080c10")
        if raw is None:
            return None
        return raw / 9.0

    # === API for entities ===

    def register_callback(self, cb) -> None:
        self._callbacks.append(cb)

    def _notify_listeners(self) -> None:
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.exception("Tylo Sauna callback error: %s", exc)

    # --- Commands ---

    def request_favorites(self) -> None:
        """Request favorites list from the controller."""
        self._send(FAVORITES_REQ, "REQ_FAVORITES")

    def light_on(self) -> None:
        self._send(LIGHT_ON_PAYLOAD, "LIGHT ON")

    def light_off(self) -> None:
        self._send(LIGHT_OFF_PAYLOAD, "LIGHT OFF")

    def heat_on(self) -> None:
        self._send(HEAT_ON_PAYLOAD, "HEAT ON")
        self._send(HEAT_AUX_PAYLOAD, "HEAT AUX")

    def heat_off(self) -> None:
        self._send(HEAT_OFF_PAYLOAD, "HEAT OFF")
        self._send(HEAT_AUX_PAYLOAD, "HEAT AUX")

    async def async_set_temperature(self, temp_c: float) -> None:
        raw = int(round(temp_c * 9.0))
        prefix = bytes.fromhex("d24105080a10")
        payload = prefix + _encode_varint(raw)
        self._send(payload, f"SETTEMP {temp_c:.1f}°C")

    async def async_set_stop_after(self, minutes: int) -> None:
        m = int(minutes)
        var = _encode_varint(m)
        p1 = bytes.fromhex("d24105080e10") + var
        p2 = bytes.fromhex("d23e020801")
        self._send(p1, f"SETSTOP {m} min (cfg)")
        await asyncio.sleep(0.02)
        self._send(p2, "SETSTOP aux")

    async def async_apply_favorite(self, slot: int, start: bool = False) -> None:
        """Apply a favorite preset as a 'scene' (temp + stop-after + light), optionally start."""
        fav = self.favorites.get(int(slot))
        if not fav or not fav.enabled:
            _LOGGER.warning("Tylo Sauna: favorite slot %s not available", slot)
            return

        # Light
        if fav.light_on is not None:
            if fav.light_on:
                self.light_on()
            else:
                self.light_off()

        # Temperature
        if fav.target_temp_c is not None:
            await self.async_set_temperature(float(fav.target_temp_c))

        # Stop-after
        if fav.stop_after_min is not None:
            await self.async_set_stop_after(int(fav.stop_after_min))

        self.last_selected_favorite_slot = int(slot)
        self._notify_listeners()

        if start:
            self.heat_on()

    async def async_ack_last_fault(self) -> None:
        """Acknowledge the last fault popup (e.g. door cancel) like the official app."""
        if not self.last_fault:
            _LOGGER.warning("Tylo Sauna: no last_fault to acknowledge")
            return
        payload = encode_fault_ack(self.last_fault)
        self._send(payload, "ACK_FAULT")
