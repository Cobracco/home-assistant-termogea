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
)
from .models import GlobalConfig, PolicyDecision, ZoneDefinition


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


def resolve_active_mode(settings: GlobalConfig) -> str:
    """Resolve the effective active mode including schedule."""
    mode = settings.global_mode.lower()
    if mode != GLOBAL_MODE_AUTO:
        return mode

    if not settings.schedule_enabled:
        return settings.auto_fallback_mode

    now = dt_util.now()
    weekday = now.strftime("%a").lower()[:3]
    current = now.time()

    for rule in settings.schedule_rules:
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
    active_mode = resolve_active_mode(settings)

    if not zone.enabled:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="zone_disabled",
            effective_target=zone.inactive_temp,
            active_mode=active_mode,
        )

    if not settings.global_enabled or not settings.automations_enabled:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="global_disabled",
            effective_target=zone.inactive_temp,
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
            effective_target=zone.away_temp,
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_OFF:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="global_off",
            effective_target=zone.inactive_temp,
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_AWAY:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="global_away",
            effective_target=zone.away_temp,
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_COMFORT:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=True,
            policy_reason="global_comfort",
            effective_target=zone.comfort_temp,
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_NIGHT:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=True,
            policy_reason="global_night",
            effective_target=zone.night_temp,
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_ECO:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=True,
            policy_reason="global_eco",
            effective_target=zone.eco_temp,
            active_mode=active_mode,
        )

    if presence_detected:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=True,
            policy_reason="presence_comfort",
            effective_target=zone.comfort_temp,
            active_mode=active_mode,
        )

    return PolicyDecision(
        assigned_people_present=assigned_people_present,
        presence_detected=presence_detected,
        zone_enabled=True,
        policy_reason="eligible_without_local_presence",
        effective_target=zone.eco_temp,
        active_mode=active_mode,
    )
