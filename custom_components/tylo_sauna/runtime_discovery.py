import asyncio
import logging
import re
from typing import Any

from .const import UDP_DISCOVERY_PORTS

_LOGGER = logging.getLogger(__name__)

UUID_RE = re.compile(
    rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _decode_varint(buf: bytes, idx: int) -> tuple[int | None, int]:
    result = 0
    shift = 0
    i = idx
    while i < len(buf):
        b = buf[i]
        result |= (b & 0x7F) << shift
        i += 1
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 63:
            return None, idx
    return None, idx


def _iter_fields(buf: bytes):
    i = 0
    while i < len(buf):
        key, i = _decode_varint(buf, i)
        if key is None:
            return
        field_no = int(key) >> 3
        wt = int(key) & 7

        if wt == 0:
            v, i = _decode_varint(buf, i)
            if v is None:
                return
            yield field_no, wt, int(v)
        elif wt == 2:
            ln, i = _decode_varint(buf, i)
            if ln is None:
                return
            ln = int(ln)
            raw = buf[i : i + ln]
            i += ln
            yield field_no, wt, raw
        elif wt == 5:
            i += 4
        elif wt == 1:
            i += 8
        else:
            return


def parse_announce(data: bytes, src_port: int) -> tuple[str | None, int | None]:
    """Best-effort parse of Tylo UDP announce: (guid, advertised_control_port)."""
    m = UUID_RE.search(data)
    guid = m.group(0).decode("ascii") if m else None
    if not guid:
        return None, None

    advertised_port: int | None = None
    for field_no, wt, value in _iter_fields(data):
        if field_no == 2 and wt == 0:
            advertised_port = int(value)
            break

    if advertised_port is None:
        advertised_port = int(src_port)

    return guid, advertised_port


class _RuntimeDiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_packet):
        self._on_packet = on_packet

    def datagram_received(self, data: bytes, addr) -> None:
        self._on_packet(data, addr)


class RuntimeDiscovery:
    """Listen for Tylo announces during runtime to adapt to changing control ports."""

    def __init__(self, hass) -> None:
        self._hass = hass
        self._controllers: set[Any] = set()
        self._transports: list[asyncio.DatagramTransport] = []

    @property
    def is_running(self) -> bool:
        return bool(self._transports)

    def register(self, controller: Any) -> None:
        self._controllers.add(controller)

    def unregister(self, controller: Any) -> None:
        self._controllers.discard(controller)

    async def async_start(self) -> None:
        if self._transports:
            return

        loop = self._hass.loop

        async def _bind(port: int) -> None:
            try:
                transport, _proto = await loop.create_datagram_endpoint(
                    lambda: _RuntimeDiscoveryProtocol(self._on_packet),
                    local_addr=("0.0.0.0", int(port)),
                )
                self._transports.append(transport)
                _LOGGER.debug("Tylo Sauna runtime discovery: listening on UDP %s", port)
            except OSError as exc:
                _LOGGER.debug(
                    "Tylo Sauna runtime discovery: cannot bind UDP %s: %s", port, exc
                )

        for p in UDP_DISCOVERY_PORTS:
            await _bind(int(p))

    async def async_stop(self) -> None:
        for t in list(self._transports):
            try:
                t.close()
            except Exception:  # noqa: BLE001
                pass
        self._transports.clear()

    def _on_packet(self, data: bytes, addr) -> None:
        src_ip, src_port = addr
        guid, advertised_port = parse_announce(data, int(src_port))
        if not guid or advertised_port is None:
            return

        # Only act on meaningful changes; log is emitted by controller update method.
        for c in list(self._controllers):
            try:
                if getattr(c, "guid", None):
                    if str(getattr(c, "guid")) != str(guid):
                        continue
                else:
                    # If we don't have a GUID, fall back to host match.
                    if str(getattr(c, "host", "")) != str(src_ip):
                        continue
                    # Adopt GUID for better matching later (runtime only).
                    setattr(c, "guid", guid)

                c.maybe_update_control_port(int(advertised_port), source="announce", src_ip=str(src_ip), guid=str(guid))
            except Exception:  # noqa: BLE001
                continue


