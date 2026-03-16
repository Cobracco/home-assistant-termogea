"""Climate entities for Termogea."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ACTIVE_MODE,
    ATTR_ASSIGNED_PEOPLE,
    ATTR_ASSIGNED_PEOPLE_PRESENT,
    ATTR_CUSTOM_SETPOINTS,
    ATTR_EFFECTIVE_TARGET,
    ATTR_ENABLED,
    ATTR_HEATING_ACTIVE,
    ATTR_IS_COMMON_AREA,
    ATTR_MANUAL_OVERRIDE_ALLOWED,
    ATTR_MAPPING_COMPLETE,
    ATTR_POLICY_REASON,
    ATTR_PRESENCE_DETECTED,
    ATTR_PRESENCE_SENSOR,
    ATTR_ZONE_ENABLED,
    ATTR_ZONE_STATUS_VALUE,
    ATTR_ZONE_ID,
    DATA_COORDINATOR,
    DATA_STORAGE,
    DOMAIN,
    SERVICE_APPLY_ZONE_POLICY,
)
from .entity import zone_device_info
from .models import ZoneDefinition
from .policy import evaluate_zone_policy, is_zone_heating_active


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
        self._manual_override_unsub = None
        self._attr_name = zone.name
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{zone.zone_id}"
        if zone.hvac_mode is not None:
            self._attr_supported_features |= ClimateEntityFeature.TURN_ON
            self._attr_supported_features |= ClimateEntityFeature.TURN_OFF
        else:
            self._attr_hvac_modes = [HVACMode.HEAT]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_restore_manual_override_timer()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_manual_override_timer()
        await super().async_will_remove_from_hass()

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
        snapshot = self.coordinator.data.get(self._zone_id)
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
            ATTR_HEATING_ACTIVE: is_zone_heating_active(
                snapshot,
                decision,
            ),
            ATTR_ZONE_STATUS_VALUE: None if snapshot is None else snapshot.status_value,
            ATTR_ACTIVE_MODE: decision.active_mode,
            ATTR_MAPPING_COMPLETE: zone.mapping_complete,
            ATTR_ENABLED: zone.enabled,
            ATTR_MANUAL_OVERRIDE_ALLOWED: zone.manual_override_allowed,
            ATTR_CUSTOM_SETPOINTS: zone.custom_setpoints,
        }

    @property
    def device_info(self) -> DeviceInfo:
        return zone_device_info(self.coordinator.config_entry, self._zone)

    @staticmethod
    def _manual_override_until_from_zone(zone: ZoneDefinition):
        if not zone.manual_override_until:
            return None
        until = dt_util.parse_datetime(zone.manual_override_until)
        if until is None:
            return None
        if until.tzinfo is None:
            until = until.replace(tzinfo=dt_util.UTC)
        return dt_util.as_utc(until)

    def _cancel_manual_override_timer(self) -> None:
        if self._manual_override_unsub is not None:
            self._manual_override_unsub()
            self._manual_override_unsub = None

    @callback
    def _async_manual_override_expired(self, _when) -> None:
        self._manual_override_unsub = None
        self.hass.async_create_task(self._async_clear_manual_override_and_reapply())

    def _schedule_manual_override_timer(self) -> None:
        self._cancel_manual_override_timer()
        zone = self._zone
        if zone.manual_override_temp is None:
            return
        until = self._manual_override_until_from_zone(zone)
        if until is None:
            return
        self._manual_override_unsub = async_track_point_in_utc_time(
            self.hass,
            self._async_manual_override_expired,
            until,
        )

    async def _async_restore_manual_override_timer(self) -> None:
        zone = self._zone
        if zone.manual_override_temp is None:
            return
        until = self._manual_override_until_from_zone(zone)
        if until is None or dt_util.utcnow() >= until:
            await self._async_clear_manual_override_and_reapply()
            return
        self._schedule_manual_override_timer()

    async def _async_clear_manual_override_and_reapply(self) -> None:
        zone = self._zone
        changed = False
        if zone.manual_override_temp is not None:
            zone.manual_override_temp = None
            changed = True
        if zone.manual_override_until is not None:
            zone.manual_override_until = None
            changed = True
        if not changed:
            return

        await self._storage.async_upsert_zone(zone)

        if self.hass.services.has_service(DOMAIN, SERVICE_APPLY_ZONE_POLICY):
            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_APPLY_ZONE_POLICY,
                {ATTR_ZONE_ID: zone.zone_id},
                blocking=False,
            )
        else:
            await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get("temperature")
        zone = self._zone
        if temperature is None or zone.target_temperature is None:
            return
        requested = float(temperature)
        await self.coordinator.client.async_write_scaled_register(
            zone.target_temperature,
            requested,
        )

        if zone.manual_override_allowed:
            until = dt_util.utcnow() + timedelta(hours=1)
            zone.manual_override_temp = requested
            zone.manual_override_until = until.isoformat()
            await self._storage.async_upsert_zone(zone)
            self._schedule_manual_override_timer()

        snapshot = self.coordinator.data.get(self._zone_id)
        if snapshot is not None:
            snapshot.target_temperature = requested
            # Push immediate UI update instead of waiting the next full poll.
            self.coordinator.async_set_updated_data(dict(self.coordinator.data))

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
