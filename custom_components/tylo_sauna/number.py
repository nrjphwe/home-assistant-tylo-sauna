import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        _LOGGER.error("Tylo Sauna number: controller not found for entry %s", entry.entry_id)
        return

    controller = data["controller"]
    async_add_entities([TyloSaunaStopTime(controller)])
    _LOGGER.info("Tylo Sauna stop time number entity added")


class TyloSaunaStopTime(NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 600
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = "min"

    def __init__(self, controller) -> None:
        self._controller = controller
        self._device_id = getattr(controller, "device_id", controller.host)
        self._attr_name = f"{controller.name} stop time"
        self._attr_unique_id = f"tylo_sauna_{self._device_id}_stop_time"

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
        return bool(self._controller.is_online())

    async def async_added_to_hass(self) -> None:
        self._controller.register_callback(self.async_write_ha_state)

    @property
    def native_value(self) -> int | None:
        if self._controller.stop_cfg_min is None:
            return None
        return int(self._controller.stop_cfg_min)

    async def async_set_native_value(self, value: float) -> None:
        mins = int(round(value))
        await self._controller.async_set_stop_after(mins)
