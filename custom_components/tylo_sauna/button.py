import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        _LOGGER.error("Tylo Sauna button: controller not found for entry %s", entry.entry_id)
        return

    controller = data["controller"]
    async_add_entities([TyloSaunaAckFaultButton(controller)])
    _LOGGER.info("Tylo Sauna ack fault button added")


class TyloSaunaAckFaultButton(ButtonEntity):
    """Acknowledge the last fault popup (door cancel etc.)."""

    def __init__(self, controller) -> None:
        self._controller = controller
        self._attr_name = f"{controller.name} acknowledge fault"
        self._attr_unique_id = f"tylo_sauna_{controller.host}_ack_fault"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._controller.host)},
            name=self._controller.name,
            manufacturer="Tylo",
            model="Elite",
        )

    async def async_press(self) -> None:
        await self._controller.async_ack_last_fault()
