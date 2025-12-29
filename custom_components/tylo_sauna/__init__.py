import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant

from .const import (
    CONF_GUID,
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_RELAXED_TELEMETRY,
    DOMAIN,
    DEFAULT_CONTROL_PORT,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["climate", "light", "number", "sensor", "select", "binary_sensor"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Integration init (no YAML)."""
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry on options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Tylo Sauna config entry."""
    from .controller import SaunaController
    from .runtime_discovery import RuntimeDiscovery

    # Options override data (so OptionsFlow changes take effect)
    cfg = {**entry.data, **entry.options}

    host = cfg[CONF_HOST]
    port = int(cfg.get(CONF_PORT, DEFAULT_CONTROL_PORT))
    name = cfg.get(CONF_NAME, "Tylo Sauna")

    guid = cfg.get(CONF_GUID)  # may exist for discovery-based setup
    relaxed = bool(cfg.get(CONF_RELAXED_TELEMETRY, True))

    controller = SaunaController(
        hass=hass,
        host=host,
        port=port,
        name=name,
        guid=guid,
        relaxed_telemetry=relaxed,
    )

    # Stable id for device/entities (do NOT use host here, because host can change via OptionsFlow)
    controller.device_id = entry.entry_id
    controller.configured_host = host
    controller.configured_port = port

    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data[entry.entry_id] = {"controller": controller}

    # Ensure runtime discovery listener exists (shared across entries) to adapt to changing control ports.
    if "_runtime_discovery" not in domain_data:
        domain_data["_runtime_discovery"] = RuntimeDiscovery(hass)

    runtime_discovery: RuntimeDiscovery = domain_data["_runtime_discovery"]
    if not runtime_discovery.is_running:
        await runtime_discovery.async_start()
    runtime_discovery.register(controller)

    # Start UDP controller (HELLO/INIT) in the background
    hass.async_create_task(controller.async_start())
    _LOGGER.info("Tylo Sauna: controller scheduled for %s:%s", host, port)

    # Start keepalive:
    # - If HA already running (common), start immediately.
    # - Otherwise start once HA is started.
    async def _start_keepalive(_event=None):
        # small delay so UDP transport is more likely created
        await asyncio.sleep(0.2)
        try:
            controller.start_keepalive()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Tylo Sauna: failed to start keepalive: %s", exc)

    if hass.is_running:
        hass.async_create_task(_start_keepalive())
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start_keepalive)

    # Reload on options update
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Forward the entry to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    controller = data["controller"] if data else None

    if controller:
        try:
            await controller.async_stop()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Tylo Sauna: error stopping controller")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and DOMAIN in hass.data:
        domain_data = hass.data[DOMAIN]
        domain_data.pop(entry.entry_id, None)
        runtime_discovery = domain_data.get("_runtime_discovery")
        if runtime_discovery and controller:
            try:
                runtime_discovery.unregister(controller)
            except Exception:  # noqa: BLE001
                pass

        # Stop listener when no entries remain (only the shared key left).
        if runtime_discovery and len([k for k in domain_data.keys() if not str(k).startswith("_")]) == 0:
            try:
                await runtime_discovery.async_stop()
            except Exception:  # noqa: BLE001
                pass
            domain_data.pop("_runtime_discovery", None)

    return unload_ok
