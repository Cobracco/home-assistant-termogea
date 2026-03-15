"""Persistent storage manager for Termogea."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .models import GlobalConfig, RuntimeConfig, ZoneDefinition
from .zone_map import ZoneMapError, load_zone_map, parse_runtime_config, serialize_runtime_config


class TermogeaStorageManager:
    """Manage persistent Termogea config in Home Assistant storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}",
        )
        self._config = RuntimeConfig(global_config=GlobalConfig(), zones=[])

    @property
    def config(self) -> RuntimeConfig:
        """Return the current runtime config."""
        return self._config

    async def async_load(self) -> RuntimeConfig:
        """Load the runtime config from storage."""
        raw = await self._store.async_load()
        self._config = parse_runtime_config(raw)
        return self._config

    async def async_save(self) -> None:
        """Persist the runtime config."""
        await self._store.async_save(serialize_runtime_config(self._config))

    async def async_initialize_from_yaml(self, path: str) -> bool:
        """Import the legacy YAML file only when storage is still empty."""
        raw = await self._store.async_load()
        if raw:
            self._config = parse_runtime_config(raw)
            return False

        zones = load_zone_map(self.hass, path)
        self._config = RuntimeConfig(global_config=GlobalConfig(), zones=zones)
        await self.async_save()
        return True

    async def async_import_yaml(self, path: str) -> None:
        """Replace zones with data imported from legacy YAML."""
        zones = load_zone_map(self.hass, path)
        self._config.zones = zones
        await self.async_save()

    async def async_update_global_config(
        self,
        global_config: GlobalConfig,
        *,
        previous_global_config: GlobalConfig | None = None,
    ) -> None:
        """Replace global config.

        If previous_global_config is provided, zones without custom setpoints
        are kept aligned to the new global values.
        """
        if previous_global_config is not None:
            for zone in self._config.zones:
                if zone.custom_setpoints:
                    continue
                zone.comfort_temp = global_config.comfort_temp
                zone.eco_temp = global_config.eco_temp
                zone.away_temp = global_config.away_temp
                zone.night_temp = global_config.night_temp
                zone.inactive_temp = global_config.inactive_temp
        self._config.global_config = global_config
        await self.async_save()

    async def async_upsert_zone(self, zone: ZoneDefinition) -> None:
        """Create or update a zone."""
        updated = False
        for index, current in enumerate(self._config.zones):
            if current.zone_id == zone.zone_id:
                self._config.zones[index] = zone
                updated = True
                break
        if not updated:
            self._config.zones.append(zone)
        await self.async_save()

    async def async_delete_zone(self, zone_id: str) -> None:
        """Delete a zone."""
        self._config.zones = [zone for zone in self._config.zones if zone.zone_id != zone_id]
        await self.async_save()

    def get_zone(self, zone_id: str) -> ZoneDefinition | None:
        """Return one zone by identifier."""
        for zone in self._config.zones:
            if zone.zone_id == zone_id:
                return zone
        return None

    def clone_zone(self, zone_id: str) -> ZoneDefinition | None:
        """Return a copy of a stored zone."""
        zone = self.get_zone(zone_id)
        if zone is None:
            return None
        return parse_runtime_config({"zones": [deepcopy(zone.as_dict())]}).zones[0]
