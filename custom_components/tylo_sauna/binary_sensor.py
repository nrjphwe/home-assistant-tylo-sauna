import logging

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        _LOGGER.error(
            "Tylo Sauna binary_sensor: controller not found for entry %s", entry.entry_id
        )
        return

    controller = data["controller"]
    async_add_entities([TyloSaunaOnlineBinarySensor(controller)])
    _LOGGER.info("Tylo Sauna online binary_sensor added")


class TyloSaunaOnlineBinarySensor(BinarySensorEntity):
    """Online/offline indicator + diagnostics attributes (always available)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, controller) -> None:
        self._controller = controller
        self._device_id = getattr(controller, "device_id", controller.host)

        self._attr_name = f"{controller.name} online"
        self._attr_unique_id = f"tylo_sauna_{self._device_id}_online"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._controller.name,
            manufacturer="Tylo",
            model="Elite",
        )

    @property
    def available(self) -> bool:
        # Important: this entity must always be available, even if the sauna is offline.
        return True

    @property
    def is_on(self) -> bool:
        return bool(self._controller.is_online())

    @property
    def extra_state_attributes(self):
        last_dt = getattr(self._controller, "last_rx_dt", None)
        seconds_ago = None
        if last_dt is not None:
            seconds_ago = int((dt_util.utcnow() - last_dt).total_seconds())

        pinned = getattr(self._controller, "telemetry_host", None)
        last_ip = getattr(self._controller, "last_rx_ip", None)

        return {
            # Config / options
            "configured_host": getattr(self._controller, "configured_host", self._controller.host),
            "configured_port": getattr(self._controller, "configured_port", getattr(self._controller, "port", None)),
            "relaxed_telemetry": bool(getattr(self._controller, "relaxed_telemetry", False)),

            # Last seen (no separate sensor -> no Activity spam)
            "last_seen": last_dt,
            "seconds_ago": seconds_ago,

            # Where telemetry actually came from
            "last_rx_ip": last_ip,
            "last_rx_port": getattr(self._controller, "last_rx_port", None),
            "pinned_telemetry_host": pinned,
            "effective_telemetry_host": pinned or last_ip,

            # If you implemented control_port auto-learn
            "control_port": getattr(self._controller, "control_port", None),

            # Counters
            "rx_packets": getattr(self._controller, "rx_packets", 0),
            "tx_packets": getattr(self._controller, "tx_packets", 0),
        }

    async def async_added_to_hass(self) -> None:
        self._controller.register_callback(self.async_write_ha_state)
