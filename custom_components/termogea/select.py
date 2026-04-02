"""Select entities for Termogea global controls."""

from __future__ import annotations

from dataclasses import replace as dataclass_replace

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_COORDINATOR,
    DATA_STORAGE,
    DOMAIN,
    SEASON_MODES,
    SERVICE_APPLY_ALL_ZONE_POLICIES,
)
from .entity import controller_device_info


async def async_setup_entry(
    hass,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Termogea select entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    storage = hass.data[DOMAIN][entry.entry_id][DATA_STORAGE]

    async_add_entities([TermogeaSeasonSelect(coordinator, storage)])


class TermogeaSeasonSelect(CoordinatorEntity, SelectEntity):
    """Select entity to control the active season mode."""

    _attr_icon = "mdi:weather-partly-cloudy"
    _attr_options = SEASON_MODES

    def __init__(self, coordinator, storage) -> None:
        super().__init__(coordinator)
        self._storage = storage
        self._attr_name = "Termogea Season Mode"
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_season_mode"

    @property
    def device_info(self) -> DeviceInfo:
        return controller_device_info(self.coordinator.config_entry)

    @property
    def current_option(self) -> str:
        return self._storage.config.global_config.season_mode

    async def async_select_option(self, option: str) -> None:
        """Change the season mode."""
        config = self._storage.config.global_config
        if config.season_mode == option:
            return

        updated = dataclass_replace(config, season_mode=option)
        await self._storage.async_update_global_config(updated, previous_global_config=config)

        if self.hass.services.has_service(DOMAIN, SERVICE_APPLY_ALL_ZONE_POLICIES):
            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_APPLY_ALL_ZONE_POLICIES,
                {},
                blocking=True,
            )
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()
