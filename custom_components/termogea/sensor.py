"""Sensors for Termogea policy details."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DATA_STORAGE, DOMAIN
from .entity import controller_device_info, zone_device_info
from .policy import evaluate_zone_policy, resolve_active_mode


async def async_setup_entry(
    hass,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Termogea policy sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    storage = hass.data[DOMAIN][entry.entry_id][DATA_STORAGE]

    entities: list[SensorEntity] = [
        TermogeaGlobalSensor(
            coordinator,
            storage,
            key="active_mode",
            name="Termogea Active Mode",
            unique_suffix="active_mode",
        ),
        TermogeaGlobalSensor(
            coordinator,
            storage,
            key="configured_zones",
            name="Termogea Configured Zones",
            unique_suffix="configured_zones",
        ),
    ]
    for zone in storage.config.zones:
        entities.append(
            TermogeaPolicyTextSensor(
                coordinator,
                storage,
                zone.zone_id,
                sensor_key="policy_reason",
                name_suffix="Policy Reason",
                unique_suffix="policy_reason",
            )
        )
        entities.append(
            TermogeaPolicyNumericSensor(
                coordinator,
                storage,
                zone.zone_id,
                name_suffix="Effective Target",
                unique_suffix="effective_target",
            )
        )

    async_add_entities(entities)


class _PolicyBaseEntity(CoordinatorEntity):
    def __init__(self, coordinator, storage, zone_id: str) -> None:
        super().__init__(coordinator)
        self._storage = storage
        self._zone_id = zone_id

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
    def device_info(self) -> DeviceInfo:
        zone = self._storage.get_zone(self._zone_id)
        return zone_device_info(self.coordinator.config_entry, zone)


class TermogeaPolicyTextSensor(_PolicyBaseEntity, SensorEntity):
    """Text sensor for the current zone policy reason."""

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
        super().__init__(coordinator, storage, zone_id)
        zone = storage.get_zone(zone_id)
        self._sensor_key = sensor_key
        self._attr_name = f"{zone.name} {name_suffix}"
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{zone_id}_{unique_suffix}"
        )

    @property
    def native_value(self) -> str:
        zone = self._storage.get_zone(self._zone_id)
        decision = evaluate_zone_policy(
            self.hass,
            zone,
            self._storage.config.zones,
            self._storage.config.global_config,
        )
        return str(getattr(decision, self._sensor_key))


class TermogeaPolicyNumericSensor(_PolicyBaseEntity, SensorEntity):
    """Numeric sensor for the effective target."""

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator,
        storage,
        zone_id: str,
        *,
        name_suffix: str,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator, storage, zone_id)
        zone = storage.get_zone(zone_id)
        self._attr_name = f"{zone.name} {name_suffix}"
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{zone_id}_{unique_suffix}"
        )

    @property
    def native_value(self) -> float | None:
        zone = self._storage.get_zone(self._zone_id)
        decision = evaluate_zone_policy(
            self.hass,
            zone,
            self._storage.config.zones,
            self._storage.config.global_config,
        )
        return decision.effective_target


class TermogeaGlobalSensor(CoordinatorEntity, SensorEntity):
    """Global sensor for persistent config state."""

    def __init__(self, coordinator, storage, *, key: str, name: str, unique_suffix: str) -> None:
        super().__init__(coordinator)
        self._storage = storage
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{unique_suffix}"

    @property
    def native_value(self):
        if self._key == "active_mode":
            return resolve_active_mode(self._storage.config.global_config)
        if self._key == "configured_zones":
            return len(self._storage.config.zones)
        return None

    @property
    def device_info(self) -> DeviceInfo:
        return controller_device_info(self.coordinator.config_entry)
