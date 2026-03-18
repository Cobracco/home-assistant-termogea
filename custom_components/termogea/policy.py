"""Policy evaluation for Termogea zones."""

from __future__ import annotations

from datetime import time

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    GLOBAL_MODE_AUTO,
    GLOBAL_MODE_AWAY,
    GLOBAL_MODE_COMFORT,
    GLOBAL_MODE_ECO,
    GLOBAL_MODE_NIGHT,
    GLOBAL_MODE_OFF,
    SEASON_MODE_SUMMER,
    SEASON_MODE_WINTER,
)
from .models import GlobalConfig, PolicyDecision, ZoneDefinition, ZoneSnapshot


def _state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return None if state is None else state.state


def _is_on(hass: HomeAssistant, entity_id: str) -> bool:
    return (_state(hass, entity_id) or "").lower() in {
        "on",
        "home",
        "true",
        "occupied",
        "detected",
    }


def _house_people_present(hass: HomeAssistant, zones: list[ZoneDefinition]) -> bool:
    people = {person for zone in zones for person in zone.people}
    return any(_is_on(hass, person) for person in people)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def resolve_active_season(settings: GlobalConfig) -> str:
    """Resolve the active season (winter/summer)."""
    configured = (settings.season_mode or "").lower()
    if configured in {SEASON_MODE_WINTER, SEASON_MODE_SUMMER}:
        return configured

    month = dt_util.now().month
    return SEASON_MODE_SUMMER if 4 <= month <= 9 else SEASON_MODE_WINTER


def _global_schedule_rules_for_season(settings: GlobalConfig, season: str):
    if season == SEASON_MODE_SUMMER:
        rules = settings.schedule_rules_summer
    else:
        rules = settings.schedule_rules_winter
    if rules:
        return rules
    return settings.schedule_rules


def _zone_schedule_rules_for_season(zone: ZoneDefinition, season: str):
    if season == SEASON_MODE_SUMMER:
        rules = zone.schedule_rules_summer
    else:
        rules = zone.schedule_rules_winter
    if rules:
        return rules
    return zone.schedule_rules


def _season_mode_value(settings: GlobalConfig, season: str, mode: str) -> float:
    if mode == GLOBAL_MODE_COMFORT:
        return (
            settings.summer_comfort_temp
            if season == SEASON_MODE_SUMMER
            else settings.winter_comfort_temp
        )
    if mode == GLOBAL_MODE_ECO:
        return (
            settings.summer_eco_temp
            if season == SEASON_MODE_SUMMER
            else settings.winter_eco_temp
        )
    if mode == GLOBAL_MODE_AWAY:
        return (
            settings.summer_away_temp
            if season == SEASON_MODE_SUMMER
            else settings.winter_away_temp
        )
    if mode == GLOBAL_MODE_NIGHT:
        return (
            settings.summer_night_temp
            if season == SEASON_MODE_SUMMER
            else settings.winter_night_temp
        )
    return (
        settings.summer_inactive_temp
        if season == SEASON_MODE_SUMMER
        else settings.winter_inactive_temp
    )


def _zone_mode_value(zone: ZoneDefinition, mode: str) -> float:
    if mode == GLOBAL_MODE_COMFORT:
        return zone.comfort_temp
    if mode == GLOBAL_MODE_ECO:
        return zone.eco_temp
    if mode == GLOBAL_MODE_AWAY:
        return zone.away_temp
    if mode == GLOBAL_MODE_NIGHT:
        return zone.night_temp
    return zone.inactive_temp


def _seasonal_zone_target(zone: ZoneDefinition, settings: GlobalConfig, season: str, mode: str) -> float:
    """Resolve effective target for one zone and one mode."""
    if not zone.custom_setpoints:
        return _season_mode_value(settings, season, mode)
    # Custom zone temperatures are absolute values and must not be shifted by
    # legacy/global deltas, otherwise runtime setpoint changes "bounce back".
    return _zone_mode_value(zone, mode)


def _active_manual_override_target(zone: ZoneDefinition) -> float | None:
    """Return manual override target when active, otherwise None."""
    if not zone.manual_override_allowed:
        return None
    if zone.manual_override_temp is None or not zone.manual_override_until:
        return None

    until = dt_util.parse_datetime(zone.manual_override_until)
    if until is None:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=dt_util.UTC)
    if dt_util.utcnow() >= dt_util.as_utc(until):
        return None
    return float(zone.manual_override_temp)


