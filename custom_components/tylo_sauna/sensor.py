import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        _LOGGER.error("Tylo Sauna sensor: controller not found for entry %s", entry.entry_id)
        return

    controller = data["controller"]
    async_add_entities(
        [
            TyloSaunaTimeToOff(controller),
            TyloSaunaFaultCode(controller),
            TyloSaunaFaultMessage(controller),
        ]
    )
    _LOGGER.info("Tylo Sauna sensors added")


class _BaseTyloSensor(SensorEntity):
    def __init__(self, controller) -> None:
        self._controller = controller
        self._device_id = getattr(controller, "device_id", controller.host)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._controller.name,
            manufacturer="Tylo",
            model="Elite",
        )

    async def async_added_to_hass(self) -> None:
        self._controller.register_callback(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self._controller.unregister_callback(self.async_write_ha_state)


class TyloSaunaTimeToOff(_BaseTyloSensor):
    """Remaining time until auto-off (minutes)."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "min"

    def __init__(self, controller) -> None:
        super().__init__(controller)
        self._attr_name = f"{controller.name} time to off"
        self._attr_unique_id = f"tylo_sauna_{self._device_id}_time_to_off"

    @property
    def available(self) -> bool:
        # This entity represents telemetry data — when offline, it should be unavailable.
        return bool(self._controller.is_online())

    @property
    def native_value(self) -> int | None:
        if self._controller.stop_rem_min is None:
            return None
        return int(self._controller.stop_rem_min)


class TyloSaunaFaultCode(_BaseTyloSensor):
    """Last fault code (door cancel etc.)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, controller) -> None:
        super().__init__(controller)
        self._attr_name = f"{controller.name} fault code"
        self._attr_unique_id = f"tylo_sauna_{self._device_id}_fault_code"

    @property
    def available(self) -> bool:
        # Diagnostics should always be available.
        return True

    @property
    def native_value(self) -> int | None:
        fault = getattr(self._controller, "last_fault", None)
        return int(fault.code) if fault else None


class TyloSaunaFaultMessage(_BaseTyloSensor):
    """Last fault message."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, controller) -> None:
        super().__init__(controller)
        self._attr_name = f"{controller.name} fault message"
        self._attr_unique_id = f"tylo_sauna_{self._device_id}_fault_message"

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str | None:
        fault = getattr(self._controller, "last_fault", None)
        return str(fault.message) if fault and fault.message else None
