"""Switch entities for Termogea global controls."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_COORDINATOR,
    DATA_STORAGE,
    DOMAIN,
    SERVICE_APPLY_ALL_ZONE_POLICIES,
)
from .entity import controller_device_info


async def async_setup_entry(
    hass,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Termogea switch entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    storage = hass.data[DOMAIN][entry.entry_id][DATA_STORAGE]

    async_add_entities(
        [
            TermogeaGlobalPowerSwitch(
                coordinator,
                storage,
            )
        ]
    )


class TermogeaGlobalPowerSwitch(CoordinatorEntity, SwitchEntity):
    """Master switch to enable/disable all Termogea automations and zones."""

    _attr_icon = "mdi:power"

    def __init__(self, coordinator, storage) -> None:
        super().__init__(coordinator)
        self._storage = storage
        self._attr_name = "Termogea Power"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_global_power"

    @property
    def device_info(self) -> DeviceInfo:
        return controller_device_info(self.coordinator.config_entry)

    @property
    def is_on(self) -> bool:
        return bool(self._storage.config.global_config.global_enabled)

    async def _async_set_power(self, enabled: bool) -> None:
        config = self._storage.config.global_config
        if config.global_enabled == enabled:
            return

        config.global_enabled = enabled
        await self._storage.async_update_global_config(config)

        if self.hass.services.has_service(DOMAIN, SERVICE_APPLY_ALL_ZONE_POLICIES):
            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_APPLY_ALL_ZONE_POLICIES,
                {},
                blocking=True,
            )
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on all zones according to current policy."""
        await self._async_set_power(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off all zones."""
        await self._async_set_power(False)