def resolve_active_mode(settings: GlobalConfig, zone: ZoneDefinition | None = None) -> str:
    """Resolve the effective active mode including schedule."""
    mode = settings.global_mode.lower()
    if mode != GLOBAL_MODE_AUTO:
        return mode

    schedule_enabled = settings.schedule_enabled
    schedule_rules = _global_schedule_rules_for_season(settings, resolve_active_season(settings))
    if zone is not None and zone.custom_schedule:
        schedule_enabled = zone.schedule_enabled
        zone_rules = _zone_schedule_rules_for_season(zone, resolve_active_season(settings))
        schedule_rules = zone_rules or schedule_rules

    if not schedule_enabled:
        return settings.auto_fallback_mode

    now = dt_util.now()
    weekday = now.strftime("%a").lower()[:3]
    current = now.time()
    active_season = resolve_active_season(settings)
    if zone is not None and zone.custom_schedule:
        zone_rules = _zone_schedule_rules_for_season(zone, active_season)
        schedule_rules = zone_rules or _global_schedule_rules_for_season(settings, active_season)
    else:
        schedule_rules = _global_schedule_rules_for_season(settings, active_season)

    for rule in schedule_rules:
        if weekday not in rule.days:
            continue
        start = _parse_hhmm(rule.start)
        end = _parse_hhmm(rule.end)
        if start <= end:
            if start <= current <= end:
                return rule.mode
        else:
            if current >= start or current <= end:
                return rule.mode

    return settings.auto_fallback_mode


def evaluate_zone_policy(
    hass: HomeAssistant,
    zone: ZoneDefinition,
    zones: list[ZoneDefinition],
    settings: GlobalConfig,
) -> PolicyDecision:
    """Compute the current policy decision for a zone."""

    assigned_people_present = any(_is_on(hass, person) for person in zone.people)
    presence_detected = bool(zone.presence_sensor and _is_on(hass, zone.presence_sensor))
    house_people_present = _house_people_present(hass, zones)
    active_season = resolve_active_season(settings)
    active_mode = resolve_active_mode(settings, zone)

    if not zone.enabled:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="zone_disabled",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_OFF),
            active_mode=active_mode,
        )

    if not settings.global_enabled or not settings.automations_enabled:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="global_disabled",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_OFF),
            active_mode=active_mode,
        )

    # Hard gate requested for v1 UX: when nobody is home every zone goes to the
    # same conservation temperature, regardless of per-zone assignment.
    if not house_people_present:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="home_empty_conservation",
            effective_target=_season_mode_value(settings, active_season, GLOBAL_MODE_OFF),
            active_mode=active_mode,
        )

    if zone.is_common_area:
        people_gate = house_people_present or assigned_people_present
        eligible = people_gate or (settings.allow_common_without_people and presence_detected)
    else:
        eligible = assigned_people_present

    if not eligible:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="no_people_assigned_home",
            effective_target=_season_mode_value(settings, active_season, GLOBAL_MODE_OFF),
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_OFF:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="global_off",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_OFF),
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_AWAY:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="global_away",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_AWAY),
            active_mode=active_mode,
        )

    manual_override_target = _active_manual_override_target(zone)
    if manual_override_target is not None:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=True,
            policy_reason="manual_override",
            effective_target=manual_override_target,
            active_mode=active_mode,
        )

    # Local presence has operational priority: if a zone is eligible and its
    # room sensor is active, force comfort target regardless of eco/night mode.
    if presence_detected:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=True,
            policy_reason="presence_comfort",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_COMFORT),
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_COMFORT:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=True,
            policy_reason="global_comfort",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_COMFORT),
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_NIGHT:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=True,
            policy_reason="global_night",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_NIGHT),
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_ECO:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=True,
            policy_reason="global_eco",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_ECO),
            active_mode=active_mode,
        )

    return PolicyDecision(
        assigned_people_present=assigned_people_present,
        presence_detected=presence_detected,
        zone_enabled=True,
        policy_reason="eligible_without_local_presence",
        effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_ECO),
        active_mode=active_mode,
    )


def is_zone_heating_active(
    snapshot: ZoneSnapshot | None,
    decision: PolicyDecision,
    *,
    delta_celsius: float = 0.1,
) -> bool:
    """Return True when the zone is actively demanding conditioning."""
    if snapshot is None:
        return False
    if not decision.zone_enabled:
        return False
    if snapshot.hvac_mode == "off":
        return False

    if snapshot.status_value is not None:
        # Server-provided StatusBits: bit0 represents active zone request.
        return bool(snapshot.status_value & 0x0001)

    current = snapshot.current_temperature
    target = snapshot.target_temperature
    if current is None:
        return False
    if target is None:
        target = decision.effective_target
    if target is None:
        return False
    return current < (target - delta_celsius)
