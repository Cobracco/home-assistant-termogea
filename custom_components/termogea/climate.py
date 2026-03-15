"""Climate entities for Termogea."""

from __future__ import annotations

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_ACTIVE_MODE,
    ATTR_ASSIGNED_PEOPLE,
    ATTR_ASSIGNED_PEOPLE_PRESENT,
    ATTR_EFFECTIVE_TARGET,
    ATTR_ENABLED,
    ATTR_IS_COMMON_AREA,
    ATTR_CUSTOM_SETPOINTS,
    ATTR_MANUAL_OVERRIDE_ALLOWED,
    ATTR_MAPPING_COMPLETE,
    ATTR_POLICY_REASON,
    ATTR_PRESENCE_DETECTED,
    ATTR_PRESENCE_SENSOR,
    ATTR_ZONE_ENABLED,
    ATTR_ZONE_ID,
    DATA_COORDINATOR,
    DATA_STORAGE,
    DOMAIN,
)
from .entity import zone_device_info
from .models import ZoneDefinition
from .policy import evaluate_zone_policy


async def async_setup_entry(
    hass,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    storage = hass.data[DOMAIN][entry.entry_id][DATA_STORAGE]
    zones = [zone for zone in storage.config.zones if zone.mapping_complete]
    async_add_entities(TermogeaClimateEntity(coordinator, storage, zone) for zone in zones)


class TermogeaClimateEntity(CoordinatorEntity, ClimateEntity):
    """Representation of a Termogea zone."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(self, coordinator, storage, zone: ZoneDefinition) -> None:
        super().__init__(coordinator)
        self._storage = storage
        self._zone_id = zone.zone_id
        self._attr_name = zone.name
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{zone.zone_id}"
        if zone.hvac_mode is not None:
            self._attr_supported_features |= ClimateEntityFeature.TURN_ON
            self._attr_supported_features |= ClimateEntityFeature.TURN_OFF
        else:
            self._attr_hvac_modes = [HVACMode.HEAT]

    @property
    def _zone(self) -> ZoneDefinition:
        return self._storage.get_zone(self._zone_id)

    @property
    def current_temperature(self) -> float | None:
        snapshot = self.coordinator.data.get(self._zone_id)
        return None if snapshot is None else snapshot.current_temperature

    @property
    def current_humidity(self) -> float | None:
        snapshot = self.coordinator.data.get(self._zone_id)
        return None if snapshot is None else snapshot.current_humidity

    @property
    def target_temperature(self) -> float | None:
        snapshot = self.coordinator.data.get(self._zone_id)
        return None if snapshot is None else snapshot.target_temperature

    @property
    def min_temp(self) -> float:
        zone = self._zone
        assert zone.target_temperature is not None
        return zone.target_temperature.min_value or 10.0

    @property
    def max_temp(self) -> float:
        zone = self._zone
        assert zone.target_temperature is not None
        return zone.target_temperature.max_value or 30.0

    @property
    def target_temperature_step(self) -> float:
        zone = self._zone
        assert zone.target_temperature is not None
        return zone.target_temperature.step or 0.5

    @property
    def hvac_mode(self) -> HVACMode:
        snapshot = self.coordinator.data.get(self._zone_id)
        zone = self._zone
        if snapshot is None:
            return HVACMode.OFF if zone.hvac_mode else HVACMode.HEAT
        if snapshot.hvac_mode == "off":
            return HVACMode.OFF
        return HVACMode.HEAT

    @property
    def available(self) -> bool:
        return super().available and self._zone_id in self.coordinator.data

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        zone = self._zone
        decision = evaluate_zone_policy(
            self.hass,
            zone,
            self._storage.config.zones,
            self._storage.config.global_config,
        )
        return {
            ATTR_ZONE_ID: zone.zone_id,
            ATTR_ASSIGNED_PEOPLE: zone.people,
            ATTR_PRESENCE_SENSOR: zone.presence_sensor,
            ATTR_IS_COMMON_AREA: zone.is_common_area,
            ATTR_POLICY_REASON: decision.policy_reason,
            ATTR_EFFECTIVE_TARGET: decision.effective_target,
            ATTR_ASSIGNED_PEOPLE_PRESENT: decision.assigned_people_present,
            ATTR_PRESENCE_DETECTED: decision.presence_detected,
            ATTR_ZONE_ENABLED: decision.zone_enabled,
            ATTR_ACTIVE_MODE: decision.active_mode,
            ATTR_MAPPING_COMPLETE: zone.mapping_complete,
            ATTR_ENABLED: zone.enabled,
            ATTR_MANUAL_OVERRIDE_ALLOWED: zone.manual_override_allowed,
            ATTR_CUSTOM_SETPOINTS: zone.custom_setpoints,
        }

    @property
    def device_info(self) -> DeviceInfo:
        return zone_device_info(self.coordinator.config_entry, self._zone)

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get("temperature")
        zone = self._zone
        if temperature is None or zone.target_temperature is None:
            return
        await self.coordinator.client.async_write_scaled_register(
            zone.target_temperature,
            float(temperature),
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        zone = self._zone
        if zone.hvac_mode is None:
            return

        if hvac_mode == HVACMode.OFF and zone.hvac_mode.off_value is not None:
            await self.coordinator.client.async_write_register_value(
                zone.hvac_mode,
                zone.hvac_mode.off_value,
            )
        elif hvac_mode == HVACMode.HEAT and zone.hvac_mode.heat_value is not None:
            await self.coordinator.client.async_write_register_value(
                zone.hvac_mode,
                zone.hvac_mode.heat_value,
            )

        await self.coordinator.async_request_refresh()
