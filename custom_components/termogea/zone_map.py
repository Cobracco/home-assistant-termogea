"""Zone and runtime config parsing helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from homeassistant.core import HomeAssistant

from .models import GlobalConfig, RegisterDefinition, RuntimeConfig, ScheduleRule, ZoneDefinition


class ZoneMapError(ValueError):
    """Raised when the zone map is invalid."""


def _parse_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _parse_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _parse_register(data: dict | None, *, required: bool) -> RegisterDefinition | None:
    if not data:
        if required:
            raise ZoneMapError("Missing required register definition")
        return None

    try:
        mod = int(data["mod"])
        reg = int(data["reg"])
    except (KeyError, TypeError, ValueError) as err:
        raise ZoneMapError(f"Invalid register definition: {data}") from err

    return RegisterDefinition(
        mod=mod,
        reg=reg,
        scale=float(data.get("scale", 1.0)),
        precision=int(data.get("precision", 1)),
        min_value=_parse_optional_float(data.get("min_value", data.get("min"))),
        max_value=_parse_optional_float(data.get("max_value", data.get("max"))),
        step=_parse_optional_float(data.get("step")),
        off_value=_parse_optional_int(data.get("off_value")),
        heat_value=_parse_optional_int(data.get("heat_value")),
    )


def _parse_schedule_rule(data: dict) -> ScheduleRule:
    try:
        return ScheduleRule(
            rule_id=str(data["rule_id"]),
            name=str(data["name"]),
            days=[str(day).lower() for day in data["days"]],
            start=str(data["start"]),
            end=str(data["end"]),
            mode=str(data["mode"]).lower(),
        )
    except KeyError as err:
        raise ZoneMapError(f"Schedule rule missing field: {err}") from err


def _parse_schedule_rules(data: Any) -> list[ScheduleRule]:
    if not isinstance(data, list):
        return []
    return [_parse_schedule_rule(rule) for rule in data if isinstance(rule, dict)]


def _parse_zone(zone_data: dict) -> ZoneDefinition:
    try:
        zone_id = str(zone_data["zone_id"])
        name = str(zone_data["name"])
    except KeyError as err:
        raise ZoneMapError(f"Zone missing required field: {err}") from err

    presets = zone_data.get("presets", {})
    comfort = float(zone_data.get("comfort_temp", presets.get("comfort", 21.0)))
    eco = float(zone_data.get("eco_temp", presets.get("eco", 18.5)))
    away = float(zone_data.get("away_temp", presets.get("away", 16.0)))
    night = float(zone_data.get("night_temp", presets.get("night", 18.0)))
    inactive = float(zone_data.get("inactive_temp", zone_data.get("off_temp", away)))

    return ZoneDefinition(
        zone_id=zone_id,
        name=name,
        current_temperature=_parse_register(zone_data.get("current_temperature"), required=False),
        current_humidity=_parse_register(
            zone_data.get("current_humidity", zone_data.get("humidity")),
            required=False,
        ),
        target_temperature=_parse_register(zone_data.get("target_temperature"), required=False),
        hvac_mode=_parse_register(zone_data.get("hvac_mode"), required=False),
        status_register=_parse_register(zone_data.get("status_register"), required=False),
        people=[str(person) for person in zone_data.get("people", [])],
        presence_sensor=zone_data.get("presence_sensor"),
        is_common_area=bool(zone_data.get("is_common_area", False)),
        enabled=bool(zone_data.get("enabled", True)),
        manual_override_allowed=bool(zone_data.get("manual_override_allowed", True)),
        manual_override_temp=_parse_optional_float(zone_data.get("manual_override_temp")),
        manual_override_until=(
            str(zone_data.get("manual_override_until")).strip()
            if zone_data.get("manual_override_until") not in (None, "")
            else None
        ),
        custom_setpoints=bool(zone_data.get("custom_setpoints", False)),
        comfort_temp=comfort,
        eco_temp=eco,
        away_temp=away,
        night_temp=night,
        inactive_temp=inactive,
    )


def _parse_global_config(data: dict | None) -> GlobalConfig:
    data = data or {}
    legacy_schedule_rules = _parse_schedule_rules(data.get("schedule_rules", []))
    schedule_rules_winter = _parse_schedule_rules(
        data.get("schedule_rules_winter", data.get("schedule_rules", []))
    )
    schedule_rules_summer = _parse_schedule_rules(
        data.get("schedule_rules_summer", data.get("schedule_rules", []))
    )
    comfort_temp = float(data.get("comfort_temp", 21.0))
    eco_temp = float(data.get("eco_temp", 18.5))
    away_temp = float(data.get("away_temp", 16.0))
    night_temp = float(data.get("night_temp", 18.0))
    inactive_temp = float(data.get("inactive_temp", data.get("away_temp", 16.0)))
    return GlobalConfig(
        global_enabled=bool(data.get("global_enabled", True)),
        automations_enabled=bool(data.get("automations_enabled", True)),
        allow_common_without_people=bool(data.get("allow_common_without_people", False)),
        season_mode=str(data.get("season_mode", "auto")).lower(),
        global_mode=str(data.get("global_mode", "auto")).lower(),
        auto_fallback_mode=str(data.get("auto_fallback_mode", "eco")).lower(),
        comfort_temp=comfort_temp,
        eco_temp=eco_temp,
        away_temp=away_temp,
        night_temp=night_temp,
        inactive_temp=inactive_temp,
        winter_comfort_temp=float(data.get("winter_comfort_temp", comfort_temp)),
        winter_eco_temp=float(data.get("winter_eco_temp", eco_temp)),
        winter_away_temp=float(data.get("winter_away_temp", away_temp)),
        winter_night_temp=float(data.get("winter_night_temp", night_temp)),
        winter_inactive_temp=float(data.get("winter_inactive_temp", inactive_temp)),
        summer_comfort_temp=float(data.get("summer_comfort_temp", comfort_temp)),
        summer_eco_temp=float(data.get("summer_eco_temp", eco_temp)),
        summer_away_temp=float(data.get("summer_away_temp", away_temp)),
        summer_night_temp=float(data.get("summer_night_temp", night_temp)),
        summer_inactive_temp=float(data.get("summer_inactive_temp", inactive_temp)),
        schedule_enabled=bool(data.get("schedule_enabled", True)),
        schedule_rules=legacy_schedule_rules,
        schedule_rules_winter=schedule_rules_winter,
        schedule_rules_summer=schedule_rules_summer,
    )


def parse_runtime_config(data: dict[str, Any] | None) -> RuntimeConfig:
    """Parse runtime config from storage data."""
    data = data or {}
    zones = [_parse_zone(zone) for zone in data.get("zones", [])]
    seen: set[str] = set()
    for zone in zones:
        if zone.zone_id in seen:
            raise ZoneMapError(f"Duplicate zone_id: {zone.zone_id}")
        seen.add(zone.zone_id)
    return RuntimeConfig(
        global_config=_parse_global_config(data.get("global_config")),
        zones=zones,
    )


def serialize_runtime_config(config: RuntimeConfig) -> dict[str, Any]:
    """Serialize runtime config for storage."""
    return {
        "global_config": config.global_config.as_dict(),
        "zones": [zone.as_dict() for zone in config.zones],
    }


def load_zone_map(hass: HomeAssistant, path: str) -> list[ZoneDefinition]:
    """Load the legacy YAML zone map from the Home Assistant config directory."""

    resolved = Path(hass.config.path(path))
    if not resolved.exists():
        raise ZoneMapError(
            f"Zone map file not found: {resolved}. "
            "Create it from the provided example before enabling the integration."
        )

    with resolved.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    zones = data.get("zones")
    if not isinstance(zones, list) or not zones:
        raise ZoneMapError("Zone map must contain a non-empty 'zones' list")

    runtime = parse_runtime_config({"zones": zones})
    return runtime.zones
