import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _opt(slot: int, name: str) -> str:
    return f"{slot}: {name}"


def _parse_slot(option: str) -> int:
    return int(option.split(":", 1)[0].strip())


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        _LOGGER.error("Tylo Sauna select: controller not found")
        return
    controller = data["controller"]
    async_add_entities([TyloSaunaFavoriteSelect(controller)])


class TyloSaunaFavoriteSelect(SelectEntity):
    def __init__(self, controller) -> None:
        self._controller = controller
        self._device_id = getattr(controller, "device_id", controller.host)
        self._attr_name = f"{controller.name} favorite"
        self._attr_unique_id = f"tylo_sauna_{self._device_id}_favorite"

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

    async def async_will_remove_from_hass(self) -> None:
        self._controller.unregister_callback(self.async_write_ha_state)

    @property
    def options(self) -> list[str]:
        favs = []
        for slot, fav in sorted(getattr(self._controller, "favorites", {}).items()):
            if getattr(fav, "enabled", False) and getattr(fav, "name", ""):
                favs.append(_opt(slot, fav.name))
        return favs

    async def async_select_option(self, option: str) -> None:
        slot = _parse_slot(option)
        await self._controller.async_apply_favorite(slot, start=False)

    @property
    def current_option(self):
        slot = getattr(self._controller, "last_selected_favorite_slot", None)
        if slot is None:
            return None
        fav = getattr(self._controller, "favorites", {}).get(int(slot))
        if not fav or not getattr(fav, "enabled", False):
            return None
        return _opt(int(slot), fav.name)
