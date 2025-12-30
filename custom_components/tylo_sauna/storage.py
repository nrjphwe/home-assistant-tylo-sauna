import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_STORAGE_KEY = "tylo_sauna_endpoints"


@dataclass
class EndpointRecord:
    host: str
    port: int
    last_seen: str | None = None  # ISO string
    source: str | None = None     # announce/telemetry/manual/etc


class EndpointStore:
    """Persist GUID -> last known endpoint for discovery-first behavior."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store(hass, _STORAGE_VERSION, _STORAGE_KEY)
        self._data: dict[str, EndpointRecord] = {}
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        raw = await self._store.async_load()
        if isinstance(raw, dict):
            endpoints = raw.get("endpoints", {})
            if isinstance(endpoints, dict):
                for guid, rec in endpoints.items():
                    if not isinstance(guid, str) or not isinstance(rec, dict):
                        continue
                    host = str(rec.get("host", "")).strip()
                    try:
                        port = int(rec.get("port", 0))
                    except Exception:  # noqa: BLE001
                        port = 0
                    if not host or not (0 < port <= 65535):
                        continue
                    self._data[guid] = EndpointRecord(
                        host=host,
                        port=port,
                        last_seen=rec.get("last_seen"),
                        source=rec.get("source"),
                    )
        self._loaded = True
        _LOGGER.debug("Tylo Sauna: endpoint store loaded (%d records)", len(self._data))

    def get(self, guid: str) -> EndpointRecord | None:
        return self._data.get(str(guid))

    def all_records(self) -> dict[str, EndpointRecord]:
        """Return a snapshot of all cached endpoint records."""
        return dict(self._data)

    def find_guid_for_host(self, host: str) -> str | None:
        """Return GUID if the host matches exactly one cached endpoint."""
        host = str(host).strip()
        if not host:
            return None

        matches = [guid for guid, rec in self._data.items() if rec.host == host]
        if len(matches) == 1:
            return matches[0]
        return None

    def singleton_guid(self) -> str | None:
        """Return GUID if there is exactly one cached endpoint."""
        if len(self._data) == 1:
            return next(iter(self._data.keys()))
        return None

    async def set(self, guid: str, host: str, port: int, source: str) -> None:
        guid = str(guid)
        host = str(host).strip()
        port = int(port)
        if not guid or not host or not (0 < port <= 65535):
            return

        # Не кэшируем loopback/нулевые адреса: в некоторых Docker-сетапах сауна может выглядеть как 127.0.0.1,
        # но это не является стабильным/доступным endpoint'ом для управления.
        try:
            import ipaddress

            ip = ipaddress.ip_address(host)
            if ip.is_loopback or ip.is_unspecified:
                return
        except Exception:  # noqa: BLE001
            return

        rec = EndpointRecord(
            host=host,
            port=port,
            last_seen=dt_util.utcnow().isoformat(),
            source=str(source),
        )
        self._data[guid] = rec
        await self._save()

    async def _save(self) -> None:
        payload: dict[str, Any] = {
            "endpoints": {
                guid: {
                    "host": rec.host,
                    "port": rec.port,
                    "last_seen": rec.last_seen,
                    "source": rec.source,
                }
                for guid, rec in self._data.items()
            }
        }
        await self._store.async_save(payload)


