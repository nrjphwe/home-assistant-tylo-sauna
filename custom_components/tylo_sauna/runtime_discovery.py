import asyncio
import logging
import re
from typing import Any

from .const import UDP_DISCOVERY_PORTS
from .storage import EndpointStore

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
    def __init__(self, hass, on_packet):
        self._hass = hass
        self._on_packet = on_packet

    def datagram_received(self, data: bytes, addr) -> None:
        # Run async handler in HA loop without blocking the UDP protocol callback.
        self._hass.async_create_task(self._on_packet(data, addr))


class RuntimeDiscovery:
    """Listen for Tylo announces during runtime to adapt to changing control ports."""

    def __init__(self, hass, store: EndpointStore) -> None:
        self._hass = hass
        self._store = store
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
                    lambda: _RuntimeDiscoveryProtocol(self._hass, self._on_packet),
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

    async def _on_packet(self, data: bytes, addr) -> None:
        src_ip, src_port = addr
        guid, advertised_port = parse_announce(data, int(src_port))
        if not guid or advertised_port is None:
            return

        # Persist last known endpoint for discovery-first behavior.
        try:
            await self._store.set(
                guid=str(guid),
                host=str(src_ip),
                port=int(advertised_port),
                source="announce",
            )
        except Exception:  # noqa: BLE001
            pass

        domain_data = self._hass.data.get("tylo_sauna", {})

        # Only act on meaningful changes; controller logs port changes itself.
        for c in list(self._controllers):
            try:
                if getattr(c, "guid", None):
                    if str(getattr(c, "guid")) != str(guid):
                        continue
                else:
                    # If we don't have a GUID, fall back to configured host match (safer than effective host).
                    configured_host = str(getattr(c, "configured_host", getattr(c, "host", "")))
                    if configured_host != str(src_ip):
                        continue
                    # Adopt GUID for better matching later.
                    setattr(c, "guid", str(guid))

                    # Migrate config entry: persist GUID so future matching is stable.
                    entry_id = getattr(c, "entry_id", None)
                    if entry_id and isinstance(domain_data, dict):
                        entry = domain_data.get(str(entry_id), {}).get("entry")
                        try:
                            if entry and not entry.data.get("guid"):
                                new_data = {**entry.data, "guid": str(guid)}
                                self._hass.config_entries.async_update_entry(entry, data=new_data)
                                _LOGGER.info("Tylo Sauna: persisted guid=%s for entry_id=%s", guid, entry_id)
                        except Exception:  # noqa: BLE001
                            pass

                # Update effective host if it changed (e.g., DHCP after reboot).
                try:
                    try:
                        import ipaddress

                        ip = ipaddress.ip_address(str(src_ip))
                        if not ip.is_loopback and not ip.is_unspecified:
                            c.maybe_update_host(str(src_ip), source="announce", guid=str(guid))
                    except Exception:  # noqa: BLE001
                        # If parsing fails, do not update host (be conservative).
                        pass
                except Exception:  # noqa: BLE001
                    pass

                c.maybe_update_control_port(
                    int(advertised_port),
                    source="announce",
                    src_ip=str(src_ip),
                    guid=str(guid),
                )
            except Exception:  # noqa: BLE001
                continue


