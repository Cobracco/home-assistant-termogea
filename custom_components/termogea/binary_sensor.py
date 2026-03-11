"""Binary sensors for Termogea policy state."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DATA_STORAGE, DOMAIN
from .policy import evaluate_zone_policy


async def async_setup_entry(
    hass,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up policy binary sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    storage = hass.data[DOMAIN][entry.entry_id][DATA_STORAGE]

    entities: list[BinarySensorEntity] = []
    for zone in storage.config.zones:
        entities.append(
            TermogeaZoneBinarySensor(
                coordinator,
                storage,
                zone.zone_id,
                sensor_key="assigned_people_present",
                name_suffix="Assigned People Present",
                unique_suffix="assigned_people_present",
            )
        )
        entities.append(
            TermogeaZoneBinarySensor(
                coordinator,
                storage,
                zone.zone_id,
                sensor_key="presence_detected",
                name_suffix="Presence Detected",
                unique_suffix="presence_detected",
            )
        )
        entities.append(
            TermogeaZoneBinarySensor(
                coordinator,
                storage,
                zone.zone_id,
                sensor_key="zone_enabled",
                name_suffix="Zone Enabled",
                unique_suffix="zone_enabled",
            )
        )

    async_add_entities(entities)


class TermogeaZoneBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Policy-related binary sensor for a zone."""

    def __init__(
        self,
        coordinator,
        storage,
        zone_id: str,
        *,
        sensor_key: str,
        name_suffix: str,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._storage = storage
        self._zone_id = zone_id
        self._sensor_key = sensor_key
        self._attr_name = f"{storage.get_zone(zone_id).name} {name_suffix}"
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{zone_id}_{unique_suffix}"
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        zone = self._storage.get_zone(self._zone_id)
        tracked = list(zone.people)
        if zone.presence_sensor:
            tracked.append(zone.presence_sensor)
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                tracked,
                self._async_handle_state_change,
            )
        )

    @callback
    def _async_handle_state_change(self, _event) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        zone = self._storage.get_zone(self._zone_id)
        decision = evaluate_zone_policy(
            self.hass,
            zone,
            self._storage.config.zones,
            self._storage.config.global_config,
        )
        return bool(getattr(decision, self._sensor_key))
