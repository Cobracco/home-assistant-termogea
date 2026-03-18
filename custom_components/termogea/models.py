"""Data models for the Termogea integration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class RegisterDefinition:
    """Definition of a Termogea register."""

    mod: int
    reg: int
    scale: float = 1.0
    precision: int = 1
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    off_value: int | None = None
    heat_value: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize the register definition."""
        return asdict(self)


@dataclass(slots=True)
class ScheduleRule:
    """Weekly schedule rule."""

    rule_id: str
    name: str
    days: list[str]
    start: str
    end: str
    mode: str

    def as_dict(self) -> dict[str, Any]:
        """Serialize the rule."""
        return asdict(self)


@dataclass(slots=True)
class GlobalConfig:
    """Persistent global configuration."""

    global_enabled: bool = True
    automations_enabled: bool = True
    allow_common_without_people: bool = False
    season_mode: str = "auto"
    global_mode: str = "auto"
    auto_fallback_mode: str = "eco"
    comfort_temp: float = 21.0
    eco_temp: float = 18.5
    away_temp: float = 16.0
    night_temp: float = 18.0
    inactive_temp: float = 16.0
    winter_comfort_temp: float = 21.0
    winter_eco_temp: float = 18.5
    winter_away_temp: float = 16.0
    winter_night_temp: float = 18.0
    winter_inactive_temp: float = 16.0
    summer_comfort_temp: float = 21.0
    summer_eco_temp: float = 18.5
    summer_away_temp: float = 16.0
    summer_night_temp: float = 18.0
    summer_inactive_temp: float = 16.0
    schedule_enabled: bool = True
    schedule_rules: list[ScheduleRule] = field(default_factory=list)
    schedule_rules_winter: list[ScheduleRule] = field(default_factory=list)
    schedule_rules_summer: list[ScheduleRule] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialize the global config."""
        data = asdict(self)
        data["schedule_rules"] = [rule.as_dict() for rule in self.schedule_rules]
        data["schedule_rules_winter"] = [
            rule.as_dict() for rule in self.schedule_rules_winter
        ]
        data["schedule_rules_summer"] = [
            rule.as_dict() for rule in self.schedule_rules_summer
        ]
        return data


@dataclass(slots=True)
class ZoneDefinition:
    """Persistent Termogea zone configuration."""

    zone_id: str
    name: str
    current_temperature: RegisterDefinition | None = None
    current_humidity: RegisterDefinition | None = None
    target_temperature: RegisterDefinition | None = None
    hvac_mode: RegisterDefinition | None = None
    status_register: RegisterDefinition | None = None
    people: list[str] = field(default_factory=list)
    presence_sensor: str | None = None
    is_common_area: bool = False
    enabled: bool = True
    manual_override_allowed: bool = True
    manual_override_temp: float | None = None
    manual_override_until: str | None = None
    custom_setpoints: bool = False
    custom_schedule: bool = False
    schedule_enabled: bool = True
    schedule_rules: list[ScheduleRule] = field(default_factory=list)
    schedule_rules_winter: list[ScheduleRule] = field(default_factory=list)
    schedule_rules_summer: list[ScheduleRule] = field(default_factory=list)
    comfort_temp: float = 21.0
    eco_temp: float = 18.5
    away_temp: float = 16.0
    night_temp: float = 18.0
    inactive_temp: float = 16.0

    @property
    def mapping_complete(self) -> bool:
        """Return True when the zone has enough technical mapping for climate control."""
        return self.target_temperature is not None

    def as_dict(self) -> dict[str, Any]:
        """Serialize the zone definition."""
        return {
            "zone_id": self.zone_id,
            "name": self.name,
            "current_temperature": (
                self.current_temperature.as_dict()
                if self.current_temperature is not None
                else None
            ),
            "current_humidity": (
                self.current_humidity.as_dict()
                if self.current_humidity is not None
                else None
            ),
            "target_temperature": (
                self.target_temperature.as_dict()
                if self.target_temperature is not None
                else None
            ),
            "hvac_mode": (
                self.hvac_mode.as_dict() if self.hvac_mode is not None else None
            ),
            "status_register": (
                self.status_register.as_dict()
                if self.status_register is not None
                else None
            ),
            "people": list(self.people),
            "presence_sensor": self.presence_sensor,
            "is_common_area": self.is_common_area,
            "enabled": self.enabled,
            "manual_override_allowed": self.manual_override_allowed,
            "manual_override_temp": self.manual_override_temp,
            "manual_override_until": self.manual_override_until,
            "custom_setpoints": self.custom_setpoints,
            "custom_schedule": self.custom_schedule,
            "schedule_enabled": self.schedule_enabled,
            "schedule_rules": [rule.as_dict() for rule in self.schedule_rules],
            "schedule_rules_winter": [rule.as_dict() for rule in self.schedule_rules_winter],
            "schedule_rules_summer": [rule.as_dict() for rule in self.schedule_rules_summer],
            "comfort_temp": self.comfort_temp,
            "eco_temp": self.eco_temp,
            "away_temp": self.away_temp,
            "night_temp": self.night_temp,
            "inactive_temp": self.inactive_temp,
        }


@dataclass(slots=True)
class RuntimeConfig:
    """Full persistent runtime configuration."""

    global_config: GlobalConfig
    zones: list[ZoneDefinition] = field(default_factory=list)


@dataclass(slots=True)
class ZoneSnapshot:
    """Latest state for a zone."""

    current_temperature: float | None
    current_humidity: float | None
    target_temperature: float | None
    hvac_mode: str | None
    status_value: int | None = None
    raw_values: dict[str, int | None] = field(default_factory=dict)


@dataclass(slots=True)
class PolicyDecision:
    """Policy decision for a zone."""

    assigned_people_present: bool
    presence_detected: bool
    zone_enabled: bool
    policy_reason: str
    effective_target: float | None
    active_mode: str
